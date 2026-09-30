"""The optional Dashboards weather package publishes forecast sensors."""

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from testing.clock import Clock
from testing.weather import Day, Hour, Weather

PACKAGE = Path(__file__).parent.parent / "packages" / "dashboards_weather_forecast.yaml"
HOURLY = "sensor.dashboards_hourly_forecast"
DAILY = "sensor.dashboards_daily_forecast"


def package_config() -> dict[str, Any]:
    return yaml.safe_load(PACKAGE.read_text())


@pytest.fixture
def expected_lingering_timers() -> bool:
    # The package refreshes forecast sensors with time_pattern triggers.
    return True


async def setup_package(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "template", package_config())
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


async def test_weather_forecast_package_holds_hourly_and_daily_forecasts(
    hass: HomeAssistant, clock: Clock, weather: Weather
) -> None:
    await clock.move_to(datetime(2026, 6, 1, 14, 20))
    await weather.set_hourly([Hour(temperature=72), Hour(temperature=74)])
    await weather.set_daily([Day(temperature=80), Day(temperature=78)])

    await setup_package(hass)

    hourly = hass.states.get(HOURLY)
    daily = hass.states.get(DAILY)
    assert hourly is not None
    assert daily is not None
    assert hourly.attributes["forecast"] == [
        {"datetime": "2026-06-01T21:00:00+00:00", "temperature": 72},
        {"datetime": "2026-06-01T22:00:00+00:00", "temperature": 74},
    ]
    assert daily.attributes["forecast"] == [
        {"datetime": "2026-06-01T00:00:00+00:00", "temperature": 80},
        {"datetime": "2026-06-02T00:00:00+00:00", "temperature": 78},
    ]
