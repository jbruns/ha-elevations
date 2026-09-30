"""The sun entity, with elevation controlled by tests."""

from dataclasses import dataclass

import pytest
from homeassistant.core import HomeAssistant


@dataclass
class Sun:
    hass: HomeAssistant
    entity_id: str = "sun.sun"

    async def set_elevation(self, elevation: float) -> None:
        """Set sun.sun's elevation as if Home Assistant recalculated it."""
        self.hass.states.async_set(
            self.entity_id,
            "above_horizon" if elevation >= 0 else "below_horizon",
            {"elevation": elevation},
        )
        await self.hass.async_block_till_done()


@pytest.fixture
async def sun(hass: HomeAssistant) -> Sun:
    sun = Sun(hass)
    await sun.set_elevation(10.0)
    return sun
