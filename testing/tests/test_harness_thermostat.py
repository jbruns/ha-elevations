"""A thermostat that records the settings automations give it."""

from homeassistant.core import HomeAssistant

from testing.thermostat import Thermostat


async def call(hass: HomeAssistant, service: str, data: dict) -> None:
    await hass.services.async_call("climate", service, data, blocking=True)


async def test_the_thermostat_supports_each_mode_and_target(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    state = hass.states.get(thermostat.entity_id)

    assert state.state == "off"
    assert state.attributes["hvac_modes"] == ["heat", "cool", "heat_cool", "off"]


async def test_the_thermostat_records_set_hvac_mode_and_set_temperature(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    await call(hass, "set_hvac_mode", {"entity_id": thermostat.entity_id, "hvac_mode": "heat"})
    await call(hass, "set_temperature", {"entity_id": thermostat.entity_id, "temperature": 20})
    await call(
        hass,
        "set_temperature",
        {
            "entity_id": thermostat.entity_id,
            "hvac_mode": "heat_cool",
            "target_temp_low": 19,
            "target_temp_high": 25,
        },
    )

    assert thermostat.calls == [
        ("set_hvac_mode", {"hvac_mode": "heat"}),
        ("set_temperature", {"temperature": 20}),
        ("set_temperature", {"hvac_mode": "heat_cool", "target_temp_low": 19, "target_temp_high": 25}),
    ]
    state = hass.states.get(thermostat.entity_id)
    assert state.state == "heat_cool"
    assert state.attributes["target_temp_low"] == 19
    assert state.attributes["target_temp_high"] == 25


async def test_a_test_sets_the_thermostats_starting_state(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    await thermostat.set(hvac_mode="cool", temperature=24, current_temperature=26)

    state = hass.states.get(thermostat.entity_id)
    assert state.state == "cool"
    assert state.attributes["temperature"] == 24
    assert state.attributes["current_temperature"] == 26
    assert thermostat.calls == []


async def test_turn_off_and_turn_on_are_recorded_as_themselves(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    await thermostat.set(hvac_mode="cool", temperature=24)

    await call(hass, "turn_off", {"entity_id": thermostat.entity_id})
    await call(hass, "turn_on", {"entity_id": thermostat.entity_id})

    assert thermostat.calls == [("turn_off", {}), ("turn_on", {})]
    # Like most thermostats, turning on resumes the mode it was turned off from.
    assert hass.states.get(thermostat.entity_id).state == "cool"
