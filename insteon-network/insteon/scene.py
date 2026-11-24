"""`insteon scene …`: create and delete Scenes, and edit them Button by Button.

    scene create NAME [--address N]
    scene delete SCENE
    scene add SCENE BUTTON (--controller | --responder)
    scene remove SCENE BUTTON
    scene level SCENE RESPONDER PERCENT [--ramp INDEX] [--controller BUTTON]

A dry run unless given `--apply`. Applying takes the lock and backs up IoX
(and HA's registries, when HA will be Reconciled), plans again, makes the
change, then writes the updates to every Device whose Links it changes. A
Battery Device is written only while the Administrator holds its Set button.
Creating or deleting a Scene adds or removes its HA switch, so those Reconcile
HA afterwards. A Button controls at most one Scene.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from insteon.address import ButtonAddress
from insteon.ha import HaClient, HaRegistry
from insteon.iox import IoxClient, Write
from insteon.network import Device, Network
from insteon.prune import PruneResult, reconcile
from insteon.rails import Backup, HaRegistryBackup, IoxBackup, Rails, WriteReport, write_updates_to
from insteon.resolve import ResolveError, ha_entity_ids, resolve_button, resolve_scene

_FIRST_SCENE_ADDRESS = 10000
_LAST_SCENE_ADDRESS = 65535
_LEVELS = 255
_RAMP_RATES = 31


@dataclass(frozen=True)
class Change:
    """One Scene change: what it says, the IoX calls, and the Devices whose Links it changes."""

    summary: str
    calls: tuple[Callable[[IoxClient], Awaitable[Write]], ...]
    devices: tuple[Device, ...]
    reconcile: bool = False
    scene: str = ""
    """The Scene's address, to find its HA switch afterwards."""


Planner = Callable[[Network], Change]


def create(name: str, address: str | None = None) -> Planner:
    def planned(network: Network) -> Change:
        if not name.strip():
            raise ResolveError("a Scene needs a name")
        if any(scene.name.casefold() == name.casefold() for scene in network.scenes):
            raise ResolveError(f"a Scene named {name!r} already exists")
        chosen = address or _free_address(network)
        if network.scene(chosen) is not None or chosen in network.node_addresses:
            raise ResolveError(f"the Controller already uses {chosen}")
        return Change(
            f"Create the Scene {name!r} at {chosen}",
            (lambda iox: iox.create_scene(chosen, name),),
            (),
            reconcile=True,
            scene=chosen,
        )

    return planned


def delete(scene_name: str) -> Planner:
    def planned(network: Network) -> Change:
        scene = resolve_scene(scene_name, network)
        return Change(
            f"Delete the Scene {scene.name} ({scene.address})",
            (lambda iox: iox.delete_scene(scene.address),),
            _devices(network, [*scene.controllers, *scene.responders]),
            reconcile=True,
        )

    return planned


def add_button(scene_name: str, button_name: str, *, controller: bool, registry: HaRegistry | None = None) -> Planner:
    def planned(network: Network) -> Change:
        scene = resolve_scene(scene_name, network)
        button = resolve_button(button_name, network, registry)
        if button in (*scene.controllers, *scene.responders):
            raise ResolveError(f"{button} is already in {scene.name} ({scene.address})")
        if controller:
            controls = next((other for other in network.scenes if button in other.controllers), None)
            if controls is not None:
                raise ResolveError(
                    f"{button} already controls {controls.name} ({controls.address}), and a Button controls at most one Scene"
                )
        role = "Controller" if controller else "Responder"
        return Change(
            f"Add {_label(network, button)} to {scene.name} ({scene.address}) as a {role} Button",
            (lambda iox: iox.add_to_scene(button, scene.address, controller=controller),),
            _devices(network, [button, *scene.controllers, *scene.responders]),
        )

    return planned


def remove_button(scene_name: str, button_name: str, *, registry: HaRegistry | None = None) -> Planner:
    def planned(network: Network) -> Change:
        scene = resolve_scene(scene_name, network)
        button = resolve_button(button_name, network, registry)
        if button not in (*scene.controllers, *scene.responders):
            raise ResolveError(f"{button} is not in {scene.name} ({scene.address})")
        return Change(
            f"Take {_label(network, button)} out of {scene.name} ({scene.address})",
            (lambda iox: iox.remove_from_scene(button, scene.address),),
            _devices(network, [*scene.controllers, *scene.responders]),
        )

    return planned


def level(
    scene_name: str,
    responder_name: str,
    percent: float,
    *,
    ramp: int | None = None,
    controller_name: str | None = None,
    registry: HaRegistry | None = None,
) -> Planner:
    """Set a Responder Button's on level (and ramp rate) for the Scene, or for one of its Controller Buttons.

    The level goes to IoX as 0-255, the byte the Responder's Link holds.
    """

    def planned(network: Network) -> Change:
        scene = resolve_scene(scene_name, network)
        responder = resolve_button(responder_name, network, registry)
        if responder not in scene.responders:
            raise ResolveError(f"{responder} is not a Responder Button in {scene.name} ({scene.address})")
        if not 0 <= percent <= 100:
            raise ResolveError(f"a level is a percent from 0 to 100, not {percent:g}")
        if ramp is not None and not 0 <= ramp <= _RAMP_RATES:
            raise ResolveError(f"a ramp rate is an index from 0 to {_RAMP_RATES}, not {ramp}")
        source, source_label = scene.address, f"{scene.name} ({scene.address})"
        devices = [responder]
        if controller_name is not None:
            button = resolve_button(controller_name, network, registry)
            if button not in scene.controllers:
                raise ResolveError(f"{button} is not a Controller Button in {scene.name} ({scene.address})")
            source, source_label = button.iox, f"{_label(network, button)} in {scene.name}"
            devices.append(button)
        value = round(percent * _LEVELS / 100)
        calls: list[Callable[[IoxClient], Awaitable[Write]]] = [
            lambda iox: iox.set_scene_profile(responder, source, "OL", value)
        ]
        summary = f"Set {_label(network, responder)} to {percent:g}% ({value}/255)"
        if ramp is not None:
            calls.append(lambda iox: iox.set_scene_profile(responder, source, "RR", ramp))
            summary += f", ramp rate {ramp}"
        return Change(f"{summary} when {source_label} turns it on", tuple(calls), _devices(network, devices))

    return planned


@dataclass
class SceneResult:
    change: Change
    applied: bool = False
    writes: WriteReport = field(default_factory=WriteReport)
    reconciled: PruneResult | None = None
    entity_ids: tuple[str, ...] = ()
    iox_backup: Path | None = None


async def scene(
    iox: IoxClient,
    ha: HaClient,
    rails: Rails,
    iox_backup: IoxBackup,
    planner: Planner,
    *,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> SceneResult:
    change = planner(await iox.network())
    if not rails.apply:
        return SceneResult(change)
    backups: Sequence[Backup] = [iox_backup, HaRegistryBackup(ha)] if change.reconcile else [iox_backup]
    async with rails.changing("scene", backups):
        change = planner(await iox.network())  # again, under the lock
        result = SceneResult(change, iox_backup=iox_backup.path)
        progress(change.summary)
        for call in change.calls:
            await call(iox)
        await write_updates_to(iox, change.devices, prompt, progress, result.writes)
        if change.reconcile:
            progress("Reconciling HA: reloading the isy994 integration, then pruning")
            result.reconciled = await reconcile(iox, ha)
            if change.scene:
                network = await iox.network()
                created = network.scene(change.scene)
                if created is not None:
                    result.entity_ids = ha_entity_ids(created, network, await ha.registry())
        result.applied = True
    return result


def render(result: SceneResult) -> str:
    change = result.change
    if not result.applied:
        lines = [f"Would: {change.summary}"]
        if change.devices:
            lines.append("Then write the updates to " + ", ".join(d.label for d in change.devices))
        if change.reconcile:
            lines.append("Then Reconcile HA: reload the isy994 integration, then prune.")
        lines.append("Dry run: nothing changed. Pass --apply to make the change.")
        return "\n".join(lines) + "\n"
    lines = [f"Done: {change.summary}"]
    lines += result.writes.lines("  ")
    if result.reconciled is not None:
        lines.append(f"Reconciled HA: pruned {len(result.reconciled.removed)} Stale HA Devices.")
    if change.scene:
        lines.append("Its HA switch: " + (", ".join(result.entity_ids) or "none yet; check the isy994 integration"))
    if result.iox_backup is not None:
        lines.append(f"IoX backup: {result.iox_backup}")
    return "\n".join(lines) + "\n"


def _free_address(network: Network) -> str:
    """A Scene address the Controller does not use: one past the highest, else the lowest free one."""
    used = {int(scene.address) for scene in network.scenes if scene.address.isdigit()}
    highest = max(used, default=_FIRST_SCENE_ADDRESS - 1)
    if highest < _LAST_SCENE_ADDRESS:
        return str(max(highest + 1, _FIRST_SCENE_ADDRESS))
    free = next((n for n in range(_FIRST_SCENE_ADDRESS, _LAST_SCENE_ADDRESS) if n not in used), None)
    if free is None:
        raise ResolveError("no free Scene address; give one with --address")
    return str(free)


def _devices(network: Network, buttons: Sequence[ButtonAddress]) -> tuple[Device, ...]:
    addresses = {button.device for button in buttons}
    return tuple(device for device in network.devices if device.address in addresses)


def _label(network: Network, button: ButtonAddress) -> str:
    found = network.button(button)
    return f"{button} {found.name}" if found else str(button)
