"""The rails every changing command runs on: dry run, the lock, and backups.

A command that changes something only plans and prints unless it was given
`--apply`. When it applies, it first takes the lock, so only one command (and
so one Modem operation) runs at a time, then backs up each system it is about
to change, and only then changes anything.

What a backup holds:

- IoX (`IoxBackup`): all of `/var/isy/FILES/CONF` as one tar, read over SSH as
  root, saved as `<private>/eisy/backups/<time>.tar`. If it cannot be taken,
  the command stops before changing anything.
- Home Assistant registries (`HaRegistryBackup`): the device registry, entity
  registry and isy994 config entries as JSON. Enough to see what a removal took
  away and re-create it by hand; it is not a Home Assistant backup and cannot be
  restored automatically.
- Home Assistant config: a command that edits automations, scripts, scenes or
  dashboards (`remove --clean`) saves each one's config as read just before
  editing it, in the backup folder, in the shape HA's config API takes back.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from insteon.ha import HaClient
from insteon.iox import IoxFiles, Write
from insteon.network import Device


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
    """Saves what one system holds before a command changes it.

    `folder` is this run's backup folder and `stamp` its time, as in its name.
    """

    name: str

    async def take(self, folder: Path, stamp: str) -> None: ...


@dataclass
class IoxBackup:
    """All of IoX's config (`/var/isy/FILES/CONF`), as a tar in `root`."""

    files: IoxFiles
    root: Path
    name: str = "IoX"
    path: Path | None = None
    """Where the archive went, once taken."""

    async def take(self, folder: Path, stamp: str) -> None:
        archive = await self.files.conf_archive()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{stamp}.tar"
        partial = path.with_suffix(".tar.partial")
        partial.write_bytes(archive)
        partial.rename(path)
        self.path = path


@dataclass
class HaRegistryBackup:
    ha: HaClient
    name: str = "Home Assistant registries"

    async def take(self, folder: Path, stamp: str) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        registry = await self.ha.registry()
        snapshot = {
            "devices": registry.devices,
            "entities": registry.entities,
            "config-entries": registry.config_entries,
        }
        for key, value in snapshot.items():
            (folder / f"ha-{key}.json").write_text(json.dumps(value, indent=2) + "\n")


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
                await backup.take(folder, stamp)
            yield folder


def hold_set(who: str, prompt: Callable[[str], str], *, other: str = "type s to skip it") -> str:
    """Ask the Administrator to put a Battery Device (`who`) in linking mode. Returns the answer, lowercased.

    An empty answer means the Device is in linking mode. Only one Battery
    Device may be in linking mode at a time, or they link to each other.
    """
    answer = prompt(
        f"{who} is a Battery Device. Hold its Set button until it enters "
        f"linking mode, then press Enter ({other}): "
    )
    return answer.strip().lower()


@dataclass(frozen=True)
class DeviceWrite:
    """How one write to a Device went."""

    device: Device
    written: bool
    pending: bool = False
    """IoX still holds Pending Writes for the Device afterwards."""
    skipped: str | None = None


async def write_device(
    device: Device, write: Callable[[], Awaitable[Write]], prompt: Callable[[str], str]
) -> DeviceWrite:
    """Run one write that IoX sends to `device`, prompting first for a Battery Device.

    The write itself waits until IoX is no longer busy; then IoX's last word
    on the Device's Pending Writes (`_3` WH or WD) tells whether they cleared.
    """
    if device.battery and hold_set(f"{device.address} {device.name}", prompt).startswith("s"):
        return DeviceWrite(device, written=False, pending=True, skipped="a Battery Device; skipped at the prompt")
    result = await write()
    return DeviceWrite(device, written=True, pending=result.pending(device.address))
