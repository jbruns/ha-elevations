"""The fixtures work together, as a Climate blueprint's tests will use them."""

from datetime import timedelta

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations
from testing.clock import Clock
from testing.helpers import Helpers
from testing.sensors import Sensors
from testing.thermostat import Thermostat


async def test_a_door_left_open_turns_the_thermostat_off(
    hass: HomeAssistant,
    clock: Clock,
    helpers: Helpers,
    sensors: Sensors,
    thermostat: Thermostat,
) -> None:
    await thermostat.set(hvac_mode="heat", temperature=20)
    paused = await helpers.input_boolean("Example Door Pause")
    await sensors.set("binary_sensor.example_patio_door", "off", device_class="door")
    await async_setup_automations(
        hass,
        [
            {
                "alias": "Door Pause",
                "triggers": [
                    {
                        "trigger": "state",
                        "entity_id": "binary_sensor.example_patio_door",
                        "to": "on",
                        "for": {"minutes": 3},
                    }
                ],
                "actions": [
                    {
                        "action": "climate.set_hvac_mode",
                        "target": {"entity_id": thermostat.entity_id},
                        "data": {"hvac_mode": "off"},
                    },
                    {"action": "input_boolean.turn_on", "target": {"entity_id": paused}},
                ],
            }
        ],
    )

    await sensors.set("binary_sensor.example_patio_door", "on", device_class="door")
    await clock.advance(timedelta(minutes=3))

    assert thermostat.calls == [("set_hvac_mode", {"hvac_mode": "off"})]
    assert hass.states.get(paused).state == "on"
