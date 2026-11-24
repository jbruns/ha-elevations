"""The one module that talks to IoX (ADR 0013).

REST reads (`/rest/...`), SOAP calls (`POST /services`), and the event stream
(`/rest/subscribe`) all go through `IoxClient`, over HTTPS on port 8443 with a
local IoX user. `IoxFiles` reads IoX's config files over SSH, read-only, and
archives them as the IoX backup. The wire facts it relies on are in
`insteon-network/research/iox-api.md`.

The writes (`IoxClient.remove_device` and the rest) send the body elements and
parameters of the Controller's own schema (iox-api.md §3). Each one waits until
IoX is no longer busy, so only one Modem operation runs at a time. The one
REST write, `IoxClient.set_program_enabled`, enables or disables a program.
Only a command on the rails (`insteon.rails`) may call any of them.

IoX's XML comes from the Administrator's own Controller on the local network,
so it is parsed with the standard library.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import io
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from xml.sax.saxutils import escape

import aiohttp
import asyncssh

from insteon.address import InsteonAddress, ButtonAddress
from insteon.network import Button, Device, ForeignNode, Link, Modem, Network, Scene
from insteon.program import (
    Body,
    Command,
    Condition,
    NodeStatus,
    Program,
    SetVariable,
    Statement,
    Undecoded,
    Variable,
    VariableKind,
    VariableRef,
    Wait,
)

SERVICE = "urn:udi-com:service:X_Insteon_Lighting_Service:1"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"

# Node flag bits (PyISY's names): 0x04 a Scene, 0x08 the root Scene, 0x10 in error.
_FLAG_ROOT = 0x08
_FLAG_IN_ERROR = 0x10
# Link types in a Scene's members: 16 is a Controller Button. The REST wiki
# gives 32 for a Responder Button, but IoX 6.0.5 sends 0; any other type is a
# Responder Button.
_CONTROLLER = "16"
# `_2` record actions: 1 a Modem record (G_PLM_ALL), 2 a record read from a
# Device (G_DEV_ALL), 3 a record the Controller expects (G_ISY_ALL).
_MODEM_RECORD, _DEVICE_RECORD, _EXPECTED_RECORD = "1", "2", "3"
# Seconds to wait for one Device's Links. IoX itself gives up on a Device that
# does not answer after about 22 s; old Devices answer slowly but steadily.
DEVICE_READ_TIMEOUT = 120.0
# Seconds to wait for events still in flight after a streaming call returns.
_STREAM_GRACE = 5.0
# Seconds to wait for one write to settle. A Restore rewrites a whole table;
# the Modem's holds hundreds of Links, at about two a second.
WRITE_TIMEOUT = 300.0
MODEM_RESTORE_TIMEOUT = 1800.0
# A Replace rewrites the new Device's table and every Scene Peer's Links to the old one.
REPLACE_TIMEOUT = 1800.0
# A Battery Device leaves linking mode after about four minutes (iox-api.md §6).
BATTERY_WINDOW = 240.0
# MoveNode's LinkModeFlag: the Button joins the Scene as a Controller or a Responder.
_JOIN_AS_CONTROLLER, _JOIN_AS_RESPONDER = 16, 32
# AddGroup's NodeTypeFlag for a Scene.
_SCENE_TYPE = 4
# RestoreDeviceFromNode's flag: 0 restores everything, Scenes included.
_RESTORE_ALL = 0
# IoX's config files, which only root may read (iox-api.md §5).
FILES = "/var/isy/FILES"
CONF = f"{FILES}/CONF"
_HEX_ID = re.compile(r"[0-9A-Fa-f]{1,8}")
# The nodes a D2D file names, found even when the file won't parse.
_NODE_ATTRIBUTE = re.compile(r'\bnode="([^"]+)"')
# IoX families. A node with no family, or family 1, is Insteon.
_INSTEON_FAMILIES = {None, "0", "1"}
_FAMILY_NAMES = {
    "2": "UPB",
    "3": "RCS",
    "4": "Z-Wave",
    "5": "OpenADR",
    "7": "UDI",
    "8": "Brultech",
    "9": "NCD",
    "10": "Node Server",
    "12": "Z-Wave",
    "14": "Zigbee",
    "15": "Matter",
}


class IoxError(RuntimeError):
    """IoX refused a call or answered with something the tool cannot read."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        """The HTTP status IoX answered with, if that was the failure."""


class DeviceUnresponsive(IoxError):
    """A Device did not answer a read of its Links: an Unresponsive Device."""


@dataclass(frozen=True)
class Event:
    """One message from the event stream."""

    control: str
    action: str
    info: ET.Element
    node: str = ""
    """The node the event is about, as IoX writes it (`AA B C 1`), or empty."""


@dataclass(frozen=True)
class Write:
    """One write IoX has finished, and the events it sent meanwhile."""

    operation: str
    events: tuple[Event, ...]

    def pending(self, device: InsteonAddress) -> bool:
        """IoX still holds Pending Writes for the Device: its last `_3` WH or WD event was WH.

        A powered Device can get a WH after its write went through
        (research/iox-api.md §9), so for one this is not proof.
        """
        last = None
        for event in self._node_events():
            if event.action in ("WH", "WD") and _is_button_of(event.node, device):
                last = event.action
        return last == "WH"

    def _node_events(self) -> Iterable[Event]:
        return (event for event in self.events if event.control == "_3")


def _is_button_of(node: str, device: InsteonAddress) -> bool:
    button = ButtonAddress.try_parse(node)
    return button is not None and button.device == device


def soap_envelope(element: str, params: Mapping[str, object | None]) -> str:
    """A SOAP 1.1 envelope for one Insteon service call, params in the order given.

    `element` is the body element, which for a few operations differs from the
    bound operation name (iox-api.md §3). A None value is sent as an empty element.
    """
    body = "".join(
        f"<{name}>{escape('' if value is None else str(value))}</{name}>" for name, value in params.items()
    )
    return (
        f'<s:Envelope xmlns:s="{SOAP_NS}"><s:Body>'
        f'<u:{element} xmlns:u="{SERVICE}">{body}</u:{element}>'
        "</s:Body></s:Envelope>"
    )


class IoxClient:
    """A session with one Controller. Use as an async context manager."""

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,  # gitleaks:allow (an annotation, not a value)
        *,
        cert_sha256: str | None = None,
        timeout: float = 30,
    ) -> None:
        self._base = base_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._cert_sha256 = cert_sha256
        self._ssl: bool | aiohttp.Fingerprint = True
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> IoxClient:
        self._ssl = _ssl_setting(self._base, self._cert_sha256)
        self._session = aiohttp.ClientSession(headers=self._auth)
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("IoxClient must be used as an async context manager")
        return self._session

    # Transport ------------------------------------------------------------

    async def get(self, path: str) -> str:
        """GET `/rest/<path>` and return the body."""
        url = f"{self._base}/rest/{path.lstrip('/')}"
        try:
            async with self._http.get(url, ssl=self._ssl, timeout=self._timeout) as response:
                text = await response.text()
                _raise_for_status(response.status, f"GET /rest/{path}")
                return text
        except aiohttp.ClientError as error:
            raise _transport_error(f"GET /rest/{path}", error) from error

    async def soap(
        self,
        operation: str,
        params: Mapping[str, object | None],
        *,
        element: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """POST one SOAP call to `/services` and return the response body."""
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{SERVICE}#{operation}"',
        }
        envelope = soap_envelope(element or operation, params)
        limit = aiohttp.ClientTimeout(total=timeout) if timeout else self._timeout
        try:
            async with self._http.post(
                f"{self._base}/services", data=envelope, headers=headers, ssl=self._ssl, timeout=limit
            ) as response:
                text = await response.text()
                _raise_for_status(response.status, f"SOAP {operation}")
                return text
        except aiohttp.ClientError as error:
            raise _transport_error(f"SOAP {operation}", error) from error

    @contextlib.asynccontextmanager
    async def events(self) -> AsyncIterator[asyncio.Queue[Event]]:
        """Subscribe to the event stream; yields a queue of parsed events.

        IoX does not answer WebSocket pings, so the client sends none.
        """
        url = f"{self._base}/rest/subscribe"
        url = "ws" + url[len("http") :] if url.startswith("http") else url
        queue: asyncio.Queue[Event] = asyncio.Queue()
        try:
            ws = await self._http.ws_connect(
                url,
                protocols=("ISYSUB",),
                headers={"Origin": "com.universal-devices.websockets.isy"},
                heartbeat=None,
                autoping=False,
                ssl=self._ssl,
            )
        except aiohttp.WSServerHandshakeError as error:
            raise IoxError(f"event stream: HTTP {error.status}") from error
        except aiohttp.ClientError as error:
            raise _transport_error("event stream", error) from error

        async def pump() -> None:
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    break
                event = _parse_event(message.data)
                if event is not None:
                    queue.put_nowait(event)

        reader = asyncio.create_task(pump())
        try:
            yield queue
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            await ws.close()

    # Reads ----------------------------------------------------------------

    async def uuid(self) -> str:
        """The Controller's uuid, which prefixes every isy994 identifier in HA."""
        root = _xml(await self.get("config"), "/rest/config")
        uuid = root.findtext("root/id")
        if not uuid:
            raise IoxError("/rest/config has no root/id")
        return uuid.strip()

    async def network(self) -> Network:
        """Devices, Buttons, Scenes and non-Insteon nodes from `/rest/nodes`."""
        uuid = await self.uuid()
        return parse_nodes(await self.get("nodes"), uuid)

    async def modem(self) -> Modem:
        """The Modem's address and firmware (`DeviceSpecific G_PLM_INFO`).

        IoX answers this one with plain text such as `AA.BB.CC v9B / Connected`,
        not a SOAP envelope.
        """
        text = await self.soap(
            "DeviceSpecific", {"command": "G_PLM_INFO", "node": "", "option": "", "flag": 1, "CDATA": ""}
        )
        identity, _, status = text.strip().partition("/")
        address, _, firmware = identity.strip().partition(" ")
        found = InsteonAddress.try_parse(address)
        if found is None:
            raise IoxError(f"G_PLM_INFO answered {text.strip()[:80]!r}, not a Modem address")
        return Modem(found, firmware.strip(), status.strip())

    async def modem_links(self, modem: InsteonAddress, *, timeout: float = 900) -> list[Link]:
        """Read the Modem's Link table (`DeviceSpecific G_PLM_ALL`).

        IoX streams one `_2` event per record, about two a second, between busy
        (`_5` 1) and not busy (`_5` 0), and the SOAP call returns when the read
        is done. The call needs the Modem's own address as `node`; with an empty
        `node` IoX streams nothing.
        """
        try:
            return await self._read_links("G_PLM_ALL", modem.iox, _MODEM_RECORD, timeout=timeout)
        except TimeoutError as error:
            raise IoxError(f"the Modem Link table read took over {timeout:.0f} s") from error

    async def expected_links(self, device: ButtonAddress, *, timeout: float = 60) -> list[Link]:
        """The Links the Controller expects a Device to hold (`G_ISY_ALL`), from its cache.

        `device` is the Device's primary Button address (`Device.primary`). A
        record with flags 0 ends the table and is not returned.
        """
        try:
            return await self._read_links("G_ISY_ALL", device.iox, _EXPECTED_RECORD, timeout=timeout)
        except TimeoutError as error:
            raise IoxError(f"the expected Links of {device.device} took over {timeout:.0f} s") from error

    async def device_links(self, device: ButtonAddress, *, timeout: float = DEVICE_READ_TIMEOUT) -> list[Link]:
        """Read the Links a Device holds (`G_DEV_ALL`), over the network.

        `device` is the Device's primary Button address. A dimmer with 6 records takes
        about 6 s; old Devices take much longer. When the Device does not
        answer, IoX gives up after about 22 s with HTTP 501. Either that or
        no end within `timeout` seconds raises DeviceUnresponsive; on a timeout
        the read is stopped (`STOP`), so the Modem is free for the next one.
        """
        try:
            return await self._read_links("G_DEV_ALL", device.iox, _DEVICE_RECORD, timeout=timeout)
        except TimeoutError as error:
            with contextlib.suppress(IoxError):
                await self.soap(
                    "DeviceSpecific",
                    {"command": "STOP", "node": device.iox, "option": "", "flag": 1, "CDATA": ""},
                    timeout=10,
                )
            raise DeviceUnresponsive(f"{device.device} did not answer within {timeout:g} s") from error
        except IoxError as error:
            if error.status == 501:
                raise DeviceUnresponsive(f"{device.device} did not answer; IoX gave up (HTTP 501)") from error
            raise

    async def _read_links(self, command: str, node: str, action: str, *, timeout: float) -> list[Link]:
        """Run one `DeviceSpecific` Link-table read and collect its `_2` records.

        The read ends when the call has returned and either a record with flags
        0 (the end of a Device's table) or not busy (`_5` 0) has arrived, or a
        short grace after the call returns passes with no events. Raises
        TimeoutError after `timeout` seconds, and IoxError if the call fails.
        """
        records: list[Link] = []
        busy = ended = False
        getter: asyncio.Future[Event] | None = None
        async with self.events() as queue:
            call = asyncio.create_task(
                self.soap(
                    "DeviceSpecific",
                    {"command": command, "node": node, "option": "", "flag": 1, "CDATA": "0 -1"},
                    timeout=timeout,
                )
            )
            try:
                async with asyncio.timeout(timeout):
                    while not (ended and call.done()):
                        # Once the call has returned, wait only a short grace
                        # for events still in flight.
                        waiting = set() if call.done() else {call}
                        getter = asyncio.ensure_future(queue.get())
                        done, _ = await asyncio.wait(
                            {getter, *waiting},
                            timeout=None if waiting else _STREAM_GRACE,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if getter not in done:
                            getter.cancel()
                            if call.done():
                                call.result()  # raises if the call failed
                            if not waiting:
                                break
                            continue
                        event = getter.result()
                        if event.control == "_2" and event.action == action:
                            link = _link(event.info)
                            if link.flags == 0:
                                ended = True
                            else:
                                records.append(link)
                        elif event.control == "_5" and event.action == "1":
                            busy = True
                        elif event.control == "_5" and event.action == "0" and busy:
                            ended = True
                    call.result()
            finally:
                for task in (getter, call):
                    if task is not None and not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
        return records

    # Writes ---------------------------------------------------------------

    async def remove_from_scene(self, button: ButtonAddress, scene: str) -> Write:
        """Take a Button out of a Scene (`RemoveFromGroup`)."""
        return await self._write("RemoveFromGroup", {"node": button.iox, "group": scene})

    async def add_to_scene(self, button: ButtonAddress, scene: str, *, controller: bool) -> Write:
        """Put a Button in a Scene as a Controller or a Responder Button (`MoveNode`)."""
        flag = _JOIN_AS_CONTROLLER if controller else _JOIN_AS_RESPONDER
        return await self._write("MoveNode", {"node": button.iox, "group": scene, "flag": flag})

    async def create_scene(self, address: str, name: str) -> Write:
        """Create an empty Scene at a Scene address IoX does not use yet (`AddGroup`)."""
        return await self._write("AddGroup", {"id": address, "name": name, "flag": _SCENE_TYPE})

    async def delete_scene(self, address: str) -> Write:
        """Delete a Scene (`RemoveGroup`)."""
        return await self._write("RemoveGroup", {"id": address})

    async def set_scene_profile(self, responder: ButtonAddress, controller: str, control: str, value: int) -> Write:
        """Set what a Responder Button does for one controller of its Scene (`SetSceneProfile`).

        `controller` is the Scene address, for the Controller's own command, or
        a Controller Button's address. `control` is `OL` (on level, 0-255) or
        `RR` (ramp rate, an index into Insteon's table).
        """
        return await self._write(
            "SetSceneProfile", {"node": responder.iox, "controller": controller, "control": control, "action": value}
        )

    async def write_updates(self, device: ButtonAddress, *, until_clear: bool = False) -> Write:
        """Write a Device's Pending Writes to it now (`WriteDeviceUpdates`).

        With `until_clear`, as for a Battery Device, wait until they clear (see `_write`).
        """
        return await self._write(
            "WriteDeviceUpdates", {"node": device.iox}, until_clear=device.device if until_clear else None
        )

    async def remove_device(self, device: ButtonAddress) -> Write:
        """Remove a Device and all its Buttons from the Controller (`RemoveNode` on its primary Button)."""
        return await self._write("RemoveNode", {"id": device.iox})

    async def restore_device(self, device: ButtonAddress, *, until_clear: bool = False) -> Write:
        """Restore a Device's Links from the Controller's database (`RestoreDevice`).

        With `until_clear`, as for a Battery Device, wait until its Pending Writes clear (see `_write`).
        """
        return await self._write(
            "RestoreDevice",
            {"node": device.iox, "flag": _RESTORE_ALL},
            element="RestoreDeviceFromNode",
            until_clear=device.device if until_clear else None,
        )

    async def restore_modem(self, *, timeout: float = MODEM_RESTORE_TIMEOUT) -> Write:
        """Restore the Modem's Links from the Controller's database (`ReplaceModem`)."""
        return await self._write("ReplaceModem", {}, timeout=timeout)

    async def replace_device(
        self, old: ButtonAddress, new: ButtonAddress, firmware: str, *, until_clear: bool = False
    ) -> Write:
        """Replace one Device with another the Controller already has (`ReplaceDevice`).

        IoX moves the old Device's Scenes and Links onto the new one. `old` and
        `new` are their primary Buttons, and `firmware` is the new Device's
        type as `/rest/nodes` gives it (`1.32.65.0`). These are the live
        schema's names; eisy-ui sends `id`, `name` and `command` (iox-api.md
        §3). With `until_clear`, as for a new Battery Device, wait until its
        Pending Writes clear (see `_write`).
        """
        return await self._write(
            "ReplaceDevice",
            {"node": old.iox, "NewNode": new.iox, "firmware": firmware},
            timeout=REPLACE_TIMEOUT,
            until_clear=new.device if until_clear else None,
        )

    async def add_device(self, address: InsteonAddress, name: str, *, flag: int, until_clear: bool = False) -> Write:
        """Add a Device by its Insteon Address (`AddNode`).

        `flag` is IoX's NodeOperationsFlag: 1 erases the Device's Links, 3 adds
        the Devices it is linked to and builds Scenes from its Links. IoX reads
        the Device's type itself, so none is given.
        """
        return await self._write(
            "AddNode",
            {"id": address.iox, "name": name, "type": "", "flag": flag},
            until_clear=address if until_clear else None,
        )

    async def _write(
        self,
        operation: str,
        params: Mapping[str, object | None],
        *,
        element: str | None = None,
        timeout: float = WRITE_TIMEOUT,
        until_clear: InsteonAddress | None = None,
    ) -> Write:
        """Send one write and wait until IoX has finished it.

        IoX reports its work on the event stream: busy (`_5` 1), then not busy
        (`_5` 0). The write is done when the call has returned and IoX is no
        longer busy, or a short grace passes with no busy event at all. The
        events seen meanwhile are returned, so a caller can tell which Devices
        IoX wrote to (`_3` WD) or still holds Pending Writes for (`_3` WH).

        With `until_clear`, a Device in linking mode, the wait goes on while
        IoX still holds Pending Writes for it, until it writes them or
        `BATTERY_WINDOW` passes, when the Device leaves linking mode anyway.
        """
        events: list[Event] = []
        busy = settled = False
        getter: asyncio.Future[Event] | None = None
        async with self.events() as queue:
            call = asyncio.create_task(self.soap(operation, params, element=element, timeout=timeout))
            try:
                async with asyncio.timeout(timeout):
                    while not (settled and call.done()):
                        waiting = set() if call.done() else {call}
                        getter = asyncio.ensure_future(queue.get())
                        done, _ = await asyncio.wait(
                            {getter, *waiting},
                            timeout=None if waiting or busy else _STREAM_GRACE,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if getter not in done:
                            getter.cancel()
                            if call.done():
                                call.result()  # raises if the call failed
                            if not waiting and not busy:
                                break
                            continue
                        event = getter.result()
                        events.append(event)
                        if event.control == "_5" and event.action == "1":
                            busy, settled = True, False
                        elif event.control == "_5" and event.action == "0":
                            busy, settled = False, True
                    call.result()
                if until_clear is not None:
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(BATTERY_WINDOW):
                            while busy or Write(operation, tuple(events)).pending(until_clear):
                                event = await queue.get()
                                events.append(event)
                                if event.control == "_5":
                                    busy = event.action == "1"
            except TimeoutError as error:
                raise IoxError(f"SOAP {operation}: IoX was still busy after {timeout:.0f} s") from error
            finally:
                for task in (getter, call):
                    if task is not None and not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
        return Write(operation, tuple(events))

    async def programs(self) -> list[Program]:
        """Every program and program folder, without bodies (`/rest/programs`)."""
        return parse_programs(await self.get("programs?subfolders=true"))

    async def set_program_enabled(self, program: Program, enabled: bool) -> None:
        """Enable or disable a program (`/rest/programs/<id>/enable|disable`).

        A write, though a GET: only a command on the rails may call it. The
        caller confirms the change by reading `/rest/programs` again.
        """
        await self.get(f"programs/{program.iox_id}/{'enable' if enabled else 'disable'}")

    async def variables(self) -> list[Variable]:
        """Every integer and state variable, with its value and init."""
        variables = []
        for kind in VariableKind:
            definitions = await self.get(f"vars/definitions/{kind.value}")
            values = await self.get(f"vars/get/{kind.value}")
            variables.extend(parse_variables(kind, definitions, values))
        return variables


def parse_variables(kind: VariableKind, definitions: str, values: str) -> list[Variable]:
    """Join `/rest/vars/definitions/<kind>` (names) to `/rest/vars/get/<kind>` (values) by id."""
    names = {
        int(entry.get("id", "")): entry.get("name", "")
        for entry in _xml(definitions, f"/rest/vars/definitions/{kind.value}").iter("e")
    }
    variables = []
    for var in _xml(values, f"/rest/vars/get/{kind.value}").iter("var"):
        number = int(var.get("id", ""))
        variables.append(
            Variable(
                kind=kind,
                id=number,
                name=names.get(number, ""),
                value=int(var.findtext("val", "0")),
                init=int(var.findtext("init", "0")),
                precision=int(var.findtext("prec", "0")),
                changed=(var.findtext("ts") or "").strip() or None,
            )
        )
    return sorted(variables, key=lambda variable: variable.id)


class IoxFiles:
    """IoX's config files under `/var/isy/FILES/CONF`, read over SSH. Read-only.

    It reads program bodies, and archives the whole of CONF as the IoX backup.

    The files are root's, so the read runs under `sudo`, which takes the SSH
    user's password on stdin (`-S`). The password never goes on a command line.
    The host key is checked against `known_hosts`, by default the user's own.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,  # gitleaks:allow (an annotation, not a value)
        *,
        port: int = 22,
        known_hosts: Path | None = None,
        timeout: float = 60,
    ) -> None:
        self._host, self._port, self._user, self._password = host, port, user, password
        self._known_hosts = known_hosts
        self._timeout = timeout

    async def program_bodies(self) -> dict[int, Body]:
        """Every program's If, Then and Else from `CONF/D2D/*.PGM`, by program id.

        A file the tool cannot read keeps its whole text as one Undecoded
        statement, under the id its name gives, so one bad file loses nothing else.
        """
        bodies = {}
        for name, data in (await self._read_folder("D2D")).items():
            stem, _, extension = name.rpartition("/")[2].partition(".")
            if extension.upper() != "PGM" or not _HEX_ID.fullmatch(stem):
                continue  # IoX names each program's file by its id in hex
            text = data.decode(errors="replace")
            try:
                number, body = parse_program_body(text, name)
            except IoxError:
                nodes = tuple(dict.fromkeys(_NODE_ATTRIBUTE.findall(text)))
                number, body = int(stem, 16), Body((Undecoded(text.strip(), nodes, whole_file=True),), (), (), "")
            bodies[number] = body
        return bodies

    async def conf_archive(self) -> bytes:
        """All of `/var/isy/FILES/CONF` as one tar archive: the IoX backup.

        Raises IoxError unless the archive is a tar holding at least one file.
        """
        where = f"SSH to {self._user}@{self._host}"
        data = await self._tar(FILES, "CONF")
        if not _files_in(data, CONF, where=where):
            raise IoxError(f"{where}: the archive of {CONF} holds no files")
        return data

    async def _read_folder(self, folder: str) -> dict[str, bytes]:
        """The files in one folder under CONF, by path relative to CONF."""
        return _files_in(await self._tar(CONF, folder), f"{CONF}/{folder}", where=f"SSH to {self._user}@{self._host}")

    async def _tar(self, parent: str, folder: str) -> bytes:
        """`folder` under `parent` as a tar archive, read as root (`sudo -S`)."""
        command = f"sudo -S -p '' tar -cf - -C {parent} {folder}"
        where = f"SSH to {self._user}@{self._host}"
        try:
            async with asyncio.timeout(self._timeout):
                async with asyncssh.connect(
                    self._host,
                    port=self._port,
                    username=self._user,
                    password=self._password,
                    known_hosts=str(self._known_hosts) if self._known_hosts else (),
                    client_keys=None,
                    agent_path=None,
                ) as connection:
                    result = await connection.run(
                        command, input=f"{self._password}\n".encode(), encoding=None, check=False
                    )
        except asyncssh.HostKeyNotVerifiable as error:
            trusted = self._known_hosts or "~/.ssh/known_hosts"
            raise IoxError(f"{where}: its host key is not in {trusted}; connect once with ssh to trust it") from error
        except asyncssh.PermissionDenied as error:
            raise IoxError(f"{where}: the Controller refused the SSH password") from error
        except TimeoutError as error:
            raise IoxError(f"{where}: no answer in {self._timeout:.0f} s") from error
        except (asyncssh.Error, OSError) as error:
            raise IoxError(f"{where}: {error}") from error
        if result.exit_status != 0:
            stderr = _bytes(result.stderr).decode(errors="replace").strip().splitlines()
            reason = stderr[-1] if stderr else "no message"
            raise IoxError(f"{where}: reading {parent}/{folder} exited {result.exit_status}: {reason}")
        return _bytes(result.stdout)


def _files_in(data: bytes, source: str, *, where: str) -> dict[str, bytes]:
    """The files in a tar archive, by path. Raises IoxError when it is not one."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            return {
                member.name: extracted.read()
                for member in archive.getmembers()
                if member.isfile() and (extracted := archive.extractfile(member)) is not None
            }
    except tarfile.TarError as error:
        raise IoxError(f"{where}: reading {source} returned no tar archive ({error})") from error


def _bytes(output: bytes | str | None) -> bytes:
    """A process's output, which asyncssh types as text or bytes; with `encoding=None` it is bytes."""
    return output.encode() if isinstance(output, str) else output or b""


def parse_program_body(text: str, source: str = "D2D file") -> tuple[int, Body]:
    """A program's id and body from its D2D file (`CONF/D2D/<id>.PGM`).

    The file is `<d2d><trigger>` XML with the id in decimal. Statements are
    decoded in the shapes seen on IoX 6.0.5; any other shape is kept raw.
    """
    trigger = _xml(text, source).find("trigger")
    if trigger is None or not (trigger.findtext("id") or "").strip().isdigit():
        raise IoxError(f"{source}: no <trigger> with an <id>")
    return int(trigger.findtext("id", "")), Body(
        conditions=_conditions(trigger.find("if")),
        then=_actions(trigger.find("then")),
        otherwise=_actions(trigger.find("else")),
        comment=(trigger.findtext("comment") or "").strip(),
    )


def _conditions(block: ET.Element | None) -> tuple[Statement, ...]:
    """An If block. `<and />` or `<or />` comes before the condition it joins."""
    statements: list[Statement] = []
    joiner: ET.Element | None = None
    for child in block if block is not None else ():
        if child.tag in ("and", "or") and joiner is None:
            joiner = child
            continue
        condition = _condition(child, joiner.tag if joiner is not None else "and")
        statements.append(condition or _undecoded([joiner, child] if joiner is not None else [child]))
        joiner = None
    if joiner is not None:
        statements.append(_undecoded([joiner]))
    return tuple(statements)


def _condition(element: ET.Element, joiner: str) -> Condition | None:
    """`<status id="ST" node="…" op="ISNOT"><var type="2" id="1" /></status>`."""
    if element.tag != "status" or len(element) != 1:
        return None
    left = _node_status(element)
    right = _variable(element[0])
    if left is None or right is None or not element.get("op"):
        return None
    return Condition(joiner, left, element.get("op", ""), right)


def _actions(block: ET.Element | None) -> tuple[Statement, ...]:
    return tuple(_action(child) or _undecoded([child]) for child in (block if block is not None else ()))


def _action(element: ET.Element) -> Statement | None:
    match element.tag, [child.tag for child in element]:
        case "var", ["op", "status"]:
            # `<var id="1" type="2"><op>EQ</op><status id="ST" node="…"/></var>`
            variable, value = _variable(element, allow_children=True), _node_status(element[1])
            op = (element.findtext("op") or "").strip()
            return SetVariable(variable, op, value) if variable and value and op else None
        case "wait", ["seconds"]:
            seconds = (element.findtext("seconds") or "").strip()
            return Wait(int(seconds)) if seconds.isdigit() else None
        case "cmd", tags if all(tag == "p" for tag in tags):
            # `<cmd id="DON" node="…"><p id=""><var type="2" id="1" /></p></cmd>`
            parameters = [_variable(p[0]) if len(p) == 1 else None for p in element]
            if not element.get("id") or not element.get("node") or None in parameters:
                return None
            return Command(element.get("id", ""), element.get("node", ""), tuple(p for p in parameters if p))
    return None


def _node_status(element: ET.Element) -> NodeStatus | None:
    if element.tag != "status" or not element.get("node") or not element.get("id"):
        return None
    return NodeStatus(element.get("node", ""), element.get("id", ""))


def _variable(element: ET.Element, *, allow_children: bool = False) -> VariableRef | None:
    """`<var type="2" id="1" />`: type 1 integer, 2 state."""
    kind, number = element.get("type", ""), element.get("id", "")
    if element.tag != "var" or (len(element) and not allow_children):
        return None
    if not (kind.isdigit() and number.isdigit()) or int(kind) not in VariableKind:
        return None
    return VariableRef(VariableKind(int(kind)), int(number))


def _undecoded(elements: Iterable[ET.Element]) -> Undecoded:
    raw, nodes = [], []
    for element in elements:
        bare = copy.copy(element)
        bare.tail = None
        raw.append(ET.tostring(bare, encoding="unicode"))
        nodes += [node for child in element.iter() if (node := child.get("node"))]
    return Undecoded("".join(raw), tuple(dict.fromkeys(nodes)))


def parse_programs(text: str) -> list[Program]:
    """Programs from a `/rest/programs?subfolders=true` response. Ids are hex."""

    def flag(element: ET.Element, name: str) -> bool | None:
        value = element.get(name)
        return None if value is None else value == "true"

    def text_of(element: ET.Element, tag: str) -> str | None:
        return (element.findtext(tag) or "").strip() or None

    programs = []
    for element in _xml(text, "/rest/programs").iter("program"):
        is_folder = element.get("folder") == "true"
        parent = element.get("parentId")
        programs.append(
            Program(
                id=int(element.get("id", ""), 16),
                name=element.findtext("name", ""),
                parent=int(parent, 16) if parent else None,
                is_folder=is_folder,
                enabled=None if is_folder else flag(element, "enabled"),
                run_at_startup=None if is_folder else flag(element, "runAtStartup"),
                status=flag(element, "status"),
                last_run=text_of(element, "lastRunTime"),
                last_finish=text_of(element, "lastFinishTime"),
            )
        )
    return programs


def parse_nodes(text: str, uuid: str) -> Network:
    """Build the Network from a `/rest/nodes` response."""
    root = _xml(text, "/rest/nodes")
    folders = {folder.findtext("address", ""): folder.findtext("name", "") for folder in root.iter("folder")}
    buttons: dict[InsteonAddress, list[tuple[ET.Element, ButtonAddress]]] = {}
    primaries: dict[InsteonAddress, ET.Element] = {}
    foreign: list[ForeignNode] = []
    addresses: set[str] = set()

    for node in root.iter("node"):
        raw = node.findtext("address", "").strip()
        addresses.add(raw)
        family = node.find("family")
        family_id = family.text.strip() if family is not None and family.text else None
        address = ButtonAddress.try_parse(raw)
        if family_id not in _INSTEON_FAMILIES or address is None:
            label = _FAMILY_NAMES.get(family_id or "", f"family {family_id}")
            foreign.append(ForeignNode(raw, node.findtext("name", ""), label))
            continue
        primary = ButtonAddress.try_parse(node.findtext("pnode", "")) or address
        buttons.setdefault(primary.device, []).append((node, address))
        if primary == address:
            primaries[address.device] = node

    devices = []
    for device_address, members in buttons.items():
        primary = primaries.get(device_address, members[0][0])
        parent = primary.find("parent")
        flag = int(primary.get("flag", "0"))
        devices.append(
            Device(
                address=device_address,
                name=primary.findtext("name", ""),
                model=primary.get("nodeDefId", ""),
                type_code=primary.findtext("type", ""),
                folder=folders.get(parent.text or "") if parent is not None and parent.get("type") == "3" else None,
                enabled=primary.findtext("enabled", "true").strip() != "false",
                comms_error=bool(flag & _FLAG_IN_ERROR),
                buttons=tuple(
                    sorted(
                        (Button(address, node.findtext("name", ""), node.get("nodeDefId", "")) for node, address in members),
                        key=lambda button: button.address,
                    )
                ),
            )
        )

    scenes = []
    for group in root.iter("group"):
        if int(group.get("flag", "0")) & _FLAG_ROOT:
            continue
        links = [(link.get("type"), ButtonAddress.try_parse(link.text or "")) for link in group.iter("link")]
        device_group = group.findtext("deviceGroup", "").strip()
        scenes.append(
            Scene(
                address=group.findtext("address", "").strip(),
                name=group.findtext("name", ""),
                controllers=tuple(button for link_type, button in links if link_type == _CONTROLLER and button),
                responders=tuple(button for link_type, button in links if link_type != _CONTROLLER and button),
                group=int(device_group) if device_group.isdigit() else None,
            )
        )

    return Network(
        uuid=uuid,
        devices=tuple(sorted(devices, key=lambda device: device.address)),
        scenes=tuple(scenes),
        foreign=tuple(foreign),
        node_addresses=frozenset(addresses),
    )


def _link(info: ET.Element) -> Link:
    def number(tag: str) -> int:
        value = info.findtext(tag)
        if value is None:
            raise IoxError(f"Link without <{tag}>")
        return int(value)

    return Link(
        index=number("ix"),
        flags=number("fl"),
        group=number("gr"),
        address=InsteonAddress.from_int(number("id")),
        data=number("data"),
    )


def _parse_event(text: str) -> Event | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if root.tag != "Event":
        return None
    info = root.find("eventInfo")
    return Event(
        control=root.findtext("control", ""),
        action=root.findtext("action", ""),
        info=info if info is not None else ET.Element("eventInfo"),
        node=(root.findtext("node") or "").strip(),
    )


def _xml(text: str, source: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as error:
        raise IoxError(f"{source}: not XML ({error})") from error


def _transport_error(what: str, error: aiohttp.ClientError) -> IoxError:
    if isinstance(error, aiohttp.ServerFingerprintMismatch):
        return IoxError(f"{what}: the Controller's certificate does not match IOX_CERT_SHA256")
    return IoxError(f"{what}: {error}")


def _raise_for_status(status: int, what: str) -> None:
    if status == 401:
        raise IoxError(f"{what}: HTTP 401, IoX refused the user name or password", status=status)
    if status >= 400:
        raise IoxError(f"{what}: HTTP {status}", status=status)


def _ssl_setting(base_url: str, cert_sha256: str | None) -> bool | aiohttp.Fingerprint:
    """Pin the Controller's self-signed certificate when a fingerprint is given."""
    if not base_url.startswith("https"):
        return True
    if cert_sha256:
        return aiohttp.Fingerprint(bytes.fromhex(cert_sha256.replace(":", "")))
    print(
        "warning: IoX certificate not pinned; set IOX_CERT_SHA256 to pin it",
        file=sys.stderr,
    )
    return False
