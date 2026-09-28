"""A weather entity answers weather.get_forecasts with the hours a test sets (ADR 0005)."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

from testing.clock import Clock
from testing.weather import Hour, Weather


async def hourly(hass: HomeAssistant, weather: Weather) -> list[dict]:
    response = await hass.services.async_call(
        "weather",
        "get_forecasts",
        {"entity_id": weather.entity_id, "type": "hourly"},
        blocking=True,
        return_response=True,
    )
    return response[weather.entity_id]["forecast"]


async def test_get_forecasts_answers_each_hours_values(
    hass: HomeAssistant, clock: Clock, weather: Weather
) -> None:
    await clock.move_to(datetime(2026, 6, 1, 14, 20))
    await weather.set_hourly(
        [
            Hour(
                temperature=24,
                dew_point=12,
                precipitation_probability=10,
                precipitation=0,
                wind_speed=8,
                wind_gust_speed=15,
            ),
            Hour(temperature=21, precipitation_probability=80, precipitation=2.5),
        ]
    )

    first, second = await hourly(hass, weather)

    assert first == {
        "datetime": "2026-06-01T21:00:00+00:00",
        "temperature": 24,
        "dew_point": 12,
        "precipitation_probability": 10,
        "precipitation": 0,
        "wind_speed": 8,
        "wind_gust_speed": 15,
    }
    assert second == {
        "datetime": "2026-06-01T22:00:00+00:00",
        "temperature": 21,
        "precipitation_probability": 80,
        "precipitation": 2.5,
    }


async def test_the_forecast_starts_at_the_current_hour_as_time_passes(
    hass: HomeAssistant, clock: Clock, weather: Weather
) -> None:
    await clock.move_to(datetime(2026, 6, 1, 14, 20))
    await weather.set_hourly([Hour(temperature=t) for t in (20, 21, 22)])

    await clock.advance(timedelta(hours=1))

    assert [hour["datetime"] for hour in await hourly(hass, weather)] == [
        "2026-06-01T22:00:00+00:00",
        "2026-06-01T23:00:00+00:00",
        "2026-06-02T00:00:00+00:00",
    ]
