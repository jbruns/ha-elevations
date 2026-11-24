"""Resolution: a DEVICE argument names exactly one Device, and likewise a Button or a Scene.

It may be an Insteon Address in any format (`1A.2B.3C`, `AA B C 1`), the IoX
name of the Device or of one of its Buttons, or the ID of a Home Assistant
entity from the Controller integration. The way back, from a Device or Scene to
its Home Assistant entities, is `ha_entity_ids`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from insteon.address import ButtonAddress, InsteonAddress
from insteon.ha import DOMAIN, HaRegistry, controller_suffix
from insteon.network import Device, Network, Scene

_ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")


def needs_home_assistant(text: str) -> bool:
    """Whether resolving `text` means reading Home Assistant: it is an entity ID."""
    query = text.strip()
    return device_address(query) is None and bool(_ENTITY_ID.fullmatch(query))


class ResolveError(LookupError):
    """The argument names no Device, or more than one."""


def resolve_device(text: str, network: Network, registry: HaRegistry | None) -> Device:
    """The one Device `text` names. Raises ResolveError otherwise."""
    query = text.strip()
    address = device_address(query)
    if address is not None:
        device = network.device(address)
        if device is None:
            raise ResolveError(f"the Controller has no Device at {address}")
        return device
    if _ENTITY_ID.fullmatch(query):
        return _from_entity(query, network, registry)
    return _from_name(query, network)


def resolve_scene(text: str, network: Network) -> Scene:
    """The one Scene `text` names: its Scene address (`12345`) or its IoX name."""
    query = text.strip()
    found = network.scene(query)
    if found is not None:
        return found
    named = [scene for scene in network.scenes if scene.name.casefold() == query.casefold()]
    if not named:
        raise ResolveError(f"no Scene on the Controller is at or named {query!r}")
    if len(named) > 1:
        listed = ", ".join(f"{scene.name} ({scene.address})" for scene in named)
        raise ResolveError(f"{query!r} is ambiguous: {listed}; give the Scene address")
    return named[0]


def resolve_button(text: str, network: Network, registry: HaRegistry | None) -> ButtonAddress:
    """The one Button `text` names: a Button address (`AA BB CC 3`) or a Button's IoX name.

    Anything else that names a Device (its Insteon Address, name or HA entity)
    names its primary Button: the load of a dimmer or relay.
    """
    query = text.strip()
    button = ButtonAddress.try_parse(query)
    if button is not None:
        if network.button(button) is None:
            raise ResolveError(f"the Controller has no Button at {button}")
        return button
    named = [
        b.address
        for device in network.devices
        for b in device.buttons
        if b.name.casefold() == query.casefold() and b.address != device.primary
    ]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise ResolveError(f"{query!r} is ambiguous: {', '.join(map(str, named))}; give the Button address")
    return resolve_device(query, network, registry).primary


def ha_entity_ids(target: Device | Scene, network: Network, registry: HaRegistry) -> tuple[str, ...]:
    """The Controller integration's entities for a Device (any of its Buttons) or a Scene."""
    found = []
    for entity in registry.entities:
        if entity.get("platform") != DOMAIN:
            continue
        if isinstance(target, Scene):
            suffix = controller_suffix(str(entity.get("unique_id", "")), network.uuid)
            matches = suffix is not None and suffix.split("_", 1)[0] == target.address
        else:
            place = place_of(entity, network.uuid)
            matches = place is not None and place.button.device == target.address
        if matches:
            found.append(str(entity["entity_id"]))
    return tuple(sorted(found))


@dataclass(frozen=True)
class EntityPlace:
    """Where an isy994 entity sits: its domain, its Button, and the rest of its unique ID (`OL` in `..._OL`)."""

    domain: str
    button: ButtonAddress
    suffix: str


def place_of(entity: dict[str, Any], uuid: str) -> EntityPlace | None:
    """Where an isy994 entity sits, from its ID and its unique ID `<uuid>_AA BB CC 1[_OL]`.

    None for a Scene's switch, a variable, or another Controller's entity.
    Button addresses hold spaces, never underscores, so the first `_` ends it.
    """
    suffix = controller_suffix(str(entity.get("unique_id", "")), uuid)
    address, _, rest = (suffix or "").partition("_")
    button = ButtonAddress.try_parse(address)
    if button is None:
        return None
    return EntityPlace(str(entity.get("entity_id", "")).split(".", 1)[0], button, rest)


def device_address(text: str) -> InsteonAddress | None:
    """The Insteon Address `text` gives as an Insteon or Button address, or None."""
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
