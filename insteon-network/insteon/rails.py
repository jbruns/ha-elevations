"""The rails every changing command runs on: dry run, the lock, and backups.

A command that changes something only plans and prints unless it was given
`--apply`. When it applies, it first takes the lock, so only one command (and
so one Modem operation) runs at a time, then backs up each system it is about
to change, and only then changes anything.

What a backup holds today:

- Home Assistant: the device registry, entity registry and isy994 config
  entries as JSON. Enough to see what a removal took away and re-create it by
  hand; it is not a Home Assistant backup and cannot be restored automatically.
- IoX: `/rest/nodes` and `/rest/config` as returned. The full IoX backup over
  SSH (`/var/isy/FILES/CONF/`) is not built yet; a command that changes IoX
  must not ship until it is.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from insteon.ha import DOMAIN, HaClient
from insteon.iox import IoxClient


class LockHeld(RuntimeError):
    """Another insteon command is already changing something."""


@contextlib.contextmanager
def lock(path: Path, command: str) -> Iterator[None]:
    """Hold the tool's lock for the duration, or raise LockHeld at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.seek(0)
            holder = handle.read().strip() or "another process"
            raise LockHeld(f"another insteon command holds {path}: {holder}") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"insteon {command} (pid {os.getpid()})\n")
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle, fcntl.LOCK_UN)


class Backup(Protocol):
    """Saves what one system holds before a command changes it."""

    name: str

    async def take(self, folder: Path) -> None: ...


@dataclass
class HaRegistryBackup:
    ha: HaClient
    name: str = "Home Assistant registries"

    async def take(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "devices": await self.ha.call("config/device_registry/list"),
            "entities": await self.ha.call("config/entity_registry/list"),
            "config_entries": await self.ha.call("config_entries/get", domain=DOMAIN),
        }
        for key, value in snapshot.items():
            (folder / f"ha-{key.replace('_', '-')}.json").write_text(json.dumps(value, indent=2) + "\n")


@dataclass
class IoxRestBackup:
    iox: IoxClient
    name: str = "IoX nodes and config (REST)"

    async def take(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        for path in ("nodes", "config"):
            (folder / f"iox-{path}.xml").write_text(await self.iox.get(path))


@dataclass(frozen=True)
class Rails:
    apply: bool
    lock_path: Path
    backup_root: Path

    def modem_operation(self, command: str) -> contextlib.AbstractContextManager[None]:
        """The lock alone, for a read that still occupies the Modem, such as a Link table read."""
        return lock(self.lock_path, command)

    @contextlib.asynccontextmanager
    async def changing(self, command: str, backups: Sequence[Backup]) -> AsyncIterator[Path]:
        """Lock, back up, then let the command change things. Yields the backup folder."""
        if not self.apply:
            raise RuntimeError(f"{command} tried to change something in a dry run")
        with lock(self.lock_path, command):
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            folder = self.backup_root / f"{stamp}-{command}"
            for backup in backups:
                await backup.take(folder)
            yield folder
