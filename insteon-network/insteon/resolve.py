"""Device resolution: a DEVICE argument names exactly one Device.

It may be an Insteon Address in any format (`1A.2B.3C`, `AA B C 1`), the IoX
name of the Device or of one of its Buttons, or the ID of a Home Assistant
entity from the Controller integration.
"""

from __future__ import annotations

import re

from insteon.address import ButtonAddress, InsteonAddress
from insteon.ha import DOMAIN, HaRegistry, controller_suffix
from insteon.network import Device, Network

_ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")


def needs_home_assistant(text: str) -> bool:
    """Whether resolving `text` means reading Home Assistant: it is an entity ID."""
    query = text.strip()
    return _address(query) is None and bool(_ENTITY_ID.fullmatch(query))


class ResolveError(LookupError):
    """The argument names no Device, or more than one."""


def resolve_device(text: str, network: Network, registry: HaRegistry | None) -> Device:
    """The one Device `text` names. Raises ResolveError otherwise."""
    query = text.strip()
    address = _address(query)
    if address is not None:
        device = network.device(address)
        if device is None:
            raise ResolveError(f"the Controller has no Device at {address}")
        return device
    if _ENTITY_ID.fullmatch(query):
        return _from_entity(query, network, registry)
    return _from_name(query, network)


def _address(text: str) -> InsteonAddress | None:
    button = ButtonAddress.try_parse(text)
    return button.device if button else InsteonAddress.try_parse(text)


def _from_name(name: str, network: Network) -> Device:
    wanted = name.casefold()
    found = [
        device
        for device in network.devices
        if device.name.casefold() == wanted or any(button.name.casefold() == wanted for button in device.buttons)
    ]
    if not found:
        raise ResolveError(f"no Device or Button on the Controller is named {name!r}")
    if len(found) > 1:
        listed = ", ".join(f"{device.name} ({device.address})" for device in found)
        raise ResolveError(f"{name!r} is ambiguous: {listed}; give the Insteon Address")
    return found[0]


def _from_entity(entity_id: str, network: Network, registry: HaRegistry | None) -> Device:
    if registry is None:
        raise ResolveError(f"{entity_id} is an entity ID; resolving it needs Home Assistant")
    entity = registry.isy994_entity(entity_id)
    if entity is None:
        raise ResolveError(f"{entity_id} is not an entity of the Controller integration ({DOMAIN})")

    # The unique_id's suffix is a Button address, perhaps with its own `_OL`-style
    # suffix (Button addresses hold spaces, never underscores), or a Scene address.
    suffix = controller_suffix(str(entity.get("unique_id", "")), network.uuid)
    if suffix is not None:
        address = suffix.split("_", 1)[0]
        if (device := _device_at(address, network)) is not None:
            return device
        scene = next((scene for scene in network.scenes if scene.address == address), None)
        if scene is not None:
            raise ResolveError(f"{entity_id} is the Scene {scene.name!r}, not a Device")

    ha_device = registry.device(entity.get("device_id"))
    for identifier in registry.isy994_identifiers(ha_device) if ha_device else []:
        suffix = controller_suffix(identifier, network.uuid)
        if suffix is not None and (device := _device_at(suffix, network)) is not None:
            return device
    raise ResolveError(f"{entity_id} matches no Device the Controller has")


def _device_at(text: str, network: Network) -> Device | None:
    button = ButtonAddress.try_parse(text)
    return network.device_of(button) if button else None
