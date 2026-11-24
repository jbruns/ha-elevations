"""`insteon remove DEVICE [--clean]`: remove a Device, clean up after it, and Reconcile HA.

A dry run unless given `--apply`. Applying takes the lock, backs up IoX and
the HA registries, plans again, then, in this order:

1. takes each of the Device's Buttons out of each Scene it is in;
2. writes the updates to the Device, unless it is Unresponsive, and to every
   Device that shared a Scene with it (its former Scene Peers);
3. removes the Device from the Controller;
4. runs a Link Audit on the former Scene Peers, and Restores each one that
   differs; reads the Modem's Links, and Restores the Modem (`ReplaceModem`,
   minutes for a full table) if it still holds a Link to the removed Device;
5. Reconciles HA: reloads the isy994 integration, then removes the Stale HA
   Devices;
6. with `--clean`, shows the diffs, and once the Administrator confirms them,
   takes the Device's References out of live HA (`insteon.clean`).

An Unresponsive Device can't be written to, so step 2 skips it, but its peers
and the Modem are still cleaned. Whether a Device is Unresponsive is decided
by reading its Links first, as the Link Audit does; for a Battery Device, the
Administrator says whether it is there. References in the repo are listed,
never edited.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from insteon import clean as clean_command
from insteon.address import ButtonAddress
from insteon.audit import DEFAULT_TIMEOUT, AuditReport, audit_devices
from insteon.ha import DOMAIN, HaClient
from insteon.iox import DeviceUnresponsive, IoxClient, IoxError
from insteon.network import Device, Network, Scene
from insteon.prune import PruneResult, reconcile
from insteon.rails import HaRegistryBackup, IoxBackup, Rails, WriteReport, hold_set, write_device, write_updates_to
from insteon.refs import RepoReference, device_entities, ha_device_ids, repo_references
from insteon.resolve import resolve_device


@dataclass(frozen=True)
class Membership:
    scene: Scene
    button: ButtonAddress
    controller: bool


@dataclass(frozen=True)
class RemovePlan:
    device: Device
    memberships: tuple[Membership, ...]
    peers: tuple[Device, ...]
    """The other Devices in the Scenes it is in."""
    unresponsive: str | None
    """Why the Device counts as Unresponsive, or None when it answered."""
    probed: bool
    """Its Links were read to decide; False for a Battery Device in a dry run."""
    references: clean_command.CleanPlan
    repo: tuple[RepoReference, ...]


@dataclass
class RemoveResult:
    plan: RemovePlan
    cleaning: bool
    applied: bool = False
    writes: WriteReport = field(default_factory=WriteReport)
    audit: AuditReport | None = None
    restored: WriteReport = field(default_factory=WriteReport)
    modem_read: bool = False
    modem_links_left: int = 0
    """In-use Links to the removed Device the Modem held after the removal."""
    modem_restored: bool = False
    reconciled: PruneResult | None = None
    cleaned: clean_command.CleanResult | None = None
    clean_declined: bool = False
    backup: Path | None = None
    iox_backup: Path | None = None


async def remove(
    iox: IoxClient,
    ha: HaClient,
    rails: Rails,
    iox_backup: IoxBackup,
    name: str,
    *,
    repo: Path,
    clean: bool = False,
    read_modem_links: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> RemoveResult:
    """Remove the Device, as the module says.

    Until the Device is removed (step 3), any failure stops the command. After
    it, every step still runs: a failed Restore is reported, and HA is still
    Reconciled, since the Controller no longer has the Device either way.
    """
    if not rails.apply:
        with rails.modem_operation("remove"):
            planned = await _plan(iox, ha, name, repo=repo, ask=None, timeout=timeout, progress=progress)
        return RemoveResult(planned, cleaning=clean)

    async with rails.changing("remove", [iox_backup, HaRegistryBackup(ha)]) as folder:
        progress(f"Backed up IoX to {iox_backup.path} and HA's registries to {folder}")
        # Plan again under the lock: the network may have changed since the dry run.
        planned = await _plan(iox, ha, name, repo=repo, ask=prompt, timeout=timeout, progress=progress)
        result = RemoveResult(planned, cleaning=clean, backup=folder, iox_backup=iox_backup.path)
        device = planned.device

        for membership in planned.memberships:  # 1
            progress(f"Taking {membership.button} out of {membership.scene.name} ({membership.scene.address})")
            await iox.remove_from_scene(membership.button, membership.scene.address)

        writing = ([] if planned.unresponsive else [device]) + list(planned.peers)  # 2
        await write_updates_to(iox, writing, prompt, progress, result.writes)

        progress(f"Removing {device.label} from the Controller")  # 3
        await iox.remove_device(device.primary)
        network = await iox.network()
        if network.device(device.address) is not None:
            raise IoxError(f"IoX still has {device.address} after RemoveNode; nothing after step 3 was done")

        await _clean_peers_and_modem(iox, network, planned, result, read_modem_links, timeout, prompt, progress)  # 4

        progress("Reconciling HA: reloading the isy994 integration, then pruning")  # 5
        result.reconciled = await reconcile(iox, ha)

        if clean:  # 6
            refreshed = await clean_command.refresh(ha, planned.references)
            if refreshed.edits:
                for line in clean_command.render(refreshed, cleaning=True):
                    progress(line)
                answer = prompt(f"Save these {len(refreshed.edits)} edits to live HA? Type yes to save them: ")
                result.clean_declined = answer.strip().lower() != "yes"
            if not result.clean_declined:
                result.cleaned = await clean_command.save(ha, refreshed, folder)
        result.applied = True
    return result


async def _clean_peers_and_modem(
    iox: IoxClient,
    network: Network,
    planned: RemovePlan,
    result: RemoveResult,
    read_modem_links: bool,
    timeout: float,
    prompt: Callable[[str], str],
    progress: Callable[[str], None],
) -> None:
    """Step 4: Restore the former Scene Peers that differ, and the Modem if it still links to the Device.

    The Device is gone by now, so a failure here is reported and the rest still runs.
    """
    device = planned.device
    peers = [peer for p in planned.peers if (peer := network.device(p.address)) is not None]
    try:
        if peers:
            result.audit = await audit_devices(
                iox, network, peers, named=False, read_modem_links=False, timeout=timeout, prompt=prompt, progress=progress
            )
    except IoxError as error:
        result.restored.failures.append(f"the Link Audit of its former Scene Peers: {error}")
    for table in result.audit.tables if result.audit else ():
        if not (table.differences or table.stale) or (peer := network.device(table.owner)) is None:
            continue
        progress(f"Restoring {peer.label}, which differs from the Controller")
        try:
            result.restored.writes.append(
                await write_device(peer, lambda clear, p=peer: iox.restore_device(p.primary, until_clear=clear), prompt)
            )
        except IoxError as error:
            result.restored.failures.append(f"{peer.label}: {error}")
    if not read_modem_links:
        return
    try:
        modem = await iox.modem()
        progress("Reading the Modem's Links (about 2 a second)")
        left = [link for link in await iox.modem_links(modem.address) if link.in_use and link.address == device.address]
        result.modem_read, result.modem_links_left = True, len(left)
        if left:
            progress(f"The Modem still holds {len(left)} Links to {device.address}: Restoring the Modem")
            await iox.restore_modem()
            result.modem_restored = True
    except IoxError as error:
        result.restored.failures.append(f"the Modem: {error}")


async def _plan(
    iox: IoxClient,
    ha: HaClient,
    name: str,
    *,
    repo: Path,
    ask: Callable[[str], str] | None,
    timeout: float,
    progress: Callable[[str], None],
) -> RemovePlan:
    """Read what the removal will touch. `ask` is None in a dry run, which prompts for nothing."""
    network = await iox.network()
    registry = await ha.registry()
    device = resolve_device(name, network, registry)
    memberships = _memberships(device, network)
    peers = _peers(device, network, memberships)

    unresponsive, probed = None, True
    if device.battery and ask is None:
        probed = False
    elif device.battery and hold_set(device.label, ask, other="type u if it is gone").startswith("u"):
        unresponsive = "the Administrator says it is gone"
    else:
        progress(f"Reading the Links of {device.address} {device.name} to see whether it answers")
        try:
            await iox.device_links(device.primary, timeout=timeout)
        except DeviceUnresponsive as error:
            unresponsive = str(error)

    entities = device_entities(device, network, registry)
    isy994 = [entity.entity_id for entity in entities if entity.platform == DOMAIN]
    helpers = [entity.entity_id for entity in entities if entity.platform != DOMAIN]
    references = await clean_command.plan(ha, isy994, ha_device_ids(device, network, registry), helpers)
    _, in_repo = repo_references(repo, [entity.entity_id for entity in entities])
    return RemovePlan(device, memberships, peers, unresponsive, probed, references, tuple(in_repo))


def _memberships(device: Device, network: Network) -> tuple[Membership, ...]:
    found = []
    for scene in network.scenes:
        for buttons, controller in ((scene.controllers, True), (scene.responders, False)):
            found.extend(Membership(scene, button, controller) for button in buttons if button.device == device.address)
    return tuple(found)


def _peers(device: Device, network: Network, memberships: tuple[Membership, ...]) -> tuple[Device, ...]:
    addresses = {
        button.device
        for scene in dict.fromkeys(m.scene for m in memberships)
        for button in (*scene.controllers, *scene.responders)
        if button.device != device.address
    }
    return tuple(peer for peer in network.devices if peer.address in addresses)


# Rendering ----------------------------------------------------------------


def render(result: RemoveResult) -> str:
    plan, device = result.plan, result.plan.device
    title = "Removed" if result.applied else "Would remove"
    sections = [f"{title} {device.address} {device.name}"]

    if plan.unresponsive:
        sections.append(f"Unresponsive ({plan.unresponsive}): nothing is written to it; its Scene Peers and the Modem are still cleaned.")
    elif not plan.probed:
        sections.append("A Battery Device: with --apply, you will be asked to hold its Set button, or to say it is gone.")

    lines = ["1. Leave its Scenes" + ("" if plan.memberships else ": it is in none")]
    lines += [
        f"   {m.button}  {'Controller' if m.controller else 'Responder'} in {m.scene.name} ({m.scene.address})"
        for m in plan.memberships
    ]
    writing = ([] if plan.unresponsive else [device]) + list(plan.peers)
    lines.append("2. Write the updates to " + (", ".join(d.label for d in writing) or "no Device"))
    lines += result.writes.lines("   ")
    lines.append(f"3. Remove {device.address} from the Controller")
    lines.append(
        "4. Link Audit its former Scene Peers and Restore each that differs; Restore the Modem if it still links to it"
    )
    if result.audit is not None:
        lines += [f"   Unresponsive peer, not Restored: {u.device.label}" for u in result.audit.unresponsive]
        lines += [f"   Skipped peer: {s.device.label}: {s.reason}" for s in result.audit.skipped]
    lines += [f"   Restored {w.device.label}" for w in result.restored.writes if w.written]
    lines += result.restored.lines("   ")
    if result.applied:
        if not result.modem_read:
            lines.append("   The Modem's Links were not read (--no-modem-links); run `insteon audit` to check them")
        elif result.modem_restored:
            lines.append(f"   The Modem held {result.modem_links_left} Links to it: Restored the Modem")
        else:
            lines.append("   The Modem holds no Links to it")
    lines.append("5. Reconcile HA: reload the isy994 integration, then prune Stale HA Devices")
    if result.reconciled is not None:
        lines.append(f"   Pruned {len(result.reconciled.removed)} Stale HA Devices")
        lines += [f"   held: {d.name} ({', '.join(d.entity_ids)})" for d in result.reconciled.held]
    lines.append(
        "6. "
        + ("Take its References out of live HA, after you confirm the diffs" if result.cleaning else "References are listed only (no --clean)")
    )
    if result.clean_declined:
        lines.append("   Not confirmed: live HA was not edited")
    sections.append("\n".join(lines))

    sections.append("\n".join(clean_command.render(plan.references, cleaning=result.cleaning)))
    if result.cleaned is not None:
        lines = [f"Edited {len(result.cleaned.saved)} in live HA"]
        lines += [f"  {edit.item.label}" for edit in result.cleaned.saved]
        lines += [f"  by hand: {hand.label}: {hand.reason}" for hand in result.cleaned.by_hand]
        if result.cleaned.backup is not None:
            lines.append(f"  Their configs before the edit: {result.cleaned.backup}")
        sections.append("\n".join(lines))

    lines = ["References in the repo (listed, never edited)"]
    lines += [
        f"  {ref.path}:{ref.line}  {ref.entity_id}" + (f" as {{{ref.token}}}" if ref.token else "") for ref in plan.repo
    ] or ["  none"]
    sections.append("\n".join(lines))

    if result.applied:
        lines = []
        if result.iox_backup is not None:
            lines.append(f"IoX backup: {result.iox_backup}")
        if result.backup is not None:
            lines.append(f"HA backup: {result.backup}")
        lines.append("Run `insteon audit` to confirm the network is clean.")
        sections.append("\n".join(lines))
    else:
        flags = "--apply" + (" (with --clean)" if result.cleaning else "")
        sections.append(f"Dry run: nothing changed. Pass {flags} to remove it.")
    return "\n\n".join(sections) + "\n"
