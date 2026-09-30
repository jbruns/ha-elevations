"""Behaviour of the Night Timeout blueprint, run in a real Home Assistant core."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.lights import Lights

BLUEPRINT = "lighting/blueprints/automation/lighting_night_timeout.yaml"
NIGHT = datetime(2026, 6, 1, 22, 5)
DAY = datetime(2026, 6, 1, 12, 0)


@dataclass
class NightTimeout:
    hass: HomeAssistant
    clock: Clock
    lights: Lights

    async def start(self, **inputs: Any) -> None:
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "light": self.lights.follower.entity_id,
                        "duration": {"minutes": 5},
                        **inputs,
                    },
                    alias="Night Timeout",
                )
            ],
        )

    async def light_on(self) -> None:
        await self.lights.follower.set(on=True)
        await self.hass.async_block_till_done()

    async def light_off(self) -> None:
        await self.lights.follower.set(on=False)
        await self.hass.async_block_till_done()

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return self.lights.follower.calls


async def test_a_light_on_too_long_at_night_turns_off(
    hass: HomeAssistant, clock: Clock, lights: Lights
) -> None:
    timeout = NightTimeout(hass, clock, lights)
    await clock.move_to(NIGHT)
    await timeout.start()

    await timeout.light_on()
    await clock.advance(timedelta(minutes=5))

    assert timeout.calls == [("turn_off", {})]


async def test_a_light_on_too_long_by_day_is_left_on(
    hass: HomeAssistant, clock: Clock, lights: Lights
) -> None:
    timeout = NightTimeout(hass, clock, lights)
    await clock.move_to(DAY)
    await timeout.start()

    await timeout.light_on()
    await clock.advance(timedelta(minutes=5))

    assert timeout.calls == []


async def test_a_light_turned_off_before_the_timeout_is_left_alone(
    hass: HomeAssistant, clock: Clock, lights: Lights
) -> None:
    timeout = NightTimeout(hass, clock, lights)
    await clock.move_to(NIGHT)
    await timeout.start()

    await timeout.light_on()
    await clock.advance(timedelta(minutes=4))
    await timeout.light_off()
    await clock.advance(timedelta(minutes=1))

    assert timeout.calls == []


async def test_night_hours_can_cross_midnight(
    hass: HomeAssistant, clock: Clock, lights: Lights
) -> None:
    timeout = NightTimeout(hass, clock, lights)
    await clock.move_to(datetime(2026, 6, 1, 23, 55))
    await timeout.start(night_start="23:00:00", night_end="01:00:00")

    await timeout.light_on()
    await clock.advance(timedelta(minutes=5))

    assert timeout.calls == [("turn_off", {})]


async def test_a_light_already_on_for_the_duration_turns_off_at_night_start(
    hass: HomeAssistant, clock: Clock, lights: Lights
) -> None:
    timeout = NightTimeout(hass, clock, lights)
    await clock.move_to(datetime(2026, 6, 1, 21, 54))
    await timeout.start()
    await timeout.light_on()
    await clock.advance(timedelta(minutes=5))
    assert timeout.calls == []

    await clock.advance(timedelta(minutes=1))

    assert timeout.calls == [("turn_off", {})]
