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
3. Saves the renames to make in the backup folder, then Reconciles HA: reloads
   the isy994 integration, which removes OLD's entities, then removes the
   Stale HA Devices, OLD's among them.
4. Renames each of NEW's entities to the ID of OLD's entity for the same
   Button (`config/entity_registry/update`, `new_entity_id`), carrying over
   its display name, icon, area, labels, aliases and disabled and hidden state.
5. Carries OLD's HA device area, name and labels over to NEW's HA device.

So every Reference by entity ID works again with no edit. HA lets an entity
take an ID that only a deleted entity held, so the renames come after the
reload has removed OLD's entities, never before. HA keeps a deleted entity's
settings and gives them back if its unique ID returns, which is why step 2
makes sure IoX no longer has OLD: otherwise the reload would bring OLD's
entities back. Before any rename, the tool checks that every old ID is free,
in the registry and in the state machine, so a collision stops the renames
before the first. From step 3 on, OLD's entities are gone from HA, so any
failure prints the renames still to make by hand, which step 3 also saved.

Entities are matched on (domain, Button number, suffix): `light` for Button 1
matches `light` for Button 1, `number` for `1_OL` matches `number` for `1_OL`.
If the Buttons don't match one for one, such as a dimmer replaced by a keypad,
or an old entity has no counterpart, the command stops after step 2 and prints
OLD's References for the Administrator to fix.

References that name OLD's HA device instead of an entity, as device
triggers do, can't follow it: NEW has its own HA device. They are listed.

`--resume` is for when IoX's Replace has run but HA was not Reconciled, as
after a run that stopped at step 2 until IoX rebooted: the Controller no
longer has OLD, but HA still has its entities. It skips steps 1 and 2.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from insteon import refs as refs_command
from insteon.address import InsteonAddress
from insteon.ha import DOMAIN, HaClient, HaError, HaRegistry
from insteon.iox import IoxClient, IoxError
from insteon.network import Device, Network
from insteon.prune import PruneResult, reconcile
from insteon.rails import DeviceWrite, HaRegistryBackup, IoxBackup, Rails, write_device
from insteon.refs import find_mentions, ha_device_ids
from insteon.resolve import ResolveError, button_of, device_address, resolve_device

# What an entity rename carries over, as `config/entity_registry/update` takes them.
_CARRIED = ("name", "icon", "area_id", "labels", "aliases")
# HA takes only "user" or null for these through the API.
_STATES = ("disabled_by", "hidden_by")
# What `search/related` finds naming an HA device that HA's config API can show.
_DEVICE_USERS = ("automation", "script", "scene")
# The renames step 3 saves in the backup folder before the reload.
RENAMES_FILE = "replace-renames.json"


@dataclass(frozen=True, order=True)
class Place:
    """Where an isy994 entity sits on its Device: domain, Button number and the rest of its unique ID."""

    domain: str
    button: int
    suffix: str


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
    resume: bool
    """IoX's Replace is done already, and only HA is Reconciled."""
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
    """IoX's Replace ran (or was done already, with `--resume`)."""
    write: DeviceWrite | None = None
    stopped: str | None = None
    """Why the command stopped before finishing, or None."""
    reconciling: bool = False
    """HA's reload has started: OLD's entities may be gone."""
    reconciled: PruneResult | None = None
    renamed: list[Rename] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    backup: Path | None = None
    iox_backup: Path | None = None
    renames_file: Path | None = None

    @property
    def finished(self) -> bool:
        """Every step ran and nothing failed: every Reference by entity ID works."""
        return self.applied and self.stopped is None and not self.failures

    @property
    def unrenamed(self) -> list[Rename]:
        return [rename for rename in self.plan.renames if rename not in self.renamed]


@dataclass(frozen=True)
class _Step:
    """What the HA steps share."""

    ha: HaClient
    plan: ReplacePlan
    result: ReplaceResult
    progress: Callable[[str], None]


async def replace(
    iox: IoxClient,
    ha: HaClient,
    rails: Rails,
    iox_backup: IoxBackup,
    old_name: str,
    new_name: str,
    *,
    repo: Path,
    resume: bool = False,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> ReplaceResult:
    """Replace OLD with NEW, as the module says."""
    if not rails.apply:
        return ReplaceResult(await _plan(iox, ha, old_name, new_name, repo=repo, resume=resume))

    async with rails.changing("replace", [iox_backup, HaRegistryBackup(ha)]) as folder:
        progress(f"Backed up IoX to {iox_backup.path} and HA's registries to {folder}")
        # Plan again under the lock: either side may have changed since the dry run.
        plan = await _plan(iox, ha, old_name, new_name, repo=repo, resume=resume)
        result = ReplaceResult(plan, backup=folder, iox_backup=iox_backup.path)
        old, new = plan.old, plan.new

        if not plan.resume:  # 1
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
                    "reboot), and don't reload the isy994 integration or restart HA meanwhile. Once it no longer "
                    f"has {old.address}, run this again with --resume to Reconcile HA"
                )
                return result
        result.applied = True
        if not plan.matched:
            result.stopped = (
                "the Buttons don't match one for one, so HA was not changed. The next reload of the isy994 "
                "integration, or restart of HA, removes the old entities: fix the References below by hand"
            )
            return result

        step = _Step(ha, plan, result, progress)
        result.renames_file = _save_renames(folder, plan)  # 3
        result.reconciling = True
        try:
            progress("Reconciling HA: reloading the isy994 integration, then pruning")
            result.reconciled = await reconcile(iox, ha)
            registry = await ha.registry()  # 4
            problem = _blocked(plan, registry, await ha.entity_ids_with_state())
            if problem:
                result.stopped = f"{problem}; no entity was renamed"
                return result
            await _rename(step, registry)
            await _carry_device(step)  # 5
        except (HaError, IoxError) as error:
            result.stopped = f"Reconciling HA failed: {error}"
    return result


def _save_renames(folder: Path, plan: ReplacePlan) -> Path:
    """The renames to make, with the old entities' settings, saved before the reload removes OLD's entities."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RENAMES_FILE
    renames = [{"rename": rename.new_entity_id, "to": rename.entity_id, "old": rename.old} for rename in plan.renames]
    path.write_text(json.dumps(renames, indent=2) + "\n")
    return path


async def _rename(step: _Step, registry: HaRegistry) -> None:
    """Give each of NEW's entities its old entity's ID and settings. A failed rename is reported, and the rest still run."""
    for rename in step.plan.renames:
        current = next(e for e in registry.entities if e.get("entity_id") == rename.new_entity_id)
        step.progress(f"Renaming {rename.new_entity_id} to {rename.entity_id}")
        try:
            await step.ha.update_entity(
                rename.new_entity_id, new_entity_id=rename.entity_id, **_settings(rename.old, current)
            )
        except HaError as error:
            step.result.failures.append(f"{rename.new_entity_id} -> {rename.entity_id}: {error}")
            continue
        step.result.renamed.append(rename)


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


def _blocked(plan: ReplacePlan, registry: HaRegistry, states: frozenset[str]) -> str | None:
    """Why the renames can't run after the reload, or None.

    Each of NEW's entities must still be there, and each old ID must be free:
    neither registered nor in the state machine, as HA requires. NEW's IDs and
    OLD's never overlap: HA named NEW's entities while OLD's were registered.
    So an old ID still taken means the reload kept an old entity, or something
    else took the ID since.
    """
    held = {str(e.get("entity_id")): e for e in registry.entities}
    for rename in plan.renames:
        if rename.new_entity_id not in held:
            return f"{rename.new_entity_id} is no longer in HA after the reload"
        holder = held.get(rename.entity_id)
        if holder is not None:
            unique = holder.get("unique_id")
            return f"{rename.entity_id} is still taken after the reload (unique ID {unique!r}, platform {holder.get('platform')})"
        if rename.entity_id in states:
            return f"{rename.entity_id} still has a state in HA after the reload"
    return None


async def _carry_device(step: _Step) -> None:
    """Give NEW's HA device the area, name and labels of OLD's."""
    old, new = step.plan.old_ha_device, step.plan.new_ha_device
    if old is None or new is None:
        return
    changes: dict[str, Any] = {
        key: old.get(key) for key in ("area_id", "labels") if old.get(key) != new.get(key) and key in old
    }
    name = HaRegistry.device_name(old)
    if name and name != HaRegistry.device_name(new):
        changes["name_by_user"] = name
    if not changes:
        return
    step.progress(f"Carrying over the HA device's {', '.join(sorted(changes))}")
    try:
        await step.ha.update_device(str(new["id"]), **changes)
    except HaError as error:
        step.result.failures.append(f"the HA device of {step.plan.new.label}: {error}")


# Planning -----------------------------------------------------------------


async def _plan(
    iox: IoxClient, ha: HaClient, old_name: str, new_name: str, *, repo: Path, resume: bool
) -> ReplacePlan:
    network = await iox.network()
    registry = await ha.registry()
    try:
        new = resolve_device(new_name, network, registry)
    except ResolveError as error:
        raise ResolveError(
            f"{error}. IoX replaces a Device only with one it already has: add it first with "
            f"`insteon add {new_name.strip()} NAME --apply`"
        ) from error
    old = _resume_old(old_name, network, registry) if resume else _resolve_old(old_name, network, registry)
    if old.address == new.address:
        raise ResolveError(f"{old.address} can't replace itself")
    if not resume and network.scenes_of(new):
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
    if not resume:
        old_buttons = [button.address.button for button in old.buttons]
        new_buttons = [button.address.button for button in new.buttons]
        if old_buttons != new_buttons:
            mismatches.append(f"{old.label} has Buttons {_numbers(old_buttons)}; {new.label} has {_numbers(new_buttons)}")
    renames = []
    for place, entity in sorted(old_entities.items()):
        counterpart = new_entities.get(place)
        if counterpart is None:
            mismatches.append(f"{entity['entity_id']} has no counterpart on {new.label}")
        else:
            renames.append(Rename(entity, str(counterpart["entity_id"])))
    extra = tuple(sorted(str(e["entity_id"]) for place, e in new_entities.items() if place not in old_entities))

    references = await refs_command.report(ha, old, network, registry, repo=repo)
    old_devices = ha_device_ids(old, network, registry)
    new_devices = ha_device_ids(new, network, registry)
    return ReplacePlan(
        old=old,
        new=new,
        resume=resume,
        renames=tuple(renames),
        mismatches=tuple(mismatches),
        extra=extra,
        old_ha_device=registry.device(old_devices[0]) if len(old_devices) == 1 else None,
        new_ha_device=registry.device(new_devices[0]) if len(new_devices) == 1 else None,
        device_references=await _device_references(ha, old_devices),
        helpers=tuple(entity.entity_id for entity in references.entities if entity.platform != DOMAIN),
        references=references,
    )


def _resolve_old(text: str, network: Network, registry: HaRegistry) -> Device:
    """OLD, which the Controller must still have unless `--resume` says its Replace is done."""
    try:
        return resolve_device(text, network, registry)
    except ResolveError as error:
        address = _address_in(text, network, registry)
        if address is not None and _isy994_entities(registry, network.uuid, address):
            raise ResolveError(
                f"{error}, though HA still has its entities. If IoX's Replace of it has already run, "
                "pass --resume to Reconcile HA only"
            ) from error
        raise


def _resume_old(text: str, network: Network, registry: HaRegistry) -> Device:
    """OLD after IoX's Replace: a Device the Controller no longer has, with its entities still in HA.

    It stands in as a Device with no Buttons, named as HA named its device.
    """
    address = _address_in(text, network, registry)
    if address is None:
        raise ResolveError(f"with --resume, give OLD as an Insteon Address or an entity ID, not {text!r}")
    if (device := network.device(address)) is not None:
        raise ResolveError(f"the Controller still has {device.label}, so its Replace is not done; run without --resume")
    gone = _gone(address, "")
    if not _isy994_entities(registry, network.uuid, address):
        raise ResolveError(
            f"HA has no entities for {address} any more, so there are no old IDs to keep. The backup folder of the "
            f"run that ran IoX's Replace holds them (ha-entities.json, {RENAMES_FILE}); rename by hand"
        )
    names = [HaRegistry.device_name(d) for i in ha_device_ids(gone, network, registry) if (d := registry.device(i))]
    return _gone(address, names[0] if names else "")


def _gone(address: InsteonAddress, name: str) -> Device:
    """A Device the Controller no longer has, known only by its address and HA's name for it."""
    return Device(address, name, model="", type_code="", folder=None, enabled=True, comms_error=False, buttons=())


def _address_in(text: str, network: Network, registry: HaRegistry) -> InsteonAddress | None:
    """The Insteon Address `text` gives: as an address, or through an isy994 entity's unique ID."""
    if (address := device_address(text.strip())) is not None:
        return address
    entity = registry.isy994_entity(text.strip())
    place = button_of(entity, network.uuid) if entity else None
    return place.button.device if place else None


def _isy994_entities(registry: HaRegistry, uuid: str, address: InsteonAddress) -> dict[Place, dict[str, Any]]:
    """The isy994 entities of one Device's Buttons, by their place on it."""
    found = {}
    for entity in registry.entities:
        place = button_of(entity, uuid) if entity.get("platform") == DOMAIN else None
        if place is not None and place.button.device == address:
            domain = str(entity.get("entity_id", "")).split(".", 1)[0]
            found[Place(domain, place.button.button, place.suffix)] = entity
    return found


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
    dry = not (result.applied or result.stopped or result.failures)
    title = "Replaced" if result.finished else "Would replace" if dry else "Did not finish replacing"
    sections = [f"{title} {old.label} with {new.label}"]
    if result.stopped:
        sections.append(f"Stopped: {result.stopped}.")

    lines = []
    if plan.resume:
        lines.append(f"1. IoX's Replace: already done (--resume): the Controller has no {old.address}")
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

    if result.reconciling and not result.finished and result.unrenamed:
        lines = ["The old entities are gone from HA. Rename these by hand, in Settings > Entities, to keep the References:"]
        lines += [f"  {rename.new_entity_id} -> {rename.entity_id}" for rename in result.unrenamed]
        if result.renames_file is not None:
            lines.append(f"  The old entities' settings: {result.renames_file}")
        sections.append("\n".join(lines))
    if not plan.matched or (result.applied and not result.finished):
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
        lines = [f"IoX backup: {result.iox_backup}", f"HA backup: {result.backup}"]
        if result.finished:
            lines.append(f"Run `insteon audit {new.address}` to confirm its Links.")
        sections.append("\n".join(lines))
    else:
        sections.append("Dry run: nothing changed. Pass --apply to replace it.")
    return "\n\n".join(sections) + "\n"
