"""`insteon add ADDRESS NAME`: add a Device to the Controller, then Reconcile HA.

By default IoX erases the Device's Links and writes its own (`AddNode` flag 1),
so nothing from the Device's past life survives. `--from-links` instead keeps
its Links and has IoX add the Devices they name and build Scenes from them
(flag 3). A dry run unless given `--apply`. Applying takes the lock and backs
up IoX and HA's registries first. For a Battery Device (`--battery`), the
Administrator holds its Set button before IoX adds it. Afterwards HA reloads
the isy994 integration, and the new entity IDs are printed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from insteon.address import InsteonAddress
from insteon.ha import HaClient
from insteon.iox import IoxClient, IoxError
from insteon.network import Device, Network
from insteon.prune import PruneResult, reconcile
from insteon.rails import DeviceWrite, HaRegistryBackup, IoxBackup, Rails, confirm_write, hold_set
from insteon.resolve import ResolveError, ha_entity_ids

ERASE_LINKS, FROM_LINKS = 1, 3


@dataclass
class AddResult:
    address: InsteonAddress
    name: str
    from_links: bool
    battery: bool
    applied: bool = False
    skipped: bool = False
    device: Device | None = None
    write: DeviceWrite | None = None
    reconciled: PruneResult | None = None
    entity_ids: tuple[str, ...] = ()
    iox_backup: Path | None = None
    backup: Path | None = None


async def add(
    iox: IoxClient,
    ha: HaClient,
    rails: Rails,
    iox_backup: IoxBackup,
    address_text: str,
    name: str,
    *,
    from_links: bool = False,
    battery: bool = False,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> AddResult:
    address = InsteonAddress.try_parse(address_text)
    if address is None:
        raise ResolveError(f"{address_text!r} is not an Insteon Address")
    if not name.strip():
        raise ResolveError("a Device needs a name")
    result = AddResult(address, name, from_links, battery)
    _check_new(await iox.network(), address)
    if not rails.apply:
        return result

    async with rails.changing("add", [iox_backup, HaRegistryBackup(ha)]) as folder:
        result.iox_backup, result.backup = iox_backup.path, folder
        _check_new(await iox.network(), address)  # again, under the lock
        if battery:
            if hold_set(f"{address} {name}", prompt, other="type s to stop").startswith("s"):
                result.skipped = True
                return result
        progress(f"Adding {address} {name}" + (", building Scenes from its Links" if from_links else ", erasing its Links"))
        write = await iox.add_device(address, name, flag=FROM_LINKS if from_links else ERASE_LINKS, until_clear=battery)
        network = await iox.network()
        result.device = network.device(address)
        if result.device is None:
            raise IoxError(
                f"IoX did not add {address}. Check it is powered and in range; a new Device may need its Set "
                "button held (pass --battery to be prompted)"
            )
        result.write = await confirm_write(iox, result.device, write)
        progress("Reconciling HA: reloading the isy994 integration, then pruning")
        result.reconciled = await reconcile(iox, ha)
        result.entity_ids = ha_entity_ids(result.device, network, await ha.registry())
        result.applied = True
    return result


def _check_new(network: Network, address: InsteonAddress) -> None:
    existing = network.device(address)
    if existing is not None:
        raise ResolveError(f"the Controller already has {address}: {existing.name}")


def render(result: AddResult) -> str:
    how = "keeping its Links and building Scenes from them" if result.from_links else "erasing its Links"
    if not result.applied and not result.skipped:
        lines = [f"Would add {result.address} {result.name}, {how}."]
        if result.battery:
            lines.append("  A Battery Device: you will be asked to hold its Set button first.")
        lines.append("Then Reconcile HA: reload the isy994 integration, and list the new entities.")
        lines.append("Dry run: nothing changed. Pass --apply to add it.")
        return "\n".join(lines) + "\n"
    if result.skipped:
        return f"Stopped at the prompt: {result.address} was not added.\n"
    device = result.device
    assert device is not None
    lines = [f"Added {device.label} ({device.model}, {len(device.buttons)} Buttons), {how}."]
    if result.write is not None and result.write.outcome:
        lines.append(f"  {result.write.outcome}.")
    elif device.battery and not result.battery:
        lines.append(
            "  It is a Battery Device, so IoX may hold Pending Writes for it: hold its Set button and run "
            "`insteon restore` on it."
        )
    lines.append("Its HA entities: " + (", ".join(result.entity_ids) or "none yet; check the isy994 integration"))
    if result.iox_backup is not None:
        lines.append(f"IoX backup: {result.iox_backup}")
    return "\n".join(lines) + "\n"
