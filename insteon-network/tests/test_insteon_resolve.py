from dataclasses import replace

import pytest
from insteon_fakes import FIXTURES, UUID, ha_devices, ha_entities

from insteon.ha import HaRegistry
from insteon.iox import parse_nodes
from insteon.network import Network
from insteon.resolve import ResolveError, ha_entity_ids, resolve_device

NETWORK = parse_nodes((FIXTURES / "nodes.xml").read_text(), UUID)
REGISTRY = HaRegistry(devices=ha_devices(), entities=ha_entities(), config_entries=[{"entry_id": "isy-entry", "domain": "isy994"}])


def name_of(text: str, network: Network = NETWORK, registry: HaRegistry | None = REGISTRY) -> str:
    return resolve_device(text, network, registry).name


@pytest.mark.parametrize("text", ["AA.BB.CC", "aabbcc", "AA BB CC 1", "aa:bb:cc"])
def test_an_insteon_address_in_any_format_resolves_to_its_device(text: str) -> None:
    assert name_of(text) == "Example Dimmer"


@pytest.mark.parametrize("text", ["0A.0B.0C", "A B C", "A B C 1"])
def test_an_address_with_dropped_leading_zeros_resolves(text: str) -> None:
    assert name_of(text) == "Example Relay"


def test_a_device_or_button_name_on_iox_resolves_to_the_device() -> None:
    assert name_of("Example Dimmer") == "Example Dimmer"
    assert name_of("example keypad a") == "Example Keypad"


def test_an_ha_entity_resolves_through_its_unique_id() -> None:
    assert name_of("light.example_dimmer") == "Example Dimmer"
    assert name_of("event.example_keypad_a") == "Example Keypad"
    assert name_of("light.example_relay") == "Example Relay"


def test_an_ha_entity_of_a_scene_is_not_a_device() -> None:
    with pytest.raises(ResolveError, match="Scene"):
        resolve_device("switch.example_scene", NETWORK, REGISTRY)


def test_an_address_the_controller_does_not_have_is_reported() -> None:
    with pytest.raises(ResolveError, match="no Device at 33.44.55"):
        resolve_device("33.44.55", NETWORK, REGISTRY)


def test_an_unknown_name_or_entity_is_reported() -> None:
    with pytest.raises(ResolveError, match="no Device"):
        resolve_device("Example Nothing", NETWORK, REGISTRY)
    with pytest.raises(ResolveError, match="light.example_nothing"):
        resolve_device("light.example_nothing", NETWORK, REGISTRY)


def test_an_entity_id_needs_home_assistant() -> None:
    with pytest.raises(ResolveError, match="Home Assistant"):
        resolve_device("light.example_dimmer", NETWORK, None)


def test_a_name_two_devices_share_is_ambiguous() -> None:
    twin = replace(NETWORK.devices[0], name="Example Dimmer")
    network = Network(uuid=UUID, devices=(*NETWORK.devices, twin), scenes=())

    with pytest.raises(ResolveError, match="ambiguous"):
        resolve_device("Example Dimmer", network, None)


@pytest.mark.parametrize(
    ("address", "entity_ids"),
    [
        ("AA.BB.CC", ("light.example_dimmer", "number.example_dimmer_on_level")),
        ("11.22.33", ("event.example_keypad_a",)),
        ("22.33.44", ()),
    ],
)
def test_a_device_resolves_to_its_entities_in_home_assistant(address: str, entity_ids: tuple[str, ...]) -> None:
    device = resolve_device(address, NETWORK, REGISTRY)

    assert ha_entity_ids(device, NETWORK, REGISTRY) == entity_ids


def test_a_scene_resolves_to_its_entity_in_home_assistant() -> None:
    scene = next(scene for scene in NETWORK.scenes if scene.address == "12345")

    assert ha_entity_ids(scene, NETWORK, REGISTRY) == ("switch.example_scene",)
