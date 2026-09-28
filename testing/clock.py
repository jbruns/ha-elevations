"""Frozen time that tests move forward, firing Home Assistant's timers."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed


@dataclass
class Clock:
    hass: HomeAssistant
    freezer: FrozenDateTimeFactory

    def now(self) -> datetime:
        """The current time in Home Assistant's time zone."""
        return dt_util.now()

    async def move_to(self, when: datetime) -> None:
        """Jump to a time; a naive time is local to Home Assistant."""
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.get_default_time_zone())
        self.freezer.move_to(when)
        await self._fire()

    async def advance(self, delta: timedelta, *, step: timedelta | None = None) -> None:
        """Move forward by delta, firing timers once at the end or after every step.

        One jump fires a time pattern once; step through to fire it each time it
        matches along the way.
        """
        remaining = delta
        while remaining > timedelta(0):
            tick = min(step or remaining, remaining)
            self.freezer.tick(tick)
            remaining -= tick
            await self._fire()

    async def _fire(self) -> None:
        async_fire_time_changed(self.hass)
        await self.hass.async_block_till_done()


@pytest.fixture
def clock(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Clock:
    return Clock(hass, freezer)
