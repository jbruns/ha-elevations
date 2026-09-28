"""A weather entity with an hourly forecast that a test sets hour by hour.

Its native units are the ones Home Assistant shows for its unit system, so
the values a test sets are the values weather.get_forecasts answers.
"""

from dataclasses import asdict, dataclass
from datetime import timedelta

import pytest
from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import setup_test_component_platform

ENTITY_ID = "weather.example"
HOUR = timedelta(hours=1)


@dataclass
class Hour:
    """One hour of forecast; leave a value out and the forecast omits it."""

    temperature: float | None = None
    dew_point: float | None = None
    precipitation_probability: float | None = None
    precipitation: float | None = None
    wind_speed: float | None = None
    wind_gust_speed: float | None = None


def native_key(key: str) -> str:
    """The Forecast key for an Hour field; values with a unit are native_."""
    return key if key == "precipitation_probability" else f"native_{key}"


class Weather(WeatherEntity):
    _attr_should_poll = False
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_condition = "cloudy"

    def __init__(self) -> None:
        self.entity_id = ENTITY_ID
        self._attr_name = "Example"
        self._hours: list[Hour] = []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_native_temperature_unit = self._default_temperature_unit
        self._attr_native_precipitation_unit = self._default_precipitation_unit
        self._attr_native_wind_speed_unit = self._default_wind_speed_unit

    async def set_hourly(self, hours: list[Hour]) -> None:
        """The forecast from the current hour on, one Hour per hour."""
        self._hours = list(hours)
        await self.async_update_listeners(["hourly"])

    async def async_forecast_hourly(self) -> list[Forecast]:
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        return [
            Forecast(
                datetime=(start + index * HOUR).isoformat(),
                **{
                    native_key(key): value
                    for key, value in asdict(hour).items()
                    if value is not None
                },
            )
            for index, hour in enumerate(self._hours)
        ]


@pytest.fixture
async def weather(hass: HomeAssistant) -> Weather:
    entity = Weather()
    setup_test_component_platform(hass, "weather", [entity])
    assert await async_setup_component(hass, "weather", {"weather": {"platform": "test"}})
    await hass.async_block_till_done()
    return entity
