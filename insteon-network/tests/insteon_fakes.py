"""A fake IoX and a fake Home Assistant, served on localhost from made-up data.

Every name, address and uuid here is a placeholder. Real responses stay in the
gitignored `.private/` folder (ADR 0004).
"""

from __future__ import annotations

import asyncio
import base64
import io
import tarfile
import contextlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncssh
import pytest
from aiohttp import WSMsgType, web

FIXTURES = Path(__file__).parent / "fixtures"
UUID = "00:00:00:00:00:00"
MODEM = "2A.2B.2C"
USER, PASSWORD = "tool", "example-password"
SSH_USER, SSH_PASSWORD = "example-admin", "example-ssh-password"
# The only commands the tool may run on the Controller: a read of the D2D
# folder, and the IoX backup, a read of all of CONF.
D2D_TAR = "sudo -S -p '' tar -cf - -C /var/isy/FILES/CONF D2D"
CONF_TAR = "sudo -S -p '' tar -cf - -C /var/isy/FILES CONF"
# The SOAP body elements that change IoX.
WRITES = frozenset(
    {
        "AddNode", "RemoveNode", "MoveNode", "RemoveFromGroup", "AddGroup", "RemoveGroup",
        "SetSceneProfile", "WriteDeviceUpdates", "RestoreDeviceFromNode", "ReplaceModem", "ReplaceDevice",
    }
)
# The REST calls that change IoX: a program's enable or disable.
PROGRAM_ACTION = re.compile(r"programs/([0-9A-Fa-f]{1,4})/(enable|disable)")
TOKEN = "example-token"


def _event(control: str, action: str, node: str = "", info: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Event seqnum="0" sid="uuid:1">'
        f"<control>{control}</control><action>{action}</action><node>{node}</node>"
        f"<eventInfo>{info}</eventInfo></Event>"
    )


def plm_record(ix: int, flags: int, group: int, address: int, data: int = 0) -> str:
    """A Modem Link record event, in the shape IoX 6.0.5 streams for G_PLM_ALL."""
    return _event(
        "_2",
        "1",
        info=f"<ix>{ix}</ix><ad>{ix}</ad><fl>{flags}</fl><gr>{group}</gr><id>{address}</id><data>{data}</data>",
    )


def link_record(action: str, node: str, ix: int, flags: int, group: int, address: int, data: int = 0) -> str:
    """A Device Link record event: action 3 for G_ISY_ALL (expected), 2 for G_DEV_ALL (read)."""
    return _event(
        "_2",
        action,
        node,
        f"<ix>{ix}</ix><ad>{4088 - 8 * ix}</ad><fl>{flags}</fl><gr>{group}</gr><id>{address}</id><data>{data}</data>",
    )


@dataclass(frozen=True)
class Rec:
    """One Link as a test writes it: flags, group, linked address and data."""

    flags: int
    group: int
    address: int
    data: int = 0


def _table(action: str, node: str, records: list[Rec]) -> list[str]:
    events = [link_record(action, node, ix, r.flags, r.group, r.address, r.data) for ix, r in enumerate(records)]
    return events + [link_record(action, node, len(records), 0, 0, 0)]


@dataclass
class FakeIox:
    nodes_xml: str = field(default_factory=lambda: (FIXTURES / "nodes.xml").read_text())
    config_xml: str = field(default_factory=lambda: (FIXTURES / "config.xml").read_text())
    programs_xml: str = field(default_factory=lambda: (FIXTURES / "programs.xml").read_text())
    plm_info: str = f"{MODEM} v9B / Connected"
    plm_records: list[str] = field(default_factory=list)
    expected: dict[str, list[Rec]] = field(default_factory=dict)
    """G_ISY_ALL tables, keyed by the Device's primary Button address as IoX writes it. Empty by default."""
    actual: dict[str, list[Rec]] = field(default_factory=dict)
    """G_DEV_ALL tables. A Device missing here does not answer: IoX gives up with HTTP 501."""
    silent: set[str] = field(default_factory=set)
    """Devices whose G_DEV_ALL read never ends, so the client's own timeout fires."""
    soap_calls: list[dict[str, Any]] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)
    rest_writes: list[str] = field(default_factory=list)
    """The REST calls that change IoX (`PROGRAM_ACTION`), in order."""
    ignored_programs: set[str] = field(default_factory=set)
    """Program ids (`000A`) whose enable or disable IoX answers as done but does not apply."""
    addable: dict[str, str] = field(default_factory=dict)
    """`<node>` XML that AddNode adds, by the `id` it is sent. An id missing here is not added."""
    refuse: set[str] = field(default_factory=set)
    """Write elements IoX answers with HTTP 500."""
    pending: set[str] = field(default_factory=set)
    """Nodes IoX reports Pending Writes for (`_3` WH) after a write to them, rather than writing (`_3` WD)."""
    written_later: set[str] = field(default_factory=set)
    """Nodes IoX holds Pending Writes for at first (`_3` WH), then writes (`_3` WD) just after the call returns."""
    trailing_wh: set[str] = field(default_factory=set)
    """Buttons IoX reports Pending Writes for (`_3` WH) as a write to their Device ends, after writing it (`_3`
    WD), as IoX 6.0.5 did live for a powered keypad whose write had gone through (#102)."""
    keeps_links: set[str] = field(default_factory=set)
    """Devices (primary Button, as IoX writes it) whose Links a Restore leaves as they were, as a Device that
    missed the write would."""
    replace_keeps_old: bool = False
    """ReplaceDevice answers 200 but changes nothing yet, as an IoX that wants a reboot first might."""
    _sockets: list[web.WebSocketResponse] = field(default_factory=list)
    _later: list[asyncio.Future[None]] = field(default_factory=list)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    def _authorised(self, request: web.Request) -> bool:
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return request.headers.get("Authorization") == expected

    async def _rest(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            return web.Response(status=401)
        path = request.match_info["path"]
        self.gets.append(path)
        if action := PROGRAM_ACTION.fullmatch(path):
            self.rest_writes.append(path)
            return self._program_action(action[1], action[2] == "enable")
        body = {
            "nodes": self.nodes_xml,
            "config": self.config_xml,
            "programs": self.programs_xml,
            **{f"vars/{kind}/{n}": (FIXTURES / f"vars-{kind}-{n}.xml").read_text() for kind in ("definitions", "get") for n in (1, 2)},
        }.get(path)
        if body is None:
            return web.Response(status=404)
        return web.Response(text=body, content_type="text/xml")

    def _program_action(self, program_id: str, enabled: bool) -> web.StreamResponse:
        """Enable or disable a program, as IoX does: 404 for an unknown id, nothing for a folder."""
        root = ET.fromstring(self.programs_xml)
        program = next((p for p in root.iter("program") if int(p.get("id", ""), 16) == int(program_id, 16)), None)
        if program is None:
            return web.Response(status=404)
        if program.get("folder") != "true" and program.get("id") not in self.ignored_programs:
            program.set("enabled", "true" if enabled else "false")
        self.programs_xml = ET.tostring(root, encoding="unicode")
        return web.Response(
            text='<?xml version="1.0" encoding="UTF-8"?><RestResponse succeeded="true"><status>200</status></RestResponse>',
            content_type="text/xml",
        )

    async def _subscribe(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            return web.Response(status=401)
        ws = web.WebSocketResponse(protocols=("ISYSUB",), autoping=False)
        await ws.prepare(request)
        await ws.send_str('<?xml version="1.0" encoding="UTF-8"?><SubscriptionResponse><SID>uuid:1</SID></SubscriptionResponse>')
        await ws.send_str(_event("ST", "0", "AA BB CC 1"))
        self._sockets.append(ws)
        try:
            async for message in ws:
                if message.type == WSMsgType.CLOSE:
                    break
        finally:
            self._sockets.remove(ws)
        return ws

    async def _services(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            return web.Response(status=401)
        body = ET.fromstring(await request.text())[0][0]
        element = body.tag.rsplit("}", 1)[-1]
        call = {
            "soapaction": request.headers.get("SOAPACTION"),
            "element": element,
            "namespace": body.tag[1:].split("}")[0],
            "params": {child.tag: child.text or "" for child in body},
        }
        self.soap_calls.append(call)
        if element in WRITES:
            return await self._write(element, call["params"])
        if element == "DeviceSpecific" and call["params"].get("command") == "G_PLM_INFO":
            return web.Response(text=self.plm_info)
        if element == "DeviceSpecific" and call["params"].get("command") == "G_PLM_ALL":
            # Like IoX: the call blocks while the records stream, bracketed by busy events.
            for ws in self._sockets:
                await ws.send_str(_event("_5", "1"))
                for record in self.plm_records:
                    await ws.send_str(record)
                    await asyncio.sleep(0)
                await ws.send_str(_event("ST", "255", "AA BB CC 1"))
                await ws.send_str(_event("_5", "0"))
        if element == "DeviceSpecific" and call["params"].get("command") == "STOP":
            self._stop.set()
        if element == "DeviceSpecific" and call["params"].get("command") in ("G_ISY_ALL", "G_DEV_ALL"):
            node = call["params"].get("node", "")
            reading = call["params"]["command"] == "G_DEV_ALL"
            tables, action = (self.actual, "2") if reading else (self.expected, "3")
            if reading and node in self.silent:
                # Like IoX, the read runs until STOP cancels it.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), 30)
                return web.Response(status=501)
            await self._broadcast([_event("_5", "1")])
            if reading and node not in tables:
                await self._broadcast([_event("_5", "0")])
                return web.Response(status=501)
            await self._broadcast([*_table(action, node, tables.get(node, [])), _event("_5", "0")])
        return web.Response(
            text='<?xml version="1.0" encoding="UTF-8"?><s:Envelope><s:Body><UDIDefaultResponse><status>200</status></UDIDefaultResponse></s:Body></s:Envelope>',
            content_type="text/xml",
        )

    async def _write(self, element: str, params: dict[str, str]) -> web.StreamResponse:
        """Change the fake network as IoX would, bracketed by busy events."""
        if element in self.refuse:
            return web.Response(status=500)
        node = params.get("node") or params.get("id") or ""
        held = node in self.pending or node in self.written_later
        written = [_event("_3", "WH" if held else "WD", node)] if node else []
        await self._broadcast([_event("_5", "1"), *written])
        self._change(element, params)
        devices = [params[key].split()[:3] for key in ("node", "id", "NewNode") if params.get(key)]
        trailing = [_event("_3", "WH", button) for button in sorted(self.trailing_wh) if button.split()[:3] in devices]
        await self._broadcast([*trailing, _event("_5", "0")])
        if node in self.written_later:

            async def write_later() -> None:
                await asyncio.sleep(0.05)
                await self._broadcast([_event("_5", "1"), _event("_3", "WD", node), _event("_5", "0")])

            self._later.append(asyncio.ensure_future(write_later()))
        return web.Response(
            text='<?xml version="1.0" encoding="UTF-8"?><s:Envelope><s:Body><UDIDefaultResponse><status>200</status></UDIDefaultResponse></s:Body></s:Envelope>',
            content_type="text/xml",
        )

    def _change(self, element: str, params: dict[str, str]) -> None:
        root = ET.fromstring(self.nodes_xml)

        def group(address: str) -> ET.Element | None:
            return next((g for g in root.iter("group") if g.findtext("address") == address), None)

        if element == "RemoveFromGroup" and (scene := group(params["group"])) is not None:
            for members in scene.iter("members"):
                for link in [link for link in members if link.text == params["node"]]:
                    members.remove(link)
        elif element == "MoveNode" and (scene := group(params["group"])) is not None:
            members = scene.find("members")
            if members is None:
                members = ET.SubElement(scene, "members")
            link = ET.SubElement(members, "link", type="16" if params["flag"] == "16" else "32")
            link.text = params["node"]
        elif element == "AddGroup":
            scene = ET.SubElement(root, "group", flag="132", nodeDefId="InsteonDimmer")
            ET.SubElement(scene, "address").text = params["id"]
            ET.SubElement(scene, "name").text = params["name"]
            ET.SubElement(scene, "deviceGroup").text = "40"
        elif element == "RemoveGroup" and (scene := group(params["id"])) is not None:
            root.remove(scene)
        elif element == "RemoveNode":
            device = params["id"].rsplit(" ", 1)[0] + " "
            for node in [n for n in root.findall("node") if (n.findtext("pnode") or "").startswith(device)]:
                root.remove(node)
            for members in root.iter("members"):
                for link in [link for link in members if (link.text or "").startswith(device)]:
                    members.remove(link)
        elif element == "AddNode" and params["id"] in self.addable:
            root.append(ET.fromstring(self.addable[params["id"]]))
        elif element == "ReplaceDevice" and not self.replace_keeps_old:
            # The old Device's Scenes move onto the new one, Button by Button,
            # and the new Device takes the old one's name; the old one is gone.
            old, new = (params[key].rsplit(" ", 1)[0] + " " for key in ("node", "NewNode"))
            old_nodes = [n for n in root.findall("node") if (n.findtext("pnode") or "").startswith(old)]
            name = next((n.findtext("name") for n in old_nodes if n.findtext("address") == n.findtext("pnode")), "")
            for node in old_nodes:
                root.remove(node)
            for node in root.findall("node"):
                if node.findtext("address") == node.findtext("pnode") and (node.findtext("pnode") or "").startswith(new):
                    node.find("name").text = name
            for members in root.iter("members"):
                for link in members:
                    if (link.text or "").startswith(old):
                        link.text = new + link.text[len(old):]
        elif element == "RestoreDeviceFromNode" and params["node"] in self.actual:
            if params["node"] not in self.keeps_links:
                self.actual[params["node"]] = list(self.expected.get(params["node"], []))
        self.nodes_xml = ET.tostring(root, encoding="unicode")

    def node_addresses(self) -> frozenset[str]:
        """Every node address the fake Controller has now, as IoX writes it."""
        return frozenset(n.findtext("address", "") for n in ET.fromstring(self.nodes_xml).iter("node"))

    def writes(self) -> list[tuple[str, dict[str, str]]]:
        """Every write the tool sent: each SOAP write's body element and parameters, in order,
        then each REST write as `REST <path>`."""
        soap = [(c["element"], c["params"]) for c in self.soap_calls if c["element"] in WRITES]
        return soap + [(f"REST {path}", {}) for path in self.rest_writes]

    async def _broadcast(self, events: list[str]) -> None:
        for ws in self._sockets:
            for event in events:
                await ws.send_str(event)
                await asyncio.sleep(0)

    def device_reads(self) -> list[str]:
        """The nodes the tool read Links from (G_DEV_ALL), in order."""
        return [c["params"]["node"] for c in self.soap_calls if c["params"].get("command") == "G_DEV_ALL"]

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/rest/subscribe", self._subscribe)
        app.router.add_get("/rest/{path:.+}", self._rest)
        app.router.add_post("/services", self._services)

        async def close_sockets(_: web.Application) -> None:
            for ws in self._sockets:
                await ws.close()

        app.on_shutdown.append(close_sockets)
        return app


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@dataclass
class FakeEisySsh:
    """The Controller's SSH login. It answers only the D2D read and the CONF
    archive, and only when sudo's password arrives on stdin."""

    files: dict[str, bytes] = field(
        default_factory=lambda: {f"D2D/{path.name}": path.read_bytes() for path in sorted((FIXTURES / "programs").iterdir())}
    )
    """The files under CONF, by path relative to it."""
    commands: list[str] = field(default_factory=list)
    sudo_passwords: list[str] = field(default_factory=list)

    def server(self) -> asyncssh.SSHServer:
        class Server(asyncssh.SSHServer):
            def password_auth_supported(self) -> bool:
                return True

            def validate_password(self, username: str, password: str) -> bool:  # gitleaks:allow
                return (username, password) == (SSH_USER, SSH_PASSWORD)

        return Server()

    async def process(self, process: asyncssh.SSHServerProcess[bytes]) -> None:
        command = process.command or ""
        self.commands.append(command)
        if command not in (D2D_TAR, CONF_TAR):
            process.stderr.write(b"sh: not allowed in the fake\n")
            process.exit(127)
            return
        line = (await process.stdin.readline()).decode().rstrip("\n")
        self.sudo_passwords.append(line)
        if line != SSH_PASSWORD:
            process.stderr.write(b"Sorry, try again.\nsudo: 1 incorrect password attempt\n")
            process.exit(1)
            return
        if command == CONF_TAR:
            process.stdout.write(_tar({f"CONF/{name}": data for name, data in self.files.items()}))
        else:
            process.stdout.write(_tar(self.files))
        process.exit(0)


@dataclass(frozen=True)
class SshEndpoint:
    port: int
    known_hosts: Path


OLD_UUID = "11:11:11:11:11:11"


def _device(device_id: str, identifier: str, *, entries: tuple[str, ...] = ("isy-entry",), domain: str = "isy994") -> dict[str, Any]:
    return {
        "id": device_id,
        "identifiers": [[domain, identifier]],
        "config_entries": list(entries),
        "name": f"Example {device_id}",
        "name_by_user": None,
    }


def _entity(entity_id: str, unique_id: str, device_id: str | None) -> dict[str, Any]:
    return {"entity_id": entity_id, "unique_id": unique_id, "platform": "isy994", "device_id": device_id}


def ha_devices() -> list[dict[str, Any]]:
    """A made-up device registry: the Controller's devices, Stale HA Devices, and others."""
    return [
        _device("hub", UUID),
        _device("variables", f"{UUID}_variables"),
        _device("dimmer", f"{UUID}_AA BB CC 1"),
        _device("relay", f"{UUID}_A B C 1"),
        _device("keypad", f"{UUID}_11 22 33 1"),
        _device("zwave-lock", f"{UUID}_ZY003_1"),
        _device("gone", f"{UUID}_33 44 55 1"),
        _device("gone-with-entities", f"{UUID}_44 55 66 1"),
        _device("old-hub", OLD_UUID),
        _device("old-dimmer", f"{OLD_UUID}_AA BB CC 1"),
        _device("old-network", f"{OLD_UUID}_network"),
        _device("hue-bulb", "example-bulb", entries=("other-entry",), domain="hue"),
    ]


def ha_entities() -> list[dict[str, Any]]:
    return [
        _entity("light.example_dimmer", f"{UUID}_AA BB CC 1", "dimmer"),
        _entity("number.example_dimmer_on_level", f"{UUID}_AA BB CC 1_OL", "dimmer"),
        _entity("light.example_relay", f"{UUID}_A B C 1", None),
        _entity("event.example_keypad_a", f"{UUID}_11 22 33 3_button", "keypad"),
        _entity("switch.example_scene", f"{UUID}_12345", "keypad"),
        _entity("sensor.example_gone", f"{UUID}_44 55 66 1", "gone-with-entities"),
        {"entity_id": "light.example_bulb", "unique_id": "example-bulb", "platform": "hue", "device_id": "hue-bulb"},
    ]


def ha_automations() -> dict[str, dict[str, Any]]:
    """Made-up automations: their friendly names and what `search/related` finds in them."""
    return {
        "automation.example_dimmer_level": {
            "name": "Example dimmer level",
            "related": {"entity": ["light.example_dimmer", "input_number.example_level"], "area": ["example"]},
        },
        "automation.example_keypad_press": {"name": "Example keypad press", "related": {"device": ["keypad"]}},
        "automation.example_bulb": {"name": "Example bulb", "related": {"entity": ["light.example_bulb"]}},
    }


@dataclass
class FakeHa:
    devices: list[dict[str, Any]] = field(default_factory=ha_devices)
    entities: list[dict[str, Any]] = field(default_factory=ha_entities)
    automations: dict[str, dict[str, Any]] = field(default_factory=ha_automations)
    entries: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"entry_id": "isy-entry", "domain": "isy994", "title": "Example IoX"},
            {"entry_id": "other-entry", "domain": "hue", "title": "Example Hue"},
        ]
    )
    commands: list[dict[str, Any]] = field(default_factory=list)
    related: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """`search/related` answers for an entity or a device, by entity ID or HA device ID."""
    dashboards: list[dict[str, Any]] = field(default_factory=list)
    """`lovelace/dashboards/list`: the dashboards other than the default one."""
    dashboard_configs: dict[str | None, Any] = field(default_factory=dict)
    """`lovelace/config` by url_path (None for the default dashboard). Missing: config_not_found."""
    configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Editable automation, script and scene configs, by entity ID, as HA's config API serves them."""
    refuse_updates: set[str] = field(default_factory=set)
    """Entity IDs whose `config/entity_registry/update` HA refuses."""
    refuse: set[str] = field(default_factory=set)
    """Command types HA answers with an error."""
    controller: Callable[[], frozenset[str]] | None = None
    """The node addresses the Controller has. When set, a reload works as isy994's does: it deletes each
    isy994 entity whose Button the Controller no longer has, and brings back a deleted one whose Button
    returned, with its old ID if that is free (HA's restore on re-add)."""
    deleted: list[dict[str, Any]] = field(default_factory=list)
    """Entities HA deleted, kept to give back their ID and settings if their unique ID returns."""
    extra_states: set[str] = field(default_factory=set)
    """Entity IDs in HA's state machine with no registry entry."""
    reload_drops: set[str] = field(default_factory=set)
    """Entity IDs a reload of the isy994 entry removes: those the Controller no longer has."""
    close_after: int | None = None
    """Drop each connection after this many commands, as an idle-timeout proxy would."""
    connections: int = 0
    """WebSocket sessions authenticated so far."""
    drop_before_reply_to: str | None = None
    """A command type that HA carries out but whose reply is lost: the connection drops first."""

    def writes(self) -> list[dict[str, Any]]:
        """Every command that changes Home Assistant, in order."""
        changing = {
            "config/device_registry/remove_config_entry", "config/entity_registry/update", "config/device_registry/update",
            "call_service", "lovelace/config/save", "rest POST",
        }
        return [c for c in self.commands if c["type"] in changing]

    def _result(self, message: dict[str, Any]) -> dict[str, Any]:
        kind = message["type"]
        if kind == "search/related" and message.get("item_type") in ("entity", "device"):
            return {"success": True, "result": self.related.get(message["item_id"], {})}
        if kind == "lovelace/dashboards/list":
            return {"success": True, "result": self.dashboards}
        if kind == "lovelace/config":
            url_path = message.get("url_path")
            if url_path not in self.dashboard_configs:
                return {"success": False, "error": {"code": "config_not_found", "message": "No config found."}}
            return {"success": True, "result": self.dashboard_configs[url_path]}
        if kind == "config/device_registry/list":
            return {"success": True, "result": self.devices}
        if kind == "config/entity_registry/list":
            return {"success": True, "result": self.entities}
        if kind in ("automation/config", "script/config"):
            config = self.configs.get(message["entity_id"])
            if config is None:
                return {"success": False, "error": {"code": "not_found", "message": "Entity not found"}}
            return {"success": True, "result": {"config": config}}
        if kind == "lovelace/config/save":
            self.dashboard_configs[message.get("url_path")] = message["config"]
            return {"success": True, "result": None}
        if kind in self.refuse:
            return {"success": False, "error": {"code": "home_assistant_error", "message": "refused"}}
        if kind == "call_service":
            if (message["domain"], message["service"]) == ("homeassistant", "reload_config_entry"):
                self._reload()
            return {"success": True, "result": {"context": {}}}
        if kind == "get_states":
            states = [{"entity_id": entity_id, "state": "on", "attributes": {}} for entity_id in sorted(self._states())]
            states += [
                {"entity_id": entity_id, "state": "scening", "attributes": {"id": config["id"]}}
                for entity_id, config in self.configs.items()
                if entity_id.startswith("scene.") and "id" in config
            ]
            states += [
                {"entity_id": entity_id, "state": "on", "attributes": {"friendly_name": automation["name"]}}
                for entity_id, automation in self.automations.items()
            ]
            return {"success": True, "result": states}
        if kind == "search/related":
            automation = self.automations.get(message.get("item_id", "")) if message.get("item_type") == "automation" else None
            return {"success": True, "result": automation["related"] if automation else {}}
        if kind == "config_entries/get":
            domain = message.get("domain")
            return {"success": True, "result": [e for e in self.entries if domain in (None, e["domain"])]}
        if kind == "config/entity_registry/update":
            return self._update_entity(message)
        if kind == "config/device_registry/update":
            device = next((d for d in self.devices if d["id"] == message["device_id"]), None)
            if device is None:
                return {"success": False, "error": {"code": "not_found", "message": "Device not found"}}
            device.update({key: message[key] for key in ("area_id", "name_by_user", "labels") if key in message})
            return {"success": True, "result": device}
        if kind == "config/device_registry/remove_config_entry":
            device = next((d for d in self.devices if d["id"] == message["device_id"]), None)
            if device is None or message["config_entry_id"] not in device["config_entries"]:
                return {"success": False, "error": {"code": "not_found", "message": "Unknown device"}}
            device["config_entries"].remove(message["config_entry_id"])
            if not device["config_entries"]:
                self.devices.remove(device)
                self.entities = [e for e in self.entities if e["device_id"] != device["id"]]
            return {"success": True, "result": {"config_entries": device["config_entries"]}}
        return {"success": False, "error": {"code": "unknown_command", "message": kind}}

    def _states(self) -> set[str]:
        return {e["entity_id"] for e in self.entities if not e.get("disabled_by")} | self.extra_states

    def _reload(self) -> None:
        dropped = [e for e in self.entities if e["entity_id"] in self.reload_drops]
        if self.controller is not None:
            present = self.controller()

            def gone(entity: dict[str, Any]) -> bool:
                address = entity["unique_id"].removeprefix(f"{UUID}_").split("_", 1)[0]
                return entity["platform"] == "isy994" and " " in address and address not in present

            dropped += [e for e in self.entities if gone(e) and e not in dropped]
            for entity in [e for e in self.deleted if not gone(e)]:
                self.deleted.remove(entity)
                taken = {e["entity_id"] for e in self.entities}
                if entity["entity_id"] in taken:
                    entity = {**entity, "entity_id": entity["entity_id"] + "_2"}
                self.entities.append(entity)
        self.entities = [e for e in self.entities if e not in dropped]
        self.deleted += dropped

    def _update_entity(self, message: dict[str, Any]) -> dict[str, Any]:
        """As HA's `config/entity_registry/update`: a new ID must keep its domain and be free.

        A deleted entity's ID is free: HA keeps deleted entities only to give
        their settings back if the same unique ID returns.
        """
        entity = next((e for e in self.entities if e["entity_id"] == message["entity_id"]), None)
        if entity is None:
            return {"success": False, "error": {"code": "not_found", "message": "Entity not found"}}
        if entity["entity_id"] in self.refuse_updates:
            return {"success": False, "error": {"code": "home_assistant_error", "message": "refused"}}
        for key in ("disabled_by", "hidden_by"):
            if message.get(key) not in (None, "user"):
                return {"success": False, "error": {"code": "invalid_format", "message": f"{key} must be user or null"}}
        new_id = message.get("new_entity_id")
        if new_id and new_id != entity["entity_id"]:
            if new_id.split(".", 1)[0] != entity["entity_id"].split(".", 1)[0]:
                return {"success": False, "error": {"code": "invalid_format", "message": "Can't change domain"}}
            if any(e["entity_id"] == new_id for e in self.entities) or new_id in self.extra_states:
                return {"success": False, "error": {"code": "invalid_info", "message": "Entity with this ID is already registered"}}
            entity["entity_id"] = new_id
        keys = ("name", "icon", "area_id", "disabled_by", "hidden_by", "labels", "aliases")
        entity.update({key: message[key] for key in keys if key in message})
        return {"success": True, "result": {"entity_entry": entity}}

    async def _websocket(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        self.connections += 1
        answered = 0
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                break
            command = message.json()
            self.commands.append(command)
            result = self._result(command)
            if command["type"] == self.drop_before_reply_to:
                assert request.transport is not None
                request.transport.close()
                break
            await ws.send_json({"id": command["id"], "type": "result", **result})
            answered += 1
            if self.close_after is not None and answered >= self.close_after:
                assert request.transport is not None
                request.transport.close()  # drop the TCP connection, as a proxy does
                break
        return ws

    async def _config(self, request: web.Request) -> web.StreamResponse:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.Response(status=401)
        kind, key = request.match_info["kind"], request.match_info["key"]
        entity_id = next(
            (
                entity_id
                for entity_id, config in self.configs.items()
                if entity_id.startswith(f"{kind}.")
                and (entity_id.split(".", 1)[1] == key if kind == "script" else config.get("id") == key)
            ),
            None,
        )
        if request.method == "GET":
            if entity_id is None:
                return web.json_response({"message": "Resource not found"}, status=404)
            return web.json_response(self.configs[entity_id])
        body = await request.json()
        self.commands.append({"type": "rest POST", "path": f"config/{kind}/config/{key}", "body": body})
        if entity_id is None:
            return web.json_response({"message": "Resource not found"}, status=404)
        self.configs[entity_id] = body
        return web.json_response({"result": "ok"})

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/websocket", self._websocket)
        app.router.add_route("*", "/api/config/{kind}/config/{key}", self._config)
        return app

    def removed(self) -> list[str]:
        """Device IDs the tool removed, in order."""
        return [c["device_id"] for c in self.commands if c["type"] == "config/device_registry/remove_config_entry"]


# Fixtures. Test modules import the ones they use: a conftest.py here would
# shadow the `conftest` other contexts' tests import from.

@pytest.fixture
def fake_iox() -> FakeIox:
    return FakeIox()


@pytest.fixture
async def iox_url(socket_enabled: None, aiohttp_server: Any, fake_iox: FakeIox) -> str:
    # phcc blocks sockets; the fakes listen on localhost only.
    server = await aiohttp_server(fake_iox.app())
    return str(server.make_url("")).rstrip("/")


@pytest.fixture
def fake_ssh() -> FakeEisySsh:
    return FakeEisySsh()


@pytest.fixture
async def eisy_ssh(socket_enabled: None, fake_ssh: FakeEisySsh, tmp_path: Path) -> Any:
    """The fake SSH login on localhost, and a known_hosts file that trusts its host key."""
    key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        fake_ssh.server, "127.0.0.1", 0, server_host_keys=[key], process_factory=fake_ssh.process, encoding=None
    )
    port = server.sockets[0].getsockname()[1]
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {key.export_public_key().decode()}")
    yield SshEndpoint(port, known_hosts)
    server.close()
    await server.wait_closed()


@pytest.fixture
def fake_ha() -> FakeHa:
    return FakeHa()


@pytest.fixture
async def ha_url(socket_enabled: None, aiohttp_server: Any, fake_ha: FakeHa) -> str:
    server = await aiohttp_server(fake_ha.app())
    return str(server.make_url("")).rstrip("/")
