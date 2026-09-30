"""Behaviour of the Follow blueprint, run in a real Home Assistant core."""

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.lights import Lights, SettableLight, SettableSwitch

BLUEPRINT = "lighting/blueprints/automation/lighting_follow.yaml"


@dataclass
class Follow:
    hass: HomeAssistant
    lights: Lights

    async def start(self, leader: SettableLight | SettableSwitch, **inputs: Any) -> None:
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "leader": leader.entity_id,
                        "follower": self.lights.follower.entity_id,
                        **inputs,
                    },
                    alias="Follow",
                )
            ],
        )

    async def switch_leader(self, on: bool) -> None:
        await self.lights.leader_switch.set(on=on)
        await self.hass.async_block_till_done()

    async def light_leader(self, on: bool, brightness: int | None = None) -> None:
        await self.lights.leader_light.set(on=on, brightness=brightness)
        await self.hass.async_block_till_done()

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return self.lights.follower.calls


async def test_a_switch_leader_turns_the_follower_on_and_off(
    hass: HomeAssistant, lights: Lights
) -> None:
    follow = Follow(hass, lights)
    await follow.start(lights.leader_switch)

    await follow.switch_leader(True)
    await follow.switch_leader(False)

    assert follow.calls == [("turn_on", {}), ("turn_off", {})]


async def test_a_light_leader_copies_brightness_when_enabled(
    hass: HomeAssistant, lights: Lights
) -> None:
    follow = Follow(hass, lights)
    await follow.start(lights.leader_light, copy_brightness=True)

    await follow.light_leader(True, brightness=117)
    await follow.light_leader(True, brightness=183)
    await follow.light_leader(False)

    assert follow.calls == [
        ("turn_on", {"brightness": 117}),
        ("turn_on", {"brightness": 183}),
        ("turn_off", {}),
    ]


async def test_a_light_leader_ignores_brightness_when_disabled(
    hass: HomeAssistant, lights: Lights
) -> None:
    follow = Follow(hass, lights)
    await follow.start(lights.leader_light, copy_brightness=False)

    await follow.light_leader(True, brightness=117)
    await follow.light_leader(True, brightness=183)
    await follow.light_leader(False)

    assert follow.calls == [("turn_on", {}), ("turn_on", {}), ("turn_off", {})]
