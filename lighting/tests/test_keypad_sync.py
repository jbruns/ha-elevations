"""Behaviour of the Keypad Sync blueprint, run in a real Home Assistant core."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.lights import KeypadLights

BLUEPRINT = "lighting/blueprints/automation/lighting_keypad_sync.yaml"


@dataclass
class KeypadSync:
    hass: HomeAssistant
    clock: Clock
    devices: KeypadLights

    async def start(self, **inputs: Any) -> None:
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "light": self.devices.light.entity_id,
                        "keypad_sync_scene": self.devices.keypad_sync_scene.entity_id,
                        **inputs,
                    },
                    alias="Keypad Sync",
                )
            ],
        )

    async def light_changed_from_ha(self, on: bool | None, *, available: bool = True) -> None:
        """The light changes alone, as from HA; its keypad buttons stay as they were."""
        await self.devices.light.set(on=on, available=available)
        await self.clock.let_runs_start()

    async def wall(self, on: bool) -> None:
        """A press at the wall: the cross-linked room Scene sets the light and
        every keypad button together."""
        await self.devices.keypad_sync_scene.set(on=on)
        await self.light_changed_from_ha(on)

    async def wait(self, seconds: float) -> None:
        await self.clock.advance(timedelta(seconds=seconds), settle=False)

    @property
    def scene_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return self.devices.keypad_sync_scene.calls


async def test_a_light_turned_on_from_ha_lights_its_keypad_buttons_after_two_seconds(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.start()

    await sync.light_changed_from_ha(True)
    await sync.wait(1)
    assert sync.scene_calls == []

    await sync.wait(1)

    assert sync.scene_calls == [("turn_on", {})]
    assert keypad_lights.light.calls == []


async def test_a_light_turned_off_from_ha_turns_its_keypad_buttons_off(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.wall(True)
    await sync.start()

    await sync.light_changed_from_ha(False)
    await sync.wait(2)

    assert sync.scene_calls == [("turn_off", {})]


async def test_a_press_at_the_wall_sends_no_scene_command(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.start()

    await sync.wall(True)
    await sync.wait(2)
    await sync.wall(False)
    await sync.wait(2)

    assert sync.scene_calls == []


async def test_a_light_coming_back_from_unavailable_is_ignored(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.start()

    await sync.light_changed_from_ha(True, available=False)
    await sync.light_changed_from_ha(True)
    await sync.wait(2)

    assert sync.scene_calls == []


async def test_the_settle_time_can_be_changed(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.start(settle_time={"seconds": 5})

    await sync.light_changed_from_ha(True)
    await sync.wait(4)
    assert sync.scene_calls == []

    await sync.wait(1)

    assert sync.scene_calls == [("turn_on", {})]


async def test_a_light_on_with_only_some_keypad_buttons_lit_lights_them_all(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await keypad_lights.keypad_sync_scene.set(on=True, all_on=False)
    await sync.start()

    await sync.light_changed_from_ha(True)
    await sync.wait(2)

    assert sync.scene_calls == [("turn_on", {})]


async def test_a_light_switched_back_within_the_settle_time_sends_nothing(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.start()

    await sync.light_changed_from_ha(True)
    await sync.wait(1)
    await sync.light_changed_from_ha(False)
    await sync.wait(2)

    assert sync.scene_calls == []


async def test_a_light_coming_back_from_unknown_is_ignored(
    hass: HomeAssistant, clock: Clock, keypad_lights: KeypadLights
) -> None:
    sync = KeypadSync(hass, clock, keypad_lights)
    await sync.start()

    await sync.light_changed_from_ha(None)
    await sync.light_changed_from_ha(True)
    await sync.wait(2)

    assert sync.scene_calls == []
