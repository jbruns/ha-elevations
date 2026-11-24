"""The Controller's picture of the Insteon network: Devices, Buttons and Scenes.

These types know nothing about IoX's wire formats; `insteon.iox` builds them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insteon.address import InsteonAddress, ButtonAddress


@dataclass(frozen=True)
class Button:
    """One part of a Device that can join a Scene."""

    address: ButtonAddress
    name: str
    model: str
    """IoX's node definition, such as `KeypadButton_ADV`."""


@dataclass(frozen=True)
class Device:
    """One Insteon product at one Insteon Address, with its Buttons."""

    address: InsteonAddress
    name: str
    model: str
    """IoX's node definition, such as `DimmerLampSwitch_ADV`."""
    type_code: str
    """IoX's Insteon category, subcategory and firmware, such as `1.32.65.0`."""
    folder: str | None
    enabled: bool
    comms_error: bool
    """IoX flags the Device as failing to communicate. Not yet a verdict that
    it is an Unresponsive Device: that takes a Link Audit."""
    buttons: tuple[Button, ...]

    @property
    def label(self) -> str:
        """The Device as reports name it: `1A.2B.3C Name`."""
        return f"{self.address} {self.name}"

    @property
    def primary(self) -> ButtonAddress:
        """The address IoX reads the Device's Links through: its first Button."""
        return self.buttons[0].address if self.buttons else ButtonAddress(self.address, 1)

    @property
    def battery(self) -> bool:
        """A Battery Device, judged from its Insteon category (`type_code`).

        Category 16 (sensors and actuators) is battery powered, except the
        Smoke Bridge (16.10), which plugs in. Category 0 (generalized
        controllers: remotes) is battery powered. See research/iox-api.md §6.
        """
        category, _, rest = self.type_code.partition(".")
        subcategory = rest.partition(".")[0]
        if category == "16":
            return subcategory != "10"
        return category == "0"


@dataclass(frozen=True)
class Scene:
    """A set of Buttons that act together without the Controller."""

    address: str
    name: str
    controllers: tuple[ButtonAddress, ...]
    responders: tuple[ButtonAddress, ...]
    group: int | None = None
    """The Insteon group the Modem controls the Scene with (IoX's `deviceGroup`)."""


@dataclass(frozen=True)
class ForeignNode:
    """A node on the Controller that is not Insteon: a misconfiguration (ADR 0012)."""

    address: str
    name: str
    family: str


@dataclass(frozen=True)
class Network:
    """Everything the Controller holds, as read in one go."""

    uuid: str
    devices: tuple[Device, ...]
    scenes: tuple[Scene, ...]
    foreign: tuple[ForeignNode, ...] = ()
    node_addresses: frozenset[str] = field(default=frozenset())
    """Every node address the Controller has, Insteon or not, as IoX writes it."""

    def device(self, address: InsteonAddress) -> Device | None:
        return next((device for device in self.devices if device.address == address), None)

    def device_of(self, button: ButtonAddress) -> Device | None:
        """The Device a Button address belongs to."""
        return self.device(button.device)

    def scene(self, address: str) -> Scene | None:
        return next((scene for scene in self.scenes if scene.address == address), None)

    def button(self, address: ButtonAddress) -> Button | None:
        device = self.device(address.device)
        if device is None:
            return None
        return next((button for button in device.buttons if button.address == address), None)

    def scenes_served_by(self, owner: InsteonAddress, link: Link, modem: InsteonAddress) -> tuple[Scene, ...]:
        """The Scenes a Link in `owner`'s table serves: those its controller's Button drives.

        When the Modem is the controller, `link.group` is the Scene's group.
        """
        controller = owner if link.controller else link.address
        if controller == modem:
            return tuple(scene for scene in self.scenes if scene.group == link.group)
        button = ButtonAddress(controller, link.group)
        return tuple(scene for scene in self.scenes if button in scene.controllers)

    def scenes_of(self, device: Device) -> tuple[Scene, ...]:
        """The Scenes any of the Device's Buttons is in."""
        return tuple(
            scene
            for scene in self.scenes
            if any(member.device == device.address for member in (*scene.controllers, *scene.responders))
        )


@dataclass(frozen=True)
class Link:
    """One record in a Device's or the Modem's link table, as IoX reports it."""

    index: int
    flags: int
    group: int
    address: InsteonAddress
    data: int

    @property
    def in_use(self) -> bool:
        """Flag bit 0x80. A record without it is deleted, or ends the table."""
        return bool(self.flags & 0x80)

    @property
    def controller(self) -> bool:
        """Flag bit 0x40: the table's owner controls `address` with `group`.

        Without it, the owner responds to `address`'s `group`.
        """
        return bool(self.flags & 0x40)


@dataclass(frozen=True)
class Modem:
    """The Controller's Insteon radio and powerline interface."""

    address: InsteonAddress
    firmware: str
    status: str
