"""`insteon inventory`: what the Controller holds, read-only.

Devices and their Buttons, Scenes with their Controller and Responder Buttons,
the Modem's Link count against its capacity, and any non-Insteon nodes, which
are a misconfiguration (ADR 0012).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from insteon.address import NodeAddress
from insteon.ha import HaRegistry
from insteon.iox import IoxClient
from insteon.network import Device, LinkRecord, Modem, Network, Scene
from insteon.rails import Rails
from insteon.resolve import resolve_device

# Status reports stop after about 900 Links on a 2413S Modem (iox-api.md §6).
DEFAULT_CAPACITY = 900
WARN_AT = 0.8


@dataclass(frozen=True)
class Inventory:
    network: Network
    modem: Modem | None
    modem_links: Sequence[LinkRecord] | None
    """None when the Link table was not read."""
    capacity: int
    devices: Sequence[Device] | None = None
    """The Devices asked for; None means every Device."""

    @property
    def shown_devices(self) -> Sequence[Device]:
        return self.network.devices if self.devices is None else self.devices

    @property
    def shown_scenes(self) -> Sequence[Scene]:
        if self.devices is None:
            return self.network.scenes
        seen = {scene.address for device in self.devices for scene in self.network.scenes_of(device)}
        return [scene for scene in self.network.scenes if scene.address in seen]

    @property
    def links_in_use(self) -> int | None:
        return None if self.modem_links is None else sum(link.in_use for link in self.modem_links)


async def inventory(
    iox: IoxClient,
    rails: Rails,
    *,
    capacity: int = DEFAULT_CAPACITY,
    read_modem_links: bool = True,
    names: Sequence[str] = (),
    registry: HaRegistry | None = None,
) -> Inventory:
    """Read the Controller. `names` limits the Devices shown (any DEVICE format)."""
    network = await iox.network()
    devices = [resolve_device(name, network, registry) for name in names] if names else None
    modem = await iox.modem()
    links = None
    if read_modem_links:
        # Reading a Link table is a Modem operation: one at a time.
        with rails.modem_operation("inventory"):
            links = await iox.modem_links(modem.address)
    return Inventory(network, modem, links, capacity, devices)


def render(report: Inventory) -> str:
    network = report.network
    devices, scenes = report.shown_devices, report.shown_scenes
    sections = [f"Controller {network.uuid}: {len(devices)} Devices, {len(scenes)} Scenes"]

    lines = ["Devices"]
    for device in devices:
        notes = [f"[{device.folder}]"] if device.folder else []
        if not device.enabled:
            notes.append("(disabled)")
        if device.comms_error:
            notes.append("! IoX reports a communication error")
        lines.append("  " + "  ".join([str(device.address), device.name, device.kind, *notes]))
        lines.extend(f"    {button.address}  {button.name}" for button in device.buttons)
    sections.append("\n".join(lines))

    lines = ["Scenes"]
    for scene in scenes:
        lines.append(f"  {scene.name} ({scene.address})")
        members = [("Controller", node) for node in scene.controllers] + [
            ("Responder ", node) for node in scene.responders
        ]
        lines.extend(f"    {role}  {node}  {_button_name(network, node)}" for role, node in members)
        if not members:
            lines.append("    (no Buttons)")
        lines.append("")
    sections.append("\n".join(lines).rstrip())

    if report.modem is not None:
        modem = report.modem
        lines = [f"Modem {modem.address}  {modem.firmware}  {modem.status}"]
        in_use = report.links_in_use
        if in_use is None or report.modem_links is None:
            lines.append("  Links: not read")
        else:
            share = in_use / report.capacity
            lines.append(
                f"  Links: {in_use} in use of about {report.capacity} ({share:.0%}); "
                f"{len(report.modem_links)} records read"
            )
            if share >= WARN_AT:
                lines.append(
                    f"  WARNING: the Modem holds {in_use} Links, {share:.0%} of its capacity of about "
                    f"{report.capacity}. Status reports start failing near capacity; Restore the Modem "
                    "to compact its table, or remove unused Scenes."
                )
        sections.append("\n".join(lines))

    if report.devices is None and network.foreign:
        lines = ["Misconfiguration: nodes that are not Insteon belong to Home Assistant's own integrations (ADR 0012)"]
        lines.extend(f"  {node.address}  {node.name}  {node.family}" for node in network.foreign)
        sections.append("\n".join(lines))

    return "\n\n".join(sections) + "\n"


def _button_name(network: Network, node: NodeAddress) -> str:
    button = network.button(node)
    return button.name if button else "(not on the Controller)"
