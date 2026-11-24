"""A fake IoX and a fake Home Assistant, served on localhost from made-up data.

Every name, address and uuid here is a placeholder. Real responses stay in the
gitignored `.private/` folder (ADR 0004).
"""

from __future__ import annotations

import asyncio
import base64
import io
import tarfile
import xml.etree.ElementTree as ET
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
# The one command the tool may run on the Controller: a read of the D2D folder.
D2D_TAR = "sudo -S -p '' tar -cf - -C /var/isy/FILES/CONF D2D"
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


@dataclass
class FakeIox:
    nodes_xml: str = field(default_factory=lambda: (FIXTURES / "nodes.xml").read_text())
    config_xml: str = field(default_factory=lambda: (FIXTURES / "config.xml").read_text())
    programs_xml: str = field(default_factory=lambda: (FIXTURES / "programs.xml").read_text())
    plm_info: str = f"{MODEM} v9B / Connected"
    plm_records: list[str] = field(default_factory=list)
    soap_calls: list[dict[str, Any]] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)
    _sockets: list[web.WebSocketResponse] = field(default_factory=list)

    def _authorised(self, request: web.Request) -> bool:
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return request.headers.get("Authorization") == expected

    async def _rest(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            return web.Response(status=401)
        path = request.match_info["path"]
        self.gets.append(path)
        body = {
            "nodes": self.nodes_xml,
            "config": self.config_xml,
            "programs": self.programs_xml,
            **{f"vars/{kind}/{n}": (FIXTURES / f"vars-{kind}-{n}.xml").read_text() for kind in ("definitions", "get") for n in (1, 2)},
        }.get(path)
        if body is None:
            return web.Response(status=404)
        return web.Response(text=body, content_type="text/xml")

    async def _subscribe(self, request: web.Request) -> web.StreamResponse:
        if not self._authorised(request):
            return web.Response(status=401)
        ws = web.WebSocketResponse(protocols=("ISYSUB",), autoping=False)
        await ws.prepare(request)
        await ws.send_str('<?xml version="1.0" encoding="UTF-8"?><SubscriptionResponse><SID>uuid:1</SID></SubscriptionResponse>')
        await ws.send_str(_event("ST", "0", "AA BB CC 1"))
        self._sockets.append(ws)
        async for message in ws:
            if message.type == WSMsgType.CLOSE:
                break
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
        return web.Response(
            text='<?xml version="1.0" encoding="UTF-8"?><s:Envelope><s:Body><UDIDefaultResponse><status>200</status></UDIDefaultResponse></s:Body></s:Envelope>',
            content_type="text/xml",
        )

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
    """The Controller's SSH login. It answers only the D2D read, and only when
    sudo's password arrives on stdin."""

    files: dict[str, bytes] = field(
        default_factory=lambda: {f"D2D/{path.name}": path.read_bytes() for path in sorted((FIXTURES / "programs").iterdir())}
    )
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
        if command != D2D_TAR:
            process.stderr.write(b"sh: not allowed in the fake\n")
            process.exit(127)
            return
        line = (await process.stdin.readline()).decode().rstrip("\n")
        self.sudo_passwords.append(line)
        if line != SSH_PASSWORD:
            process.stderr.write(b"Sorry, try again.\nsudo: 1 incorrect password attempt\n")
            process.exit(1)
            return
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

    def _result(self, message: dict[str, Any]) -> dict[str, Any]:
        kind = message["type"]
        if kind == "config/device_registry/list":
            return {"success": True, "result": self.devices}
        if kind == "config/entity_registry/list":
            return {"success": True, "result": self.entities}
        if kind == "get_states":
            states = [{"entity_id": "light.example_dimmer", "state": "on", "attributes": {}}]
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
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                break
            command = message.json()
            self.commands.append(command)
            await ws.send_json({"id": command["id"], "type": "result", **self._result(command)})
        return ws

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/websocket", self._websocket)
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
