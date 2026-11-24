"""`insteon replace OLD NEW`: IoX's Replace, then Reconcile HA, keeping the old entity IDs.

NEW must already be on the Controller, added as `insteon add` adds it, and HA
must already have its entities: IoX's "Replace with" only offers a Device it
already has (research/iox-api.md §3). A dry run unless given `--apply`.
Applying takes the lock, backs up IoX and HA's registries, plans again, then,
in this order:

1. IoX's Replace (`ReplaceDevice`): IoX moves OLD's Scenes and Links onto NEW.
   For a Battery Device, the Administrator holds NEW's Set button first.
2. Reads the Controller again. Unless it no longer has OLD and still has NEW,
   the command stops here, with HA untouched, so every Reference still works.
3. Reconciles HA: reloads the isy994 integration, which removes OLD's
   entities, then removes the Stale HA Devices, OLD's among them.
4. Renames each of NEW's entities to the ID of OLD's entity for the same
   Button (`config/entity_registry/update`, `new_entity_id`), carrying over
   its display name, icon, area, labels, aliases and disabled and hidden state.
5. Carries OLD's HA device area, name and labels over to NEW's HA device.

So every Reference by entity ID works again with no edit. HA lets an entity
take an ID that only a deleted entity held, so the renames come after the reload
has removed OLD's entities, never before. HA keeps a deleted entity's settings
and gives them back if its unique ID returns, which is why step 2 makes sure
IoX no longer has OLD: otherwise the reload would bring OLD's entities back. Before any
rename, the tool checks that every old ID is free, so a collision stops the
renames before the first.

Entities are matched on (domain, Button number, suffix): `light` for Button 1
matches `light` for Button 1, `number` for `1_OL` matches `number` for `1_OL`.
If the Buttons don't match one for one, such as a dimmer replaced by a keypad,
or an old entity has no counterpart, the command stops after step 2 and prints
OLD's References for the Administrator to fix.

References that name OLD's HA device instead of an entity, as device
triggers do, can't follow it: NEW has its own HA device. They are listed.

If IoX already no longer has OLD but HA still has its entities, as after a run
that stopped at step 2 for IoX to reboot, IoX's Replace is taken as done and
only HA is Reconciled.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from insteon import refs as refs_command
from insteon.address import ButtonAddress, InsteonAddress
from insteon.ha import DOMAIN, HaClient, HaError, HaRegistry, controller_suffix
from insteon.iox import IoxClient
from insteon.network import Device, Network
from insteon.prune import PruneResult, reconcile
from insteon.rails import DeviceWrite, HaRegistryBackup, IoxBackup, Rails, write_device
from insteon.refs import find_mentions, ha_device_ids
from insteon.resolve import ResolveError, resolve_device

# What an entity rename carries over, as `config/entity_registry/update` takes them.
_CARRIED = ("name", "icon", "area_id", "labels", "aliases")
# HA takes only "user" or null for these through the API.
_STATES = ("disabled_by", "hidden_by")
# What `search/related` finds naming an HA device that HA's config API can show.
_DEVICE_USERS = ("automation", "script", "scene")

Key = tuple[str, int, str]
"""An isy994 entity's place on its Device: domain, Button number, unique-ID suffix."""


@dataclass(frozen=True)
class Rename:
    old: dict[str, Any]
    """OLD's entity, as the registry held it before the Replace."""
    new_entity_id: str
    """NEW's entity for the same Button, by its ID before the rename."""

    @property
    def entity_id(self) -> str:
        """The ID it takes: the old entity's."""
        return str(self.old["entity_id"])


@dataclass(frozen=True)
class DeviceReference:
    """An automation, script or scene that names OLD's HA device, which NEW doesn't inherit."""

    label: str
    confirmed: bool
    """Its config names the device; False when HA's config API can't show it."""


@dataclass(frozen=True)
class ReplacePlan:
    old: Device
    new: Device
    replaced: bool
    """IoX already has no OLD: its Replace is done, and only HA is Reconciled."""
    renames: tuple[Rename, ...]
    mismatches: tuple[str, ...]
    """Why the Buttons don't match one for one; empty when they do."""
    extra: tuple[str, ...]
    """NEW's entities no old entity matches. They keep their IDs."""
    old_ha_device: dict[str, Any] | None
    new_ha_device: dict[str, Any] | None
    device_references: tuple[DeviceReference, ...]
    helpers: tuple[str, ...]
    """Other integrations' entities on OLD's HA device, such as `switch_as_x`, for a check by hand."""
    references: refs_command.RefsReport

    @property
    def matched(self) -> bool:
        return not self.mismatches


@dataclass
class ReplaceResult:
    plan: ReplacePlan
    applied: bool = False
    write: DeviceWrite | None = None
    stopped: str | None = None
    """Why the command stopped before finishing, or None."""
    reconciled: PruneResult | None = None
    renamed: list[Rename] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    device_carried: bool = False
    backup: Path | None = None
    iox_backup: Path | None = None


async def replace(
    iox: IoxClient,
    ha: HaClient,
    rails: Rails,
    iox_backup: IoxBackup,
    old_name: str,
    new_name: str,
    *,
    repo: Path,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> ReplaceResult:
    """Replace OLD with NEW, as the module says."""
    if not rails.apply:
        return ReplaceResult(await _plan(iox, ha, old_name, new_name, repo=repo))

    async with rails.changing("replace", [iox_backup, HaRegistryBackup(ha)]) as folder:
        progress(f"Backed up IoX to {iox_backup.path} and HA's registries to {folder}")
        # Plan again under the lock: either side may have changed since the dry run.
        plan = await _plan(iox, ha, old_name, new_name, repo=repo)
        result = ReplaceResult(plan, backup=folder, iox_backup=iox_backup.path)
        old, new = plan.old, plan.new

        if not plan.replaced:  # 1
            progress(f"Replacing {old.label} with {new.label} on the Controller")
            result.write = await write_device(
                new,
                lambda clear: iox.replace_device(old.primary, new.primary, new.type_code, until_clear=clear),
                prompt,
            )
            if not result.write.written:
                result.stopped = "stopped at the prompt: IoX's Replace was not run, and nothing changed"
                return result
            network = await iox.network()  # 2
            if network.device(old.address) is not None or network.device(new.address) is None:
                result.stopped = (
                    f"IoX answered the Replace, but still has {old.address}"
                    if network.device(old.address) is not None
                    else f"IoX no longer has {new.address} after the Replace"
                ) + (
                    "; HA was not changed, so every Reference still works. Check the Controller (it may want a "
                    "reboot), and don't reload the isy994 integration meanwhile. Once it no longer has "
                    f"{old.address}, run this again to Reconcile HA"
                )
                return result
        result.applied = True
        if not plan.matched:
            result.stopped = (
                "the Buttons don't match one for one, so HA was not changed. The next reload of the isy994 "
                "integration, or restart of HA, removes the old entities: fix the References below by hand"
            )
            return result

        progress("Reconciling HA: reloading the isy994 integration, then pruning")  # 3
        result.reconciled = await reconcile(iox, ha)

        registry = await ha.registry()  # 4
        problem = _blocked(plan, registry)
        if problem:
            result.stopped = f"{problem}; no entity was renamed. Rename them by hand"
            return result
        await _rename(ha, plan, registry, result, progress)

        await _carry_device(ha, plan, result, progress)  # 5
    return result


async def _rename(
    ha: HaClient, plan: ReplacePlan, registry: HaRegistry, result: ReplaceResult, progress: Callable[[str], None]
) -> None:
    """Give each of NEW's entities its old entity's ID and settings. A failed rename is reported, and the rest still run."""
    for rename in plan.renames:
        current = next(e for e in registry.entities if e.get("entity_id") == rename.new_entity_id)
        progress(f"Renaming {rename.new_entity_id} to {rename.entity_id}")
        try:
            await ha.update_entity(rename.new_entity_id, new_entity_id=rename.entity_id, **_settings(rename.old, current))
        except HaError as error:
            result.failures.append(f"{rename.new_entity_id} -> {rename.entity_id}: {error}")
            continue
        result.renamed.append(rename)


def _settings(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What the rename carries over from the old entity: the settings that differ.

    A display name the old entity took from IoX carries over as a name, so it
    reads as before even when NEW's IoX name differs. HA takes disabled and
    hidden only as "user" or null; an old entity disabled by the integration
    leaves NEW's as it is.
    """
    changes = {key: old.get(key) for key in _CARRIED if old.get(key) != new.get(key) and key in old}
    if old.get("name") is None and old.get("original_name") and old.get("original_name") != new.get("original_name"):
        changes["name"] = old["original_name"]
    for key in _STATES:
        if old.get(key) == "user" and new.get(key) != "user":
            changes[key] = "user"
        elif old.get(key) is None and new.get(key) is not None:
            changes[key] = None
    return changes


def _blocked(plan: ReplacePlan, registry: HaRegistry) -> str | None:
    """Why the renames can't run after the reload, or None.

    Each of NEW's entities must still be there, and each old ID must be free.
    NEW's IDs and OLD's never overlap: HA named NEW's entities while OLD's
    were registered. So an old ID still taken means the reload kept an old
    entity, or something else took the ID since.
    """
    held = {str(e.get("entity_id")): e for e in registry.entities}
    for rename in plan.renames:
        if rename.new_entity_id not in held:
            return f"{rename.new_entity_id} is no longer in HA after the reload"
        holder = held.get(rename.entity_id)
        if holder is not None:
            unique = holder.get("unique_id")
            return f"{rename.entity_id} is still taken after the reload (unique ID {unique!r}, platform {holder.get('platform')})"
    return None


async def _carry_device(ha: HaClient, plan: ReplacePlan, result: ReplaceResult, progress: Callable[[str], None]) -> None:
    """Give NEW's HA device the area, name and labels of OLD's."""
    old, new = plan.old_ha_device, plan.new_ha_device
    if old is None or new is None:
        return
    changes: dict[str, Any] = {
        key: old.get(key) for key in ("area_id", "labels") if old.get(key) != new.get(key) and key in old
    }
    name = old.get("name_by_user") or old.get("name")
    if name and name != (new.get("name_by_user") or new.get("name")):
        changes["name_by_user"] = name
    if not changes:
        return
    progress(f"Carrying over the HA device's {', '.join(sorted(changes))}")
    try:
        await ha.update_device(str(new["id"]), **changes)
        result.device_carried = True
    except HaError as error:
        result.failures.append(f"the HA device of {plan.new.label}: {error}")


# Planning -----------------------------------------------------------------


async def _plan(iox: IoxClient, ha: HaClient, old_name: str, new_name: str, *, repo: Path) -> ReplacePlan:
    network = await iox.network()
    registry = await ha.registry()
    try:
        new = resolve_device(new_name, network, registry)
    except ResolveError as error:
        raise ResolveError(
            f"{error}. IoX replaces a Device only with one it already has: add it first with "
            f"`insteon add {new_name.strip()} NAME --apply`"
        ) from error
    old, replaced = _resolve_old(old_name, network, registry)
    if old.address == new.address:
        raise ResolveError(f"{old.address} can't replace itself")
    if not replaced and network.scenes_of(new):
        scenes = ", ".join(scene.name for scene in network.scenes_of(new))
        raise ResolveError(
            f"{new.label} is already in Scenes ({scenes}); a Replace moves {old.label}'s Scenes onto it. "
            "Take it out of them, or add it again erasing its Links"
        )
    old_entities = _isy994_entities(registry, network.uuid, old.address)
    new_entities = _isy994_entities(registry, network.uuid, new.address)
    if not new_entities:
        raise ResolveError(
            f"HA has no entities for {new.label} yet. Reload the isy994 integration (`insteon add` does), then run this again"
        )

    mismatches = []
    if not replaced:
        old_buttons = [button.address.button for button in old.buttons]
        new_buttons = [button.address.button for button in new.buttons]
        if old_buttons != new_buttons:
            mismatches.append(f"{old.label} has Buttons {_numbers(old_buttons)}; {new.label} has {_numbers(new_buttons)}")
    renames = []
    for key, entity in sorted(old_entities.items()):
        counterpart = new_entities.get(key)
        if counterpart is None:
            mismatches.append(f"{entity['entity_id']} has no counterpart on {new.label}")
        else:
            renames.append(Rename(entity, str(counterpart["entity_id"])))
    extra = tuple(sorted(str(e["entity_id"]) for key, e in new_entities.items() if key not in old_entities))

    old_devices = ha_device_ids(old, network, registry)
    new_devices = ha_device_ids(new, network, registry)
    helpers = tuple(
        sorted(
            str(e["entity_id"]) for e in registry.entities if e.get("platform") != DOMAIN and e.get("device_id") in old_devices
        )
    )
    return ReplacePlan(
        old=old,
        new=new,
        replaced=replaced,
        renames=tuple(renames),
        mismatches=tuple(mismatches),
        extra=extra,
        old_ha_device=registry.device(old_devices[0]) if len(old_devices) == 1 else None,
        new_ha_device=registry.device(new_devices[0]) if len(new_devices) == 1 else None,
        device_references=await _device_references(ha, old_devices),
        helpers=helpers,
        references=await refs_command.report(ha, old, network, registry, repo=repo),
    )


def _resolve_old(text: str, network: Network, registry: HaRegistry) -> tuple[Device, bool]:
    """OLD, and whether IoX's Replace is already done.

    When the Controller has no Device at OLD's address but HA still has its
    entities, the Replace is taken as done: OLD stands in as a Device with no
    Buttons, named as HA named it.
    """
    try:
        return resolve_device(text, network, registry), False
    except ResolveError:
        address = _address_in(text, network, registry)
        if address is None or network.device(address) is not None:
            raise
        if not _isy994_entities(registry, network.uuid, address):
            raise
    stand_in = Device(address, "", "", "", None, True, False, ())
    devices = [registry.device(device_id) for device_id in ha_device_ids(stand_in, network, registry)]
    name = next((d.get("name_by_user") or d.get("name") or "" for d in devices if d), "")
    return Device(address, name, "", "", None, True, False, ()), True


def _address_in(text: str, network: Network, registry: HaRegistry) -> InsteonAddress | None:
    """The Insteon Address `text` gives: as an address, or through an isy994 entity's unique ID."""
    query = text.strip()
    button = ButtonAddress.try_parse(query)
    if button is not None:
        return button.device
    if (address := InsteonAddress.try_parse(query)) is not None:
        return address
    entity = registry.isy994_entity(query)
    found = _key_of(entity, network.uuid) if entity else None
    return found[1] if found else None


def _isy994_entities(registry: HaRegistry, uuid: str, address: InsteonAddress) -> dict[Key, dict[str, Any]]:
    """The isy994 entities of one Device's Buttons, by their place on it."""
    found = {}
    for entity in registry.entities:
        keyed = _key_of(entity, uuid) if entity.get("platform") == DOMAIN else None
        if keyed is not None and keyed[1] == address:
            found[keyed[0]] = entity
    return found


def _key_of(entity: dict[str, Any], uuid: str) -> tuple[Key, InsteonAddress] | None:
    """`(domain, Button number, suffix)` and the Device's address, from an isy994 unique ID `<uuid>_AA BB CC 1_OL`."""
    suffix = controller_suffix(str(entity.get("unique_id", "")), uuid)
    address, _, rest = (suffix or "").partition("_")
    button = ButtonAddress.try_parse(address)
    if button is None:
        return None
    domain = str(entity.get("entity_id", "")).split(".", 1)[0]
    return (domain, button.button, rest), button.device


async def _device_references(ha: HaClient, device_ids: tuple[str, ...]) -> tuple[DeviceReference, ...]:
    """The automations, scripts and scenes that name OLD's HA device by its ID."""
    found = []
    for device_id in device_ids:
        related = await ha.related(device_id, item_type="device")
        for kind in _DEVICE_USERS:
            for entity_id in sorted(related.get(kind, [])):
                config = await ha.item_config(kind, entity_id)
                if config is None:
                    found.append(DeviceReference(f"{kind} {entity_id}", confirmed=False))
                elif next(iter(find_mentions(config, [device_id])), None) is not None:
                    found.append(DeviceReference(f"{kind} {entity_id}", confirmed=True))
    return tuple(found)


def _numbers(buttons: list[int]) -> str:
    return ", ".join(f"{number:X}" for number in buttons) or "none"


# Rendering ----------------------------------------------------------------


def render(result: ReplaceResult) -> str:
    plan, old, new = result.plan, result.plan.old, result.plan.new
    done = result.applied and result.stopped is None
    title = "Replaced" if done else "Did not finish replacing" if result.stopped else "Would replace"
    sections = [f"{title} {old.label} with {new.label}"]
    if result.stopped:
        sections.append(f"Stopped: {result.stopped}.")

    lines = []
    if plan.replaced:
        lines.append(f"1. IoX's Replace: already done (the Controller has no {old.address}, but HA still has its entities)")
    else:
        lines.append(f"1. IoX's Replace: move {old.address}'s Scenes and Links onto {new.address}")
        if new.battery:
            lines.append("   A Battery Device: with --apply, you will be asked to hold its Set button first.")
        if result.write is not None and result.write.outcome:
            lines.append(f"   {new.label}: {result.write.outcome}")
    lines.append(f"2. Check the Controller no longer has {old.address}, and has {new.address}")
    lines.append("3. Reconcile HA: reload the isy994 integration, then prune Stale HA Devices")
    if result.reconciled is not None:
        lines.append(f"   Pruned {len(result.reconciled.removed)} Stale HA Devices")
        lines += [f"   held: {d.name} ({', '.join(d.entity_ids)})" for d in result.reconciled.held]
    if plan.matched:
        lines.append("4. Rename each new entity to the old entity's ID, with its name, area and state")
        lines += [f"   {rename.new_entity_id} -> {rename.entity_id}" for rename in plan.renames] or ["   none to rename"]
        lines += [f"   kept: {entity_id} (no old entity matches it)" for entity_id in plan.extra]
        lines.append("5. Carry the HA device's area and name over to the new HA device")
    else:
        lines.append("4. Not run: the Buttons don't match one for one")
        lines += [f"   {mismatch}" for mismatch in plan.mismatches]
    lines += [f"   failed: {failure}" for failure in result.failures]
    sections.append("\n".join(lines))

    if not plan.matched or (result.stopped and result.applied):
        sections.append(refs_command.render(plan.references).rstrip("\n"))
    if plan.device_references:
        lines = ["These name the old HA device, which the new one does not inherit; edit them by hand afterwards"]
        lines += [
            f"  {ref.label}" + ("" if ref.confirmed else " (its config can't be read here; check it)")
            for ref in plan.device_references
        ]
        sections.append("\n".join(lines))
    if plan.helpers:
        sections.append(
            "\n".join(
                ["Helpers on the old HA device; check each still works afterwards"]
                + [f"  {helper}" for helper in plan.helpers]
            )
        )

    if result.backup is not None:
        lines = []
        if result.iox_backup is not None:
            lines.append(f"IoX backup: {result.iox_backup}")
        if result.backup is not None:
            lines.append(f"HA backup: {result.backup}")
        if done:
            lines.append(f"Run `insteon audit {new.address}` to confirm its Links.")
        sections.append("\n".join(lines))
    else:
        sections.append("Dry run: nothing changed. Pass --apply to replace it.")
    return "\n\n".join(section for section in sections if section) + "\n"
