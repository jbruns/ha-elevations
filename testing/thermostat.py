"""A thermostat: a climate entity that records the calls automations make to it:
set_temperature, set_hvac_mode, turn_on and turn_off.

It heats, cools, holds a heating and cooling pair (heat_cool), or is off.
Temperatures are in Home Assistant's unit system.
"""

from typing import Any

import pytest
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

ENTITY_ID = "climate.example"


class Thermostat(ClimateEntity):
    _attr_should_poll = False
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_hvac_mode = HVACMode.OFF
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self) -> None:
        self.entity_id = ENTITY_ID
        self._attr_name = "Example"
        self._attr_current_temperature = None
        self._attr_target_temperature = None
        self._attr_target_temperature_low = None
        self._attr_target_temperature_high = None
        # turn_on resumes the last mode other than off, as most thermostats do.
        self._resume_mode = HVACMode.HEAT_COOL
        # Each call as (service, data), such as ("set_hvac_mode", {"hvac_mode": "heat"})
        # or ("turn_off", {}).
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_temperature_unit = self.hass.config.units.temperature_unit

    async def set(
        self,
        *,
        hvac_mode: str | None = None,
        temperature: float | None = None,
        target_temp_low: float | None = None,
        target_temp_high: float | None = None,
        current_temperature: float | None = None,
        available: bool | None = None,
    ) -> None:
        """Set the thermostat's state without recording a call, as if changed by hand.
        available=False makes it unavailable, as when its integration loses touch."""
        if available is not None:
            self._attr_available = available
        if current_temperature is not None:
            self._attr_current_temperature = current_temperature
        self._apply(
            hvac_mode=hvac_mode,
            temperature=temperature,
            target_temp_low=target_temp_low,
            target_temp_high=target_temp_high,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self.calls.append(("set_hvac_mode", {"hvac_mode": str(hvac_mode)}))
        self._apply(hvac_mode=hvac_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self.calls.append(
            (
                "set_temperature",
                {k: _plain(v) for k, v in kwargs.items() if k != "entity_id"},
            )
        )
        self._apply(
            hvac_mode=kwargs.get("hvac_mode"),
            temperature=kwargs.get("temperature"),
            target_temp_low=kwargs.get("target_temp_low"),
            target_temp_high=kwargs.get("target_temp_high"),
        )

    async def async_turn_off(self) -> None:
        self.calls.append(("turn_off", {}))
        self._apply(hvac_mode=HVACMode.OFF)

    async def async_turn_on(self) -> None:
        self.calls.append(("turn_on", {}))
        self._apply(hvac_mode=self._resume_mode)

    def _apply(
        self,
        *,
        hvac_mode: str | None = None,
        temperature: float | None = None,
        target_temp_low: float | None = None,
        target_temp_high: float | None = None,
    ) -> None:
        if hvac_mode is not None:
            self._attr_hvac_mode = HVACMode(hvac_mode)
            if self._attr_hvac_mode != HVACMode.OFF:
                self._resume_mode = self._attr_hvac_mode
        if temperature is not None:
            self._attr_target_temperature = temperature
        if target_temp_low is not None:
            self._attr_target_temperature_low = target_temp_low
        if target_temp_high is not None:
            self._attr_target_temperature_high = target_temp_high
        self.async_write_ha_state()


def _plain(value: Any) -> Any:
    return str(value) if isinstance(value, HVACMode) else value


@pytest.fixture
async def thermostat(hass: HomeAssistant) -> Thermostat:
    entity = Thermostat()
    setup_test_component_platform(hass, "climate", [entity])
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()
    return entity
