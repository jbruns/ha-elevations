import pytest
from insteon_fakes import FakeHa, TOKEN
from insteon_fakes import fake_ha, ha_url  # noqa: F401 (fixtures)

from insteon.ha import HaClient, HaError


async def test_the_registry_holds_devices_entities_and_the_isy994_entries(ha_url: str) -> None:
    async with HaClient(ha_url, TOKEN) as ha:
        registry = await ha.registry()

    assert registry.isy994_entries == {"isy-entry"}
    assert "hue-bulb" not in {device["id"] for device in registry.isy994_devices()}
    assert [entity["entity_id"] for entity in registry.entities_of("dimmer")] == [
        "light.example_dimmer",
        "number.example_dimmer_on_level",
    ]


async def test_removing_a_device_from_its_config_entry_deletes_it(ha_url: str, fake_ha: FakeHa) -> None:
    async with HaClient(ha_url, TOKEN) as ha:
        await ha.remove_config_entry("gone", "isy-entry")
        registry = await ha.registry()

    assert "gone" not in {device["id"] for device in registry.devices}


async def test_a_refused_command_raises_with_home_assistants_reason(ha_url: str) -> None:
    async with HaClient(ha_url, TOKEN) as ha:
        with pytest.raises(HaError, match="not_found"):
            await ha.remove_config_entry("no-such-device", "isy-entry")


async def test_a_connection_closed_while_idle_is_reopened_for_the_next_command(
    ha_url: str, fake_ha: FakeHa
) -> None:
    fake_ha.close_after = 1  # e.g. a proxy dropping the socket during a long Modem Restore
    async with HaClient(ha_url, TOKEN) as ha:
        first = await ha.registry()
        second = await ha.registry()

    assert first.isy994_entries == second.isy994_entries == {"isy-entry"}
    assert fake_ha.connections >= 2


async def test_a_wrong_token_is_reported_plainly(ha_url: str) -> None:
    with pytest.raises(HaError, match="refused the access token"):
        async with HaClient(ha_url, "wrong"):
            pass


async def test_automations_list_the_entities_and_devices_each_references(ha_url: str) -> None:
    async with HaClient(ha_url, TOKEN) as ha:
        automations = {automation.entity_id: automation for automation in await ha.automations()}

    assert sorted(automations) == [
        "automation.example_bulb",
        "automation.example_dimmer_level",
        "automation.example_keypad_press",
    ]
    level = automations["automation.example_dimmer_level"]
    assert level.name == "Example dimmer level"
    assert level.entities == {"light.example_dimmer", "input_number.example_level"}
    assert automations["automation.example_keypad_press"].devices == {"keypad"}
