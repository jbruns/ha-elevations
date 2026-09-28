"""Who is home: zone.home counts the persons in it (ADR 0005)."""

from dataclasses import dataclass

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@dataclass
class Household:
    hass: HomeAssistant
    persons: int = 0

    async def set_home(self, count: int) -> None:
        """Put count persons home and everyone else away."""
        self.persons = max(self.persons, count)
        for index in range(1, self.persons + 1):
            home = index <= count
            self.hass.states.async_set(
                f"person.example_{index}",
                "home" if home else "not_home",
                # zone.home counts the persons whose in_zones lists it.
                {"in_zones": ["zone.home"] if home else []},
            )
        await self.hass.async_block_till_done()
        assert self.hass.states.get("zone.home").state == str(count)


@pytest.fixture
async def household(hass: HomeAssistant) -> Household:
    assert await async_setup_component(hass, "zone", {})
    return Household(hass)
