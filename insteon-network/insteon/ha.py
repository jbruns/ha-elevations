"""Home Assistant over its WebSocket API, and its REST config API.

Reads: registries, search, dashboards and the config of automations, scripts
and scenes. Writes: removing a device, reloading the isy994 integration, and
saving an automation, script, scene or dashboard config through HA's own
config API (never `.storage`). The tool Reconciles Home Assistant to the
Controller, never the other way round.
"""

from __future__ import annotations

import itertools
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import aiohttp

DOMAIN = "isy994"


class HaError(RuntimeError):
    """Home Assistant refused a call or could not be reached."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        """The error code Home Assistant answered with, such as `config_not_found`."""


def controller_suffix(identifier: str, uuid: str) -> str | None:
    """What an isy994 ID names on the Controller with this uuid.

    isy994 writes device identifiers and entity unique IDs as
    `<uuid>_<suffix>`: a Button address (`AA B C 1`, perhaps with its own
    `_OL`-style suffix), a Scene address, or `variables` / `network`. None
    when the ID belongs to another Controller.
    """
    prefix = f"{uuid}_"
    return identifier[len(prefix) :] if identifier.startswith(prefix) else None


@dataclass(frozen=True)
class HaRegistry:
    """The parts of Home Assistant's registries the tool reads, as returned by HA."""

    devices: Sequence[dict[str, Any]]
    entities: Sequence[dict[str, Any]]
    config_entries: Sequence[dict[str, Any]]
    """The Controller integration's config entries."""

    @property
    def isy994_entries(self) -> frozenset[str]:
        return frozenset(entry["entry_id"] for entry in self.config_entries)

    def isy994_devices(self) -> list[dict[str, Any]]:
        """Devices with an isy994 identifier."""
        return [device for device in self.devices if self.isy994_identifiers(device)]

    @staticmethod
    def isy994_identifiers(device: dict[str, Any]) -> list[str]:
        return [value for domain, value in device.get("identifiers", []) if domain == DOMAIN]

    def isy994_entries_of(self, device: dict[str, Any]) -> tuple[str, ...]:
        """The isy994 config entries a device belongs to."""
        entries = self.isy994_entries
        return tuple(entry for entry in device.get("config_entries", []) if entry in entries)

    def isy994_entity(self, entity_id: str) -> dict[str, Any] | None:
        return next(
            (e for e in self.entities if e.get("entity_id") == entity_id and e.get("platform") == DOMAIN),
            None,
        )

    def device(self, device_id: str | None) -> dict[str, Any] | None:
        return next((device for device in self.devices if device.get("id") == device_id), None)

    def entities_of(self, device_id: str) -> list[dict[str, Any]]:
        return [entity for entity in self.entities if entity.get("device_id") == device_id]


@dataclass(frozen=True)
class Automation:
    """One Home Assistant automation and what it references, as HA's search finds it."""

    entity_id: str
    name: str
    entities: frozenset[str]
    devices: frozenset[str]
    """HA device IDs, as in device triggers and targets."""


class HaClient:
    """One authenticated WebSocket session. Use as an async context manager."""

    def __init__(self, url: str, token: str, *, ipv4_only: bool = False, timeout: float = 30) -> None:
        base = url.strip().rstrip("/")
        self._url = "ws" + base[len("http") :] + "/api/websocket" if base.startswith("http") else base
        self._base = base if base.startswith("http") else "http" + base[len("ws") :].removesuffix("/api/websocket")
        self._token = token.strip()
        self._family = socket.AF_INET if ipv4_only else 0
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def __aenter__(self) -> HaClient:
        self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=self._family))
        try:
            self._ws = await self._session.ws_connect(self._url, timeout=aiohttp.ClientWSTimeout(ws_close=10))
            await self._receive()  # auth_required
            await self._ws.send_json({"type": "auth", "access_token": self._token})
            reply = await self._receive()
            if reply.get("type") != "auth_ok":
                raise HaError("Home Assistant refused the access token")
        except (aiohttp.ClientError, TimeoutError) as error:
            await self.__aexit__(None, None, None)
            raise HaError(f"cannot reach Home Assistant: {error}") from error
        except HaError:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
        self._ws = self._session = None

    @property
    def _socket(self) -> aiohttp.ClientWebSocketResponse:
        if self._ws is None:
            raise RuntimeError("HaClient must be used as an async context manager")
        return self._ws

    async def _receive(self) -> dict[str, Any]:
        try:
            message = await self._socket.receive_json(timeout=self._timeout)
        except TypeError as error:  # a close frame instead of JSON
            raise HaError("Home Assistant closed the connection") from error
        if not isinstance(message, dict):
            raise HaError(f"unexpected message from Home Assistant: {message!r}")
        return message

    async def call(self, kind: str, **payload: Any) -> Any:
        """Send one command and return its result. Raises HaError if it fails."""
        message_id = next(self._ids)
        await self._socket.send_json({"id": message_id, "type": kind, **payload})
        while True:
            reply = await self._receive()
            if reply.get("id") == message_id and reply.get("type") == "result":
                break
        if not reply.get("success"):
            error = reply.get("error") or {}
            code = error.get("code", "error")
            raise HaError(f"{kind}: {code}: {error.get('message', '')}".rstrip(": "), code=code)
        return reply.get("result")

    async def registry(self) -> HaRegistry:
        """Devices, entities and the isy994 config entries, read in one go."""
        devices = await self.call("config/device_registry/list")
        entities = await self.call("config/entity_registry/list")
        entries = await self.call("config_entries/get", domain=DOMAIN)
        return HaRegistry(devices=devices, entities=entities, config_entries=entries)

    async def automations(self) -> list[Automation]:
        """Every automation, with the entities and devices HA's `search/related` finds in it."""
        automations = []
        for state in await self.call("get_states"):
            entity_id = state.get("entity_id", "")
            if not entity_id.startswith("automation."):
                continue
            related = await self.call("search/related", item_type="automation", item_id=entity_id)
            automations.append(
                Automation(
                    entity_id=entity_id,
                    name=state.get("attributes", {}).get("friendly_name") or entity_id,
                    entities=frozenset(related.get("entity", [])) - {entity_id},
                    devices=frozenset(related.get("device", [])),
                )
            )
        return sorted(automations, key=lambda automation: automation.entity_id)


    async def related(self, item_id: str, *, item_type: str = "entity") -> dict[str, list[str]]:
        """What HA's own search finds related to an entity (or a device): automations, scripts, groups and so on."""
        return await self.call("search/related", item_type=item_type, item_id=item_id) or {}

    async def config_entries(self) -> list[dict[str, Any]]:
        """Every config entry: domain, title and state, but not its options."""
        return await self.call("config_entries/get")

    async def dashboards(self) -> list[dict[str, Any]]:
        """The Lovelace dashboards other than the default one."""
        return await self.call("lovelace/dashboards/list")

    async def dashboard_config(self, url_path: str | None) -> Any:
        """A dashboard's config, storage or YAML; `url_path` None is the default dashboard."""
        return await self.call("lovelace/config", **({} if url_path is None else {"url_path": url_path}))

    async def rest(self, method: str, path: str, body: Any = None) -> Any:
        """One call to HA's REST API (`/api/<path>`), as JSON."""
        url = f"{self._base}/api/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token}"}
        session = self._session
        if session is None:
            raise RuntimeError("HaClient must be used as an async context manager")
        try:
            async with session.request(
                method, url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=self._timeout)
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise HaError(f"{method} /api/{path}: HTTP {response.status}: {text[:200]}")
                return await response.json(content_type=None) if text else None
        except aiohttp.ClientError as error:
            raise HaError(f"{method} /api/{path}: {error}") from error

    async def item_config(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        """The config of an automation, script or scene, as HA's editor reads it, or None.

        None means HA holds no editable config for it, as for one defined in
        YAML without an `id`.
        """
        if kind in ("automation", "script"):
            try:
                found = await self.call(f"{kind}/config", entity_id=entity_id)
            except HaError as error:
                if error.code == "not_found":
                    return None
                raise
            return (found or {}).get("config")
        state = await self.state(entity_id)
        scene_id = (state or {}).get("attributes", {}).get("id")
        if scene_id is None:
            return None
        return await self.rest("GET", f"config/scene/config/{scene_id}")

    async def save_item_config(self, kind: str, entity_id: str, config: dict[str, Any]) -> None:
        """Save an automation, script or scene through HA's config API, which reloads it."""
        key = entity_id.split(".", 1)[1] if kind == "script" else config["id"]
        await self.rest("POST", f"config/{kind}/config/{key}", config)

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        return next((s for s in await self.call("get_states") if s.get("entity_id") == entity_id), None)

    async def save_dashboard_config(self, url_path: str | None, config: Any) -> None:
        """Save a storage-mode dashboard's config; `url_path` None is the default dashboard."""
        await self.call("lovelace/config/save", config=config, **({} if url_path is None else {"url_path": url_path}))

    async def reload_entry(self, entry_id: str) -> None:
        """Reload a config entry. For isy994 this removes the entities the Controller no longer has."""
        await self.call(
            "call_service", domain="homeassistant", service="reload_config_entry", service_data={"entry_id": entry_id}
        )

    async def remove_config_entry(self, device_id: str, config_entry_id: str) -> None:
        """Detach a device from a config entry; HA deletes a device left with none."""
        await self.call(
            "config/device_registry/remove_config_entry",
            device_id=device_id,
            config_entry_id=config_entry_id,
        )
