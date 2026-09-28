"""Tests set sensors' states and attributes directly."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from testing.sensors import Sensors


async def test_a_sensor_carries_its_device_class_unit_and_name(
    hass: HomeAssistant, sensors: Sensors
) -> None:
    await sensors.set(
        "sensor.example_freezer_temperature",
        "-18.5",
        device_class="temperature",
        unit="°C",
        friendly_name="Example Freezer",
    )

    state = hass.states.get("sensor.example_freezer_temperature")
    assert state.state == "-18.5"
    assert state.attributes == {
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "friendly_name": "Example Freezer",
    }


async def test_battery_sensors_can_be_told_apart_by_integration(
    hass: HomeAssistant, sensors: Sensors
) -> None:
    await sensors.battery("sensor.example_lock_battery", 15, integration="example_zwave")
    await sensors.battery("sensor.example_phone_battery", 12, integration="example_mobile")

    low_without_phones = Template(
        "{{ states.sensor"
        " | selectattr('attributes.device_class', 'eq', 'battery')"
        " | rejectattr('entity_id', 'in', integration_entities('example_mobile'))"
        " | map(attribute='entity_id') | list }}",
        hass,
    ).async_render()

    assert low_without_phones == ["sensor.example_lock_battery"]
    assert hass.states.get("sensor.example_lock_battery").attributes[
        "unit_of_measurement"
    ] == "%"


async def test_setting_a_sensor_again_updates_its_state(
    hass: HomeAssistant, sensors: Sensors
) -> None:
    await sensors.battery("sensor.example_lock_battery", 50, integration="example_zwave")
    await sensors.battery("sensor.example_lock_battery", 10, integration="example_zwave")
    await sensors.set("binary_sensor.example_leak", "on", device_class="moisture")

    assert hass.states.get("sensor.example_lock_battery").state == "10"
    assert hass.states.get("binary_sensor.example_leak").state == "on"
