"""The one module that talks to IoX (ADR 0013).

REST reads (`/rest/...`), SOAP calls (`POST /services`), and the event stream
(`/rest/subscribe`) all go through `IoxClient`, over HTTPS on port 8443 with a
local IoX user. The wire facts it relies on are in
`insteon-network/research/iox-api.md`.

IoX's XML comes from the Administrator's own Controller on the local network,
so it is parsed with the standard library.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import sys
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from xml.sax.saxutils import escape

import aiohttp

from insteon.address import InsteonAddress, ButtonAddress
from insteon.network import Button, Device, ForeignNode, Link, Modem, Network, Scene

SERVICE = "urn:udi-com:service:X_Insteon_Lighting_Service:1"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"

# Node flag bits (PyISY's names): 0x04 a Scene, 0x08 the root Scene, 0x10 in error.
_FLAG_ROOT = 0x08
_FLAG_IN_ERROR = 0x10
# Link types in a Scene's members: 16 is a Controller Button. The REST wiki
# gives 32 for a Responder Button, but IoX 6.0.5 sends 0; any other type is a
# Responder Button.
_CONTROLLER = "16"
# Seconds to wait for events still in flight after a streaming call returns.
_STREAM_GRACE = 5.0
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


@dataclass(frozen=True)
class Event:
    """One message from the event stream."""

    control: str
    action: str
    info: ET.Element


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
        records: list[Link] = []
        busy = ended = False
        async with self.events() as queue:
            call = asyncio.create_task(
                self.soap(
                    "DeviceSpecific",
                    {"command": "G_PLM_ALL", "node": modem.iox, "option": "", "flag": 1, "CDATA": "0 -1"},
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
                        if event.control == "_2" and event.action == "1":
                            records.append(_link(event.info))
                        elif event.control == "_5" and event.action == "1":
                            busy = True
                        elif event.control == "_5" and event.action == "0" and busy:
                            ended = True
                    call.result()
            except TimeoutError as error:
                raise IoxError(f"the Modem Link table read took over {timeout:.0f} s") from error
            finally:
                if not call.done():
                    call.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await call
        return records


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
        scenes.append(
            Scene(
                address=group.findtext("address", "").strip(),
                name=group.findtext("name", ""),
                controllers=tuple(button for link_type, button in links if link_type == _CONTROLLER and button),
                responders=tuple(button for link_type, button in links if link_type != _CONTROLLER and button),
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
    return Event(
        control=root.findtext("control", ""),
        action=root.findtext("action", ""),
        info=root.find("eventInfo") if root.find("eventInfo") is not None else ET.Element("eventInfo"),
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
        raise IoxError(f"{what}: HTTP 401, IoX refused the user name or password")
    if status >= 400:
        raise IoxError(f"{what}: HTTP {status}")


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
