"""Device resolution: a DEVICE argument names exactly one Device.

It may be an Insteon Address in any format (`1A.2B.3C`, `AA B C 1`), the IoX
name of the Device or of one of its Buttons, or the ID of a Home Assistant
entity from the Controller integration.
"""

from __future__ import annotations

import re

from insteon.address import InsteonAddress, NodeAddress
from insteon.ha import HaRegistry
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
    node = NodeAddress.try_parse(text)
    return node.device if node else InsteonAddress.try_parse(text)


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
    entity = next(
        (e for e in registry.entities if e.get("entity_id") == entity_id and e.get("platform") == "isy994"),
        None,
    )
    if entity is None:
        raise ResolveError(f"{entity_id} is not an entity of the Controller integration (isy994)")

    # The unique_id is `<uuid>_<node address>[_<suffix>]`; node addresses hold
    # spaces, never underscores.
    prefix = f"{network.uuid}_"
    unique_id = str(entity.get("unique_id", ""))
    if unique_id.startswith(prefix):
        rest = unique_id[len(prefix) :].split("_", 1)[0]
        node = NodeAddress.try_parse(rest)
        if node is not None and (device := network.device(node.device)) is not None:
            return device
        scene = next((scene for scene in network.scenes if scene.address == rest), None)
        if scene is not None:
            raise ResolveError(f"{entity_id} is the Scene {scene.name!r}, not a Device")

    device_id = entity.get("device_id")
    for ha_device in registry.devices:
        if ha_device.get("id") != device_id:
            continue
        for domain, identifier in ha_device.get("identifiers", []):
            if domain == "isy994" and identifier.startswith(prefix):
                node = NodeAddress.try_parse(identifier[len(prefix) :])
                if node is not None and (device := network.device(node.device)) is not None:
                    return device
    raise ResolveError(f"{entity_id} matches no Device the Controller has")
