"""`insteon restore DEVICE|modem`: Restore Links from the Controller's database.

A Device's Restore rewrites its Link table (`RestoreDevice`); the Modem's
rewrites the Modem's (`ReplaceModem`), which takes minutes for a full table.
A dry run unless given `--apply`. Applying takes the lock and the IoX backup
first. A Battery Device is written only while the Administrator holds its Set
button.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from insteon.ha import HaRegistry
from insteon.iox import IoxClient
from insteon.network import Device
from insteon.rails import DeviceWrite, IoxBackup, Rails, write_device
from insteon.resolve import resolve_device

MODEM = "modem"


@dataclass
class RestoreResult:
    device: Device | None
    """The Device, or None for the Modem."""
    applied: bool = False
    write: DeviceWrite | None = None
    iox_backup: Path | None = None


async def restore(
    iox: IoxClient,
    rails: Rails,
    iox_backup: IoxBackup,
    name: str,
    *,
    registry: HaRegistry | None = None,
    prompt: Callable[[str], str] = input,
) -> RestoreResult:
    network = await iox.network()
    device = None if name.strip().lower() == MODEM else resolve_device(name, network, registry)
    result = RestoreResult(device)
    if not rails.apply:
        return result
    async with rails.changing("restore", [iox_backup]):
        result.iox_backup = iox_backup.path
        if device is None:
            await iox.restore_modem()
        else:
            result.write = await write_device(
                iox, device, lambda clear: iox.restore_device(device.primary, until_clear=clear), prompt
            )
        result.applied = True
    return result


def render(result: RestoreResult) -> str:
    device = result.device
    what = "the Modem's Links" if device is None else f"the Links of {device.label}"
    if not result.applied:
        lines = [f"Would Restore {what} from the Controller's database."]
        if device is not None and device.battery:
            lines.append("  A Battery Device: you will be asked to hold its Set button first.")
        lines.append("Dry run: nothing changed. Pass --apply to Restore.")
        return "\n".join(lines) + "\n"
    lines = []
    write = result.write
    if write is not None and not write.written:
        lines.append(f"Skipped at the prompt: nothing written. {what[0].upper()}{what[1:]} still need a Restore.")
    else:
        lines.append(f"Restored {what}.")
    if write is not None and write.written and write.outcome:
        lines.append(f"{write.device.label}: {write.outcome}.")
    if result.iox_backup is not None:
        lines.append(f"IoX backup: {result.iox_backup}")
    lines.append("Run `insteon audit` on it to confirm.")
    return "\n".join(lines) + "\n"
