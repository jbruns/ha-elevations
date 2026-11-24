"""The Link Audit's comparison of one Link table with what the Controller expects.

Links are compared by role (controller or responder), group and linked
address, then by data. Records that are not in use (deleted, or the end of the
table) are left out. Only flag bits 0x80 (in use) and 0x40 (controller) are
compared: the others are product specific, and on a live dimmer a working
Link read back with bit 0x08 set where the Controller expects it clear
(research/iox-api.md §4).

The Link Audit (`insteon.audit`) compares every table it reads this way, and a
write to a powered Device is confirmed this way (`insteon.rails`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from insteon.address import InsteonAddress
from insteon.network import Link, Network

_IDENTITY_MASK = 0xC0  # in use, controller


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

    @property
    def differs(self) -> bool:
        """The table holds other Links than the Controller expects: a difference, or a Stale Link it does not expect.

        A Restore leaves out a Link the Controller does not expect, so such a
        Stale Link differs; one the Controller expects is what it would write.
        """
        return bool(self.differences) or not all(entry.expected for entry in self.stale)


def audit_table(
    owner: InsteonAddress,
    expected: Sequence[Link],
    held: Sequence[Link],
    network: Network,
    modem: InsteonAddress,
    unresponsive: Collection[InsteonAddress] = (),
    *,
    compare_data: bool = True,
) -> TableAudit:
    """Compare the Links `owner` holds with those the Controller expects it to hold.

    A held Link to an address the Controller has no Device at, or to one of
    `unresponsive`, is a Stale Link rather than an `extra` difference.
    """
    expected_by_identity = _by_identity(in_use(expected))
    held_by_identity = _by_identity(in_use(held))

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


def in_use(links: Iterable[Link]) -> list[Link]:
    return [link for link in links if link.in_use]


def _identity(link: Link) -> tuple[int, int, InsteonAddress]:
    return link.flags & _IDENTITY_MASK, link.group, link.address


def _by_identity(links: Iterable[Link]) -> dict[tuple[int, int, InsteonAddress], list[Link]]:
    found: dict[tuple[int, int, InsteonAddress], list[Link]] = {}
    for link in links:
        found.setdefault(_identity(link), []).append(link)
    return found


def _staleness(
    address: InsteonAddress, network: Network, modem: InsteonAddress, unresponsive: Collection[InsteonAddress]
) -> str | None:
    """Why a Link to `address` is a Stale Link, or None when it is not."""
    if address == modem:
        return None
    if network.device(address) is None:
        return f"the Controller has no Device at {address}"
    if address in unresponsive:
        return f"{address} is an Unresponsive Device"
    return None
