"""`insteon prune`: remove Stale HA Devices.

A Stale HA Device is an isy994 device in Home Assistant that matches no Device
the Controller has. isy994's own removal check is broken (it allows removing
any isy994 device, live ones included), so the tool decides for itself, from
the Controller's `/rest/nodes`, what is stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from insteon.address import NodeAddress
from insteon.ha import DOMAIN, HaClient, HaRegistry
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


@dataclass
class PruneResult:
    stale: list[StaleHaDevice]
    """Every Stale HA Device, removable or held."""
    removed: list[StaleHaDevice] = field(default_factory=list)
    backup: Path | None = None

    @property
    def held(self) -> list[StaleHaDevice]:
        """Stale HA Devices prune will not remove, because entities still use them."""
        return [device for device in self.stale if device.entity_ids or not device.config_entries]

    @property
    def removable(self) -> list[StaleHaDevice]:
        return [device for device in self.stale if not device.entity_ids and device.config_entries]


def find_stale(network: Network, registry: HaRegistry) -> list[StaleHaDevice]:
    """Every isy994 device none of whose identifiers names something the Controller has."""
    stale = []
    for device in registry.isy994_devices():
        identifiers = [value for domain, value in device.get("identifiers", []) if domain == DOMAIN]
        verdicts = [_staleness(identifier, network) for identifier in identifiers]
        if any(verdict is None for verdict in verdicts):
            continue
        stale.append(
            StaleHaDevice(
                device_id=device["id"],
                name=device.get("name_by_user") or device.get("name") or "",
                identifier=identifiers[0],
                reason="; ".join(verdict for verdict in verdicts if verdict),
                config_entries=tuple(
                    entry for entry in device.get("config_entries", []) if entry in registry.isy994_entries
                ),
                entity_ids=tuple(entity["entity_id"] for entity in registry.entities_of(device["id"])),
            )
        )
    return stale


def _staleness(identifier: str, network: Network) -> str | None:
    """Why the identifier is stale, or None when the Controller has what it names."""
    uuid = network.uuid
    if identifier == uuid:
        return None
    if not identifier.startswith(f"{uuid}_"):
        other = identifier.split("_", 1)[0]
        return f"from another Controller ({other})"
    rest = identifier[len(uuid) + 1 :]
    if rest in _SERVICE_DEVICES or rest in network.node_addresses:
        return None
    node = NodeAddress.try_parse(rest)
    if node is not None:
        if network.device(node.device) is not None:
            return None
        return f"the Controller has no Device at {node.device}"
    return f"the Controller has no node {rest!r}"


async def prune(iox: IoxClient, ha: HaClient, rails: Rails) -> PruneResult:
    """Find Stale HA Devices; with `--apply`, remove the ones no entity uses."""
    result = PruneResult(find_stale(await iox.network(), await ha.registry()))
    if not rails.apply or not result.removable:
        return result
    async with rails.changing("prune", [HaRegistryBackup(ha)]) as backup:
        # Read both sides again under the lock: they may have changed since the plan.
        result = PruneResult(find_stale(await iox.network(), await ha.registry()), backup=backup)
        for device in result.removable:
            for entry in device.config_entries:
                await ha.remove_config_entry(device.device_id, entry)
            result.removed.append(device)
    return result


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
        lines.append(f"Dry run: nothing removed. Pass --apply to remove the {len(removable)} removable devices.")
    else:
        lines.append(f"Removed {len(result.removed)} Stale HA Devices.")
        if result.backup is not None:
            lines.append(f"Backup: {result.backup}")
    return "\n".join(lines) + "\n"
