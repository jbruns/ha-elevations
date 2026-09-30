"""Lights and switches whose service calls tests can inspect."""

from dataclasses import dataclass
from typing import Any

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform


def _call_data(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if k != "entity_id"}


class SettableLight(LightEntity):
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entity_id: str, name: str) -> None:
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_unique_id = entity_id
        self._attr_is_on = False
        self._attr_brightness: int | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def set(self, *, on: bool, brightness: int | None = None) -> None:
        """Set the light as if changed by hand, without recording a call."""
        self._apply(on=on, brightness=brightness)

    async def async_turn_on(self, **kwargs: Any) -> None:
        data = _call_data(kwargs)
        self.calls.append(("turn_on", data))
        self._apply(on=True, brightness=data.get(ATTR_BRIGHTNESS, self._attr_brightness))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.calls.append(("turn_off", _call_data(kwargs)))
        self._apply(on=False)

    def _apply(self, *, on: bool, brightness: int | None = None) -> None:
        self._attr_is_on = on
        if brightness is not None:
            self._attr_brightness = brightness
        self.async_write_ha_state()


class SettableSwitch(SwitchEntity):
    _attr_should_poll = False

    def __init__(self, entity_id: str, name: str) -> None:
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_unique_id = entity_id
        self._attr_is_on = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def set(self, *, on: bool) -> None:
        """Set the switch as if changed by hand, without recording a call."""
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.calls.append(("turn_on", _call_data(kwargs)))
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.calls.append(("turn_off", _call_data(kwargs)))
        self._attr_is_on = False
        self.async_write_ha_state()


@dataclass
class Lights:
    leader_light: SettableLight
    follower: SettableLight
    other_follower: SettableLight
    leader_switch: SettableSwitch


@pytest.fixture
async def lights(hass: HomeAssistant) -> Lights:
    leader_light = SettableLight("light.example_leader", "Example Leader")
    follower = SettableLight("light.example_follower", "Example Follower")
    other_follower = SettableLight("light.example_other_follower", "Example Other Follower")
    leader_switch = SettableSwitch("switch.example_leader", "Example Leader")
    setup_test_component_platform(hass, "light", [leader_light, follower, other_follower])
    setup_test_component_platform(hass, "switch", [leader_switch])
    assert await async_setup_component(hass, "light", {"light": {"platform": "test"}})
    assert await async_setup_component(hass, "switch", {"switch": {"platform": "test"}})
    await hass.async_block_till_done()
    return Lights(leader_light, follower, other_follower, leader_switch)
