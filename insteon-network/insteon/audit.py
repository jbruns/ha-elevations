"""`insteon audit`: the Link Audit, read-only.

For each Device, the Links it actually holds (`G_DEV_ALL`) are compared with
the Links the Controller expects it to hold (`G_ISY_ALL`). The Controller has
no expected table for the Modem, so the Modem's expected Links are derived
from the Devices' expected tables: every Link a Device holds with the Modem has
its mirror in the Modem's table, with the same group and the roles swapped.

Links are compared by role (controller or responder), group and linked
address, then by data. Records that are not in use (deleted, or the end of the
table) are left out. Only flag bits 0x80 (in use) and 0x40 (controller) are
compared: the others are product specific, and on a live dimmer a working
Link read back with bit 0x08 set where the Controller expects it clear
(research/iox-api.md §4).

Reading the Links of one Device is a Modem operation, so the whole audit holds
the lock, though it changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from insteon.address import InsteonAddress
from insteon.ha import HaRegistry
from insteon.iox import DEVICE_READ_TIMEOUT, DeviceUnresponsive, IoxClient
from insteon.network import Device, Link, Modem, Network, Scene
from insteon.rails import Rails
from insteon.resolve import resolve_device

DEFAULT_TIMEOUT = DEVICE_READ_TIMEOUT

_IDENTITY_MASK = 0xC0  # in use, controller
# A plain in-use record of each role, as IoX writes them (research/iox-api.md §4).
_RESPONDER_FLAGS, _CONTROLLER_FLAGS = 0xA2, 0xE2


@dataclass(frozen=True)
class Difference:
    """A Link the Device holds and the Controller does not expect, or the reverse."""

    kind: Literal["missing", "extra", "data"]
    """`missing` (expected, not held), `extra` (held, not expected) or `data` (both, different data)."""
    link: Link
    held_data: int | None = None
    """For `data`: what the Device holds; `link.data` is what the Controller expects."""


@dataclass(frozen=True)
class StaleLink:
    owner: InsteonAddress
    """The Device, or the Modem, whose table holds the Link."""
    link: Link
    reason: str
    expected: bool
    """The Controller expects the Link too."""


@dataclass(frozen=True)
class TableAudit:
    """One Device's or the Modem's Links, compared with the Controller's."""

    owner: InsteonAddress
    differences: tuple[Difference, ...]
    stale: tuple[StaleLink, ...]


@dataclass(frozen=True)
class Unresponsive:
    device: Device
    reason: str


@dataclass(frozen=True)
class Skipped:
    device: Device
    reason: str


@dataclass(frozen=True)
class AuditReport:
    network: Network
    modem: Modem
    tables: tuple[TableAudit, ...]
    """Every Device read, then the Modem if its table was read."""
    unresponsive: tuple[Unresponsive, ...]
    skipped: tuple[Skipped, ...]
    named: bool = False
    """Only some Devices were audited."""
    modem_read: bool = True

    @property
    def stale(self) -> list[StaleLink]:
        return [stale for table in self.tables for stale in table.stale]

    @property
    def unresponsive_addresses(self) -> set[InsteonAddress]:
        return _addresses(self.unresponsive)

    @property
    def audited(self) -> int:
        return sum(table.owner != self.modem.address for table in self.tables) + len(self.unresponsive)


async def audit(
    iox: IoxClient,
    rails: Rails,
    *,
    names: Sequence[str] = (),
    registry: HaRegistry | None = None,
    read_modem_links: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> AuditReport:
    """Run the Link Audit on every Device, or only on the ones `names` gives.

    A Battery Device or a disabled Device is skipped unless named. A named
    Battery Device is read after `prompt` asks the Administrator to hold its
    Set button; answering `s` skips it.
    """
    network = await iox.network()
    devices = _unique([resolve_device(name, network, registry) for name in names]) if names else list(network.devices)
    with rails.modem_operation("audit"):
        return await audit_devices(
            iox,
            network,
            devices,
            named=bool(names),
            read_modem_links=read_modem_links,
            timeout=timeout,
            prompt=prompt,
            progress=progress,
        )


async def audit_devices(
    iox: IoxClient,
    network: Network,
    devices: Sequence[Device],
    *,
    named: bool,
    read_modem_links: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    prompt: Callable[[str], str] = input,
    progress: Callable[[str], None] = lambda message: None,
) -> AuditReport:
    """The Link Audit of these Devices. The caller holds the lock.

    Unless `named`, Battery and disabled Devices are skipped. With `named`,
    the Modem's Links are compared only where they link to these Devices.
    """
    modem = await iox.modem()
    to_read, skipped = _to_read(devices, named=named)

    held: dict[InsteonAddress, list[Link]] = {}
    unresponsive: list[Unresponsive] = []
    modem_links: list[Link] | None = None
    progress(f"Reading the Links the Controller expects for {len(devices)} Devices")
    expected = {device.address: await iox.expected_links(device.primary) for device in devices}
    for number, device in enumerate(to_read, 1):
        label = f"[{number}/{len(to_read)}] {device.address} {device.name}"
        if device.battery:
            answer = prompt(
                f"{label} is a Battery Device. Hold its Set button until it enters linking mode, "
                "then press Enter (or type s to skip it): "
            )
            if answer.strip().lower().startswith("s"):
                skipped.append(Skipped(device, "a Battery Device; skipped at the prompt"))
                continue
        progress(f"{label}: reading its Links")
        try:
            held[device.address] = await iox.device_links(device.primary, timeout=timeout)
        except DeviceUnresponsive as error:
            progress(f"{label}: Unresponsive ({error})")
            unresponsive.append(Unresponsive(device, str(error)))
        else:
            progress(f"{label}: {sum(link.in_use for link in held[device.address])} Links")
    if read_modem_links:
        progress("Reading the Modem's Links (about 2 a second)")
        modem_links = await iox.modem_links(modem.address)

    unresponsive_addresses = _addresses(unresponsive)
    tables = [
        _audit_table(address, expected[address], links, network, modem.address, unresponsive_addresses)
        for address, links in held.items()
    ]
    if modem_links is not None:
        scope = {device.address for device in devices}
        held_by_modem = [link for link in modem_links if not named or link.address in scope]
        mirrored = [
            _mirror(address, link)
            for address, links in expected.items()
            for link in _in_use(links)
            if link.address == modem.address
        ]
        tables.append(
            _audit_table(
                modem.address,
                mirrored,
                held_by_modem,
                network,
                modem.address,
                unresponsive_addresses,
                compare_data=False,
            )
        )
    return AuditReport(
        network=network,
        modem=modem,
        tables=tuple(tables),
        unresponsive=tuple(unresponsive),
        skipped=tuple(skipped),
        named=named,
        modem_read=modem_links is not None,
    )


def _addresses(unresponsive: Iterable[Unresponsive]) -> set[InsteonAddress]:
    return {entry.device.address for entry in unresponsive}


def _to_read(devices: Sequence[Device], *, named: bool) -> tuple[list[Device], list[Skipped]]:
    """The Devices to read, and those skipped: Battery and disabled ones, unless named."""
    if named:
        return list(devices), []
    to_read, skipped = [], []
    for device in devices:
        if not device.enabled:
            skipped.append(Skipped(device, "disabled; name it to audit it"))
        elif device.battery:
            skipped.append(Skipped(device, "a Battery Device; name it to audit it"))
        else:
            to_read.append(device)
    return to_read, skipped


def _unique(devices: Iterable[Device]) -> list[Device]:
    seen: dict[InsteonAddress, Device] = {}
    for device in devices:
        seen.setdefault(device.address, device)
    return list(seen.values())


def _in_use(links: Iterable[Link]) -> list[Link]:
    return [link for link in links if link.in_use]


def _identity(link: Link) -> tuple[int, int, InsteonAddress]:
    return link.flags & _IDENTITY_MASK, link.group, link.address


def _mirror(owner: InsteonAddress, link: Link) -> Link:
    """The Modem's side of a Link `owner` holds with the Modem."""
    flags = _RESPONDER_FLAGS if link.controller else _CONTROLLER_FLAGS
    return Link(index=-1, flags=flags, group=link.group, address=owner, data=0)


def _audit_table(
    owner: InsteonAddress,
    expected: Sequence[Link],
    held: Sequence[Link],
    network: Network,
    modem: InsteonAddress,
    unresponsive: set[InsteonAddress],
    *,
    compare_data: bool = True,
) -> TableAudit:
    expected_by_identity = _by_identity(_in_use(expected))
    held_by_identity = _by_identity(_in_use(held))

    stale = []
    for identity, links in held_by_identity.items():
        reason = _staleness(links[0].address, network, modem, unresponsive)
        if reason is not None:
            stale.append(StaleLink(owner, links[0], reason, expected=identity in expected_by_identity))
    stale_identities = {_identity(entry.link) for entry in stale}

    differences = []
    for identity, links in expected_by_identity.items():
        if identity not in held_by_identity:
            differences.append(Difference("missing", links[0]))
        elif compare_data and {link.data for link in links} != {link.data for link in held_by_identity[identity]}:
            differences.append(Difference("data", links[0], held_data=held_by_identity[identity][0].data))
    for identity, links in held_by_identity.items():
        if identity not in expected_by_identity and identity not in stale_identities:
            differences.append(Difference("extra", links[0]))
    return TableAudit(owner, tuple(differences), tuple(stale))


def _by_identity(links: Iterable[Link]) -> dict[tuple[int, int, InsteonAddress], list[Link]]:
    found: dict[tuple[int, int, InsteonAddress], list[Link]] = {}
    for link in links:
        found.setdefault(_identity(link), []).append(link)
    return found


def _staleness(
    address: InsteonAddress, network: Network, modem: InsteonAddress, unresponsive: set[InsteonAddress]
) -> str | None:
    """Why a Link to `address` is a Stale Link, or None when it is not."""
    if address == modem:
        return None
    if network.device(address) is None:
        return f"the Controller has no Device at {address}"
    if address in unresponsive:
        return f"{address} is an Unresponsive Device"
    return None


# Rendering ----------------------------------------------------------------


def render(report: AuditReport) -> str:
    network, modem = report.network, report.modem
    scope = "the named Devices" if report.named else "every Device"
    modem_note = "and the Modem" if report.modem_read else "but not the Modem (--no-modem-links)"
    sections = [
        f"Link Audit of {scope} {modem_note}: {report.audited} Devices read, "
        f"{len(report.unresponsive)} Unresponsive, {len(report.skipped)} skipped"
    ]

    if report.unresponsive:
        lines = [f"Unresponsive Devices ({len(report.unresponsive)})"]
        for entry in report.unresponsive:
            lines.append(f"  {_device_label(entry.device)}")
            lines.append(f"    {entry.reason}")
            lines.append(f"    Scenes: {_scene_list(network.scenes_of(entry.device))}")
        lines.append("  Only the Administrator decides whether to Remove or Restore each one.")
        sections.append("\n".join(lines))
    else:
        sections.append("No Unresponsive Devices.")

    stale = report.stale
    if stale:
        lines = [f"Stale Links ({len(stale)})"]
        for entry in stale:
            expected = "the Controller expects it" if entry.expected else "the Controller does not expect it"
            owner = _address_label(network, modem, entry.owner)
            lines.append(f"  in {owner}: {_link_text(network, modem, entry.link)}")
            lines.append(f"    {entry.reason}; {expected}")
            scenes = network.scenes_served_by(entry.owner, entry.link, modem.address)
            if scenes:
                lines.append(f"    Scenes: {_scene_list(scenes)}")
        sections.append("\n".join(lines))
    else:
        sections.append("No Stale Links.")

    differing = [table for table in report.tables if table.differences]
    if differing:
        count = sum(len(table.differences) for table in differing)
        lines = [
            f"Differences ({count}, in {len(differing)} tables)",
            "  missing: the Controller expects the Link and the table lacks it; "
            "extra: the table holds a Link the Controller does not expect",
        ]
        for table in differing:
            lines.append(f"  {_address_label(network, modem, table.owner)}")
            for difference in table.differences:
                text = _difference_text(network, modem, difference)
                if difference.kind == "missing" and difference.link.address in report.unresponsive_addresses:
                    text += " (an Unresponsive Device)"
                lines.append(f"    {text}")
                scenes = network.scenes_served_by(table.owner, difference.link, modem.address)
                if scenes:
                    lines.append(f"      Scenes: {_scene_list(scenes)}")
        sections.append("\n".join(lines))
    else:
        sections.append("No differences.")

    if report.skipped:
        lines = [f"Skipped ({len(report.skipped)})"]
        lines.extend(f"  {_device_label(entry.device)}: {entry.reason}" for entry in report.skipped)
        sections.append("\n".join(lines))

    if report.named and report.unresponsive:
        sections.append("Only the Devices named were read, so Links to other Unresponsive Devices are not flagged.")
    return "\n\n".join(sections) + "\n"


def _difference_text(network: Network, modem: Modem, difference: Difference) -> str:
    link_text = _link_text(network, modem, difference.link)
    if difference.kind == "data":
        held = difference.held_data if difference.held_data is not None else 0
        link_text += f": holds {_data(held)}, the Controller expects {_data(difference.link.data)}"
    return f"{difference.kind:<8} {link_text}"


def _link_text(network: Network, modem: Modem, link: Link) -> str:
    other = _address_label(network, modem, link.address)
    if link.controller:
        return f"controls {other} with group {link.group}"
    return f"responds to {other} group {link.group}"


def _address_label(network: Network, modem: Modem, address: InsteonAddress) -> str:
    if address == modem.address:
        return f"the Modem ({address})"
    device = network.device(address)
    return f"{address} {device.name}" if device else f"{address} (not on the Controller)"


def _device_label(device: Device) -> str:
    folder = f"  [{device.folder}]" if device.folder else ""
    return f"{device.address}  {device.name}{folder}"


def _scene_list(scenes: Iterable[Scene]) -> str:
    names = [f"{scene.name} ({scene.address})" for scene in scenes]
    return ", ".join(names) if names else "none"


def _data(value: int) -> str:
    return f"{(value >> 16) & 0xFF:02X} {(value >> 8) & 0xFF:02X} {value & 0xFF:02X}"
