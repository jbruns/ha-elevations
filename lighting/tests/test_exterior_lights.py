"""Behaviour of the Exterior Lights blueprint, run in a real Home Assistant core."""

from dataclasses import dataclass
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from testing.automations import async_setup_automations, blueprint_automation
from testing.lights import Lights
from testing.sun import Sun

BLUEPRINT = "lighting/blueprints/automation/lighting_exterior_lights.yaml"


@dataclass
class ExteriorLights:
    hass: HomeAssistant
    lights: Lights
    sun: Sun

    async def start(self, **inputs: Any) -> None:
        assert await async_setup_component(self.hass, "homeassistant", {})
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "exterior_lights": {
                            "entity_id": [
                                self.lights.follower.entity_id,
                                self.lights.leader_switch.entity_id,
                            ]
                        },
                        **inputs,
                    },
                    alias="Exterior Lights",
                )
            ],
        )

    async def elevation(self, elevation: float) -> None:
        await self.sun.set_elevation(elevation)
        await self.hass.async_block_till_done()

    async def restart(self) -> None:
        self.hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await self.hass.async_block_till_done()

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return self.lights.follower.calls + self.lights.leader_switch.calls


async def test_dusk_turns_exterior_lights_and_switches_on(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(2.0)
    await exterior.start()

    await exterior.elevation(1.4)

    assert exterior.calls == [("turn_on", {}), ("turn_on", {})]


async def test_dawn_turns_exterior_lights_and_switches_off(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(0.0)
    await exterior.start()

    await exterior.elevation(1.1)

    assert exterior.calls == [("turn_off", {}), ("turn_off", {})]


async def test_elevation_between_the_thresholds_does_nothing(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(1.1)
    await exterior.start()

    await exterior.elevation(1.2)

    assert exterior.calls == []


async def test_custom_thresholds_control_the_dusk_and_dawn_crossings(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(4.0)
    await exterior.start(on_below=3.0, off_above=2.0)

    await exterior.elevation(2.5)
    await exterior.elevation(1.5)
    await exterior.elevation(2.5)

    assert exterior.calls == [
        ("turn_on", {}),
        ("turn_off", {}),
        ("turn_on", {}),
        ("turn_off", {}),
    ]


async def test_home_assistant_starting_mid_evening_turns_the_exterior_lights_on(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(-6.0)
    await exterior.start()

    await exterior.restart()

    assert exterior.calls == [("turn_on", {}), ("turn_on", {})]


async def test_home_assistant_starting_in_daytime_turns_the_exterior_lights_off(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(10.0)
    await exterior.start()

    await exterior.restart()

    assert exterior.calls == [("turn_off", {}), ("turn_off", {})]


async def test_home_assistant_starting_between_thresholds_does_nothing(
    hass: HomeAssistant, lights: Lights, sun: Sun
) -> None:
    exterior = ExteriorLights(hass, lights, sun)
    await sun.set_elevation(1.2)
    await exterior.start()

    await exterior.restart()

    assert exterior.calls == []
