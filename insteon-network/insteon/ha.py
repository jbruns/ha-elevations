"""Home Assistant over its WebSocket API: registry reads and device removal.

The tool Reconciles Home Assistant to the Controller, never the other way round.
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


@dataclass(frozen=True)
class HaRegistry:
    """The parts of Home Assistant's registries the tool reads, as returned by HA."""

    devices: Sequence[dict[str, Any]]
    entities: Sequence[dict[str, Any]]
    isy994_entries: frozenset[str]
    """The entry IDs of the Controller integration's config entries."""

    def isy994_devices(self) -> list[dict[str, Any]]:
        """Devices with an isy994 identifier."""
        return [
            device
            for device in self.devices
            if any(domain == DOMAIN for domain, _ in device.get("identifiers", []))
        ]

    def entities_of(self, device_id: str) -> list[dict[str, Any]]:
        return [entity for entity in self.entities if entity.get("device_id") == device_id]


class HaClient:
    """One authenticated WebSocket session. Use as an async context manager."""

    def __init__(self, url: str, token: str, *, ipv4_only: bool = False, timeout: float = 30) -> None:
        base = url.strip().rstrip("/")
        self._url = "ws" + base[len("http") :] + "/api/websocket" if base.startswith("http") else base
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
        except (aiohttp.ClientError, TimeoutError) as error:
            await self._session.close()
            raise HaError(f"cannot reach Home Assistant: {error}") from error
        if reply.get("type") != "auth_ok":
            await self.__aexit__(None, None, None)
            raise HaError("Home Assistant refused the access token")
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
        self._ws = self._session = None

    async def _receive(self) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("HaClient must be used as an async context manager")
        try:
            message = await self._ws.receive_json(timeout=self._timeout)
        except TypeError as error:  # a close frame instead of JSON
            raise HaError("Home Assistant closed the connection") from error
        if not isinstance(message, dict):
            raise HaError(f"unexpected message from Home Assistant: {message!r}")
        return message

    async def call(self, kind: str, **payload: Any) -> Any:
        """Send one command and return its result. Raises HaError if it fails."""
        if self._ws is None:
            raise RuntimeError("HaClient must be used as an async context manager")
        message_id = next(self._ids)
        await self._ws.send_json({"id": message_id, "type": kind, **payload})
        while True:
            reply = await self._receive()
            if reply.get("id") == message_id and reply.get("type") == "result":
                break
        if not reply.get("success"):
            error = reply.get("error") or {}
            raise HaError(f"{kind}: {error.get('code', 'error')}: {error.get('message', '')}".rstrip(": "))
        return reply.get("result")

    async def registry(self) -> HaRegistry:
        """Devices, entities and the isy994 config entries, read in one go."""
        devices = await self.call("config/device_registry/list")
        entities = await self.call("config/entity_registry/list")
        entries = await self.call("config_entries/get", domain=DOMAIN)
        return HaRegistry(
            devices=devices,
            entities=entities,
            isy994_entries=frozenset(entry["entry_id"] for entry in entries),
        )

    async def remove_config_entry(self, device_id: str, config_entry_id: str) -> None:
        """Detach a device from a config entry; HA deletes a device left with none."""
        await self.call(
            "config/device_registry/remove_config_entry",
            device_id=device_id,
            config_entry_id=config_entry_id,
        )
