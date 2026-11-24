"""`insteon prune`: remove Stale HA Devices, and the Reconcile of HA.

A Stale HA Device is an isy994 device in Home Assistant that matches no Device
the Controller has. isy994's own removal check is broken (it allows removing
any isy994 device, live ones included), so the tool decides for itself, from
the Controller's `/rest/nodes`, what is stale.

`reconcile` is what a command that changed the Controller runs last: reload the
isy994 integration, which removes the entities the Controller no longer has
and adds new ones, then remove the Stale HA Devices that leaves
(research/pyisy-and-ha-isy994.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from insteon.address import ButtonAddress
from insteon.ha import HaClient, HaRegistry, controller_suffix
from insteon.iox import IoxClient
from insteon.network import Network
from insteon.rails import HaRegistryBackup, Rails

# isy994's service devices: `<uuid>_variables` and `<uuid>_network`.
_SERVICE_DEVICES = {"variables", "network"}


@dataclass(frozen=True)
class StaleHaDevice:
    device_id: str
    name: str
    identifier: str
    reason: str
    config_entries: tuple[str, ...]
    """The isy994 config entries to detach it from."""
    entity_ids: tuple[str, ...]

    @property
    def removable(self) -> bool:
        """No entity uses it, and it is in an isy994 config entry to detach it from."""
        return not self.entity_ids and bool(self.config_entries)


@dataclass
class PruneResult:
    stale: list[StaleHaDevice]
    """Every Stale HA Device, removable or held."""
    removed: list[StaleHaDevice] = field(default_factory=list)
    backup: Path | None = None

    @property
    def held(self) -> list[StaleHaDevice]:
        """Stale HA Devices prune will not remove: entities still use them, or they
        are in no isy994 config entry to detach them from."""
        return [device for device in self.stale if not device.removable]

    @property
    def removable(self) -> list[StaleHaDevice]:
        return [device for device in self.stale if device.removable]


def find_stale(network: Network, registry: HaRegistry) -> list[StaleHaDevice]:
    """Every isy994 device none of whose identifiers names something the Controller has."""
    stale = []
    for device in registry.isy994_devices():
        identifiers = registry.isy994_identifiers(device)
        verdicts = [_staleness(identifier, network) for identifier in identifiers]
        if any(verdict is None for verdict in verdicts):
            continue
        stale.append(
            StaleHaDevice(
                device_id=device["id"],
                name=device.get("name_by_user") or device.get("name") or "",
                identifier=identifiers[0],
                reason="; ".join(verdict for verdict in verdicts if verdict),
                config_entries=registry.isy994_entries_of(device),
                entity_ids=tuple(entity["entity_id"] for entity in registry.entities_of(device["id"])),
            )
        )
    return stale


def _staleness(identifier: str, network: Network) -> str | None:
    """Why the identifier is stale, or None when the Controller has what it names."""
    if identifier == network.uuid:
        return None
    suffix = controller_suffix(identifier, network.uuid)
    if suffix is None:
        other = identifier.split("_", 1)[0]
        return f"from another Controller ({other})"
    if suffix in _SERVICE_DEVICES or suffix in network.node_addresses:
        return None
    button = ButtonAddress.try_parse(suffix)
    if button is not None:
        if network.device_of(button) is not None:
            return None
        return f"the Controller has no Device at {button.device}"
    return f"the Controller has no node {suffix!r}"


async def prune(iox: IoxClient, ha: HaClient, rails: Rails) -> PruneResult:
    """Find Stale HA Devices; with `--apply`, remove the ones no entity uses."""
    result = PruneResult(find_stale(await iox.network(), await ha.registry()))
    if not rails.apply or not result.removable:
        return result
    async with rails.changing("prune", [HaRegistryBackup(ha)]) as backup:
        result = await remove_stale(iox, ha)
        result.backup = backup
    return result


async def remove_stale(iox: IoxClient, ha: HaClient) -> PruneResult:
    """Remove the Stale HA Devices no entity uses. The caller holds the lock and has backed up.

    Both sides are read again here: they may have changed since any plan.
    """
    result = PruneResult(find_stale(await iox.network(), await ha.registry()))
    for device in result.removable:
        for entry in device.config_entries:
            await ha.remove_config_entry(device.device_id, entry)
        result.removed.append(device)
    return result


async def reconcile(iox: IoxClient, ha: HaClient) -> PruneResult:
    """Reload the isy994 integration, then remove the Stale HA Devices. The caller holds the lock."""
    for entry in sorted((await ha.registry()).isy994_entries):
        await ha.reload_entry(entry)
    return await remove_stale(iox, ha)


def render(result: PruneResult, *, applying: bool) -> str:
    stale, removable, held = result.stale, result.removable, result.held
    if not stale:
        return "No Stale HA Devices.\n"
    lines = [f"Stale HA Devices: {len(stale)} ({len(removable)} removable, {len(held)} held)"]
    for device in stale:
        lines.append(f"  {device.device_id}  {device.name}  {device.identifier}")
        lines.append(f"    {device.reason}")
        if device.entity_ids:
            count = len(device.entity_ids)
            lines.append(
                f"    held: {count} {'entity still uses' if count == 1 else 'entities still use'} it "
                f"({', '.join(device.entity_ids)}); reload the isy994 integration, then prune again"
            )
        elif not device.config_entries:
            lines.append("    held: not in an isy994 config entry; remove it by hand")
    if not applying:
        lines.append(
            f"Dry run: nothing removed. Pass --apply to remove the {len(removable)} removable devices."
            if removable
            else "Nothing to remove."
        )
    else:
        lines.append(f"Removed {len(result.removed)} Stale HA Devices.")
        if result.backup is not None:
            lines.append(f"Backup: {result.backup}")
    return "\n".join(lines) + "\n"
