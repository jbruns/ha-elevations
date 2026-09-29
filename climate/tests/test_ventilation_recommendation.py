"""Behaviour of the Ventilation Recommendation blueprint, run in a real Home
Assistant core.

Unless a test says otherwise, it is 12:01, someone is home, the air is clean,
and the next hours are dry, calm and mild. Indoors is warm (76) and outdoors
comfortable (72), so outdoor air would cool the home. Temperatures are in °F,
the blueprint's defaults. The recommendation is checked every 5 minutes; an
"open" must hold for 10 minutes, and a "close" for 5.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.helpers import Helpers
from testing.household import Household
from testing.phones import Phone
from testing.sensors import Sensors
from testing.weather import Hour, Weather

BLUEPRINT = "climate/blueprints/automation/climate_ventilation_recommendation.yaml"
INDOOR = "sensor.example_indoor_temperature"
OUTDOOR = "sensor.example_outdoor_temperature"
AQI = "sensor.example_outdoor_aqi"
NOON = datetime(2026, 6, 1, 12)
FINE = Hour(
    temperature=72,
    dew_point=50,
    precipitation_probability=0,
    precipitation=0,
    wind_speed=5,
    wind_gust_speed=10,
)
MINUTE = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def fahrenheit(hass: HomeAssistant) -> None:
    """°F before the weather entity takes its units."""
    hass.config.units = US_CUSTOMARY_SYSTEM


@dataclass
class Ventilation:
    hass: HomeAssistant
    clock: Clock
    sensors: Sensors
    household: Household
    weather: Weather
    recipient: Phone
    active: str
    hold: str

    async def start(self, *, aqi: bool = True, **inputs: Any) -> None:
        """Create the automation, with any other inputs; aqi=False leaves out
        the AQI sensor."""
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "indoor_temperature": INDOOR,
                        "outdoor_temperature": OUTDOOR,
                        "weather": self.weather.entity_id,
                        **({"outdoor_aqi": AQI} if aqi else {}),
                        "recipients": [self.recipient.device_id],
                        "recommendation_active": self.active,
                        "hold_timer": self.hold,
                        **inputs,
                    },
                    alias="Ventilation Recommendation",
                )
            ],
        )

    async def temperatures(self, indoor: Any, outdoor: Any) -> None:
        await self.sensors.set(
            INDOOR, indoor, device_class="temperature", unit="°F"
        )
        await self.sensors.set(
            OUTDOOR, outdoor, device_class="temperature", unit="°F"
        )

    async def aqi(self, value: Any) -> None:
        await self.sensors.set(AQI, value, device_class="aqi")

    async def forecast(self, *hours: Hour) -> None:
        """The next hours; fine weather after the ones given, 4 hours in all."""
        await self.weather.set_hourly(list(hours) + [FINE] * (4 - len(hours)))

    async def wait(self, minutes: float) -> None:
        await self.clock.advance(timedelta(minutes=minutes), step=MINUTE / 2)

    async def at(self, hour: int, minute: int = 0) -> None:
        """Move to a time on the test's day, or the next morning before 06:00."""
        day = NOON if hour >= 6 else NOON + timedelta(days=1)
        await self.clock.move_to(day.replace(hour=hour, minute=minute))

    async def set_active(self, on: bool) -> None:
        await self.hass.services.async_call(
            "input_boolean",
            "turn_on" if on else "turn_off",
            {"entity_id": self.active},
            blocking=True,
        )

    @property
    def is_active(self) -> bool:
        return self.hass.states.get(self.active).state == "on"

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return self.recipient.notifications

    def sent(self) -> list[tuple[str, str]]:
        return [(n["title"], n["message"]) for n in self.notifications]


@pytest.fixture
async def ventilation(
    hass: HomeAssistant,
    clock: Clock,
    helpers: Helpers,
    sensors: Sensors,
    household: Household,
    weather: Weather,
    recipient: Phone,
) -> Ventilation:
    """Everything the blueprint reads, at 12:01; call start() to create it."""
    # Created before the clock moves, while the websocket's access token is valid.
    active = await helpers.input_boolean("Example Windows Open")
    hold = await helpers.timer("Example Ventilation Hold")
    # Just after a check, so the first check after start() is at 12:05.
    await clock.move_to(NOON.replace(minute=1))
    ventilation = Ventilation(
        hass, clock, sensors, household, weather, recipient, active, hold
    )
    await ventilation.temperatures(76, 72)
    await ventilation.aqi(20)
    await ventilation.forecast()
    await household.set_home(1)
    return ventilation


OPEN = ("Open the windows", "Outdoor air can cool the home. Indoor 76°F, outdoor 72°F.")


async def opened(ventilation: Ventilation, **inputs: Any) -> None:
    """Start the automation and let the Open Notification arrive."""
    await ventilation.start(**inputs)
    await ventilation.wait(15)
    assert ventilation.sent() == [OPEN]
    assert ventilation.is_active


# Open


async def test_open_is_sent_once_it_has_held_for_10_minutes(
    ventilation: Ventilation,
) -> None:
    await ventilation.start()

    # First checked at 12:05, so the hold ends at 12:15.
    await ventilation.wait(13.5)
    assert ventilation.notifications == []
    await ventilation.wait(0.5)

    assert ventilation.sent() == [OPEN]
    assert ventilation.notifications[0]["data"]["push"] == {"sound": "default"}
    assert ventilation.is_active


async def test_open_is_not_sent_again_while_active(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.wait(60)

    assert ventilation.sent() == [OPEN]


async def test_open_that_does_not_hold_is_not_sent(ventilation: Ventilation) -> None:
    await ventilation.forecast(replace(FINE, precipitation_probability=50))
    await ventilation.start()
    await ventilation.wait(1)

    await ventilation.forecast()
    await ventilation.wait(7)
    await ventilation.forecast(replace(FINE, precipitation_probability=50))
    await ventilation.wait(30)

    assert ventilation.notifications == []
    assert not ventilation.is_active


async def test_the_open_hold_is_an_input(ventilation: Ventilation) -> None:
    await ventilation.start(open_for={"minutes": 20})

    await ventilation.wait(23.5)
    assert ventilation.notifications == []
    await ventilation.wait(0.5)

    assert ventilation.sent() == [OPEN]


@pytest.mark.parametrize(
    ("indoor", "outdoor", "reason"),
    [
        (76, 72, "Outdoor air can cool the home"),
        (73, 71, "Outdoor air can cool the home"),
        (70, 72, "Outdoor air is comfortable"),
        (66, 68, "Outdoor air can warm the home"),
    ],
)
async def test_open_says_why_ventilation_helps(
    ventilation: Ventilation, indoor: float, outdoor: float, reason: str
) -> None:
    await ventilation.temperatures(indoor, outdoor)
    await ventilation.start()

    await ventilation.wait(15)

    assert ventilation.sent() == [
        ("Open the windows", f"{reason}. Indoor {indoor}°F, outdoor {outdoor}°F.")
    ]


# Close


async def test_close_is_sent_once_it_has_held_for_5_minutes(
    ventilation: Ventilation,
) -> None:
    await opened(ventilation)

    await ventilation.forecast(replace(FINE, precipitation_probability=50))
    # First checked at 12:20, so the hold ends at 12:25.
    await ventilation.wait(8.5)
    assert len(ventilation.notifications) == 1
    await ventilation.wait(0.5)

    assert ventilation.sent()[1:] == [
        (
            "Close the windows",
            "Rain is forecast within the next 3 hours. Indoor 76°F, outdoor 72°F.",
        )
    ]
    assert not ventilation.is_active


async def test_close_silently_replaces_open(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.household.set_home(0)
    await ventilation.wait(10)

    open_notification, close = ventilation.notifications
    assert close["data"]["tag"] == open_notification["data"]["tag"]
    assert close["data"]["push"] == {"sound": "none", "interruption-level": "passive"}


async def test_close_that_does_not_hold_is_not_sent(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.household.set_home(0)
    await ventilation.wait(6)
    await ventilation.household.set_home(1)
    await ventilation.wait(30)

    assert ventilation.sent() == [OPEN]
    assert ventilation.is_active


async def test_the_close_hold_is_an_input(ventilation: Ventilation) -> None:
    await opened(ventilation, close_for={"minutes": 15})

    await ventilation.household.set_home(0)
    await ventilation.wait(18.5)
    assert len(ventilation.notifications) == 1
    await ventilation.wait(0.5)

    assert len(ventilation.notifications) == 2


async def test_no_close_without_an_open(ventilation: Ventilation) -> None:
    await ventilation.household.set_home(0)
    await ventilation.start()

    await ventilation.wait(60)

    assert ventilation.notifications == []


async def test_open_follows_close_again(ventilation: Ventilation) -> None:
    await opened(ventilation)
    await ventilation.household.set_home(0)
    await ventilation.wait(10)

    await ventilation.household.set_home(1)
    await ventilation.wait(15)

    assert [title for title, _ in ventilation.sent()] == [
        "Open the windows",
        "Close the windows",
        "Open the windows",
    ]
    assert ventilation.is_active


# Why Close is sent: one test for each reason


async def closed_because(ventilation: Ventilation) -> str:
    """Wait for the Close Notification and return its reason."""
    await ventilation.wait(10)
    title, message = ventilation.sent()[-1]
    assert title == "Close the windows"
    assert not ventilation.is_active
    return message.split(". Indoor ")[0]


async def test_close_when_nobody_is_home(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.household.set_home(0)

    assert await closed_because(ventilation) == "No household members are home"


async def test_close_when_the_aqi_is_above_the_maximum(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.aqi(76)

    assert await closed_because(ventilation) == "Outdoor AQI is 76"


async def test_an_aqi_at_the_maximum_still_opens(ventilation: Ventilation) -> None:
    await ventilation.aqi(75)

    await opened(ventilation)


@pytest.mark.parametrize(
    ("hour", "reason"),
    [
        (replace(FINE, precipitation_probability=21), "Rain is forecast"),
        (replace(FINE, precipitation=0.02), "Rain is forecast"),
        (replace(FINE, dew_point=61), "Muggy air is forecast"),
        (replace(FINE, wind_gust_speed=26), "Wind gusts are high"),
        (replace(FINE, wind_gust_speed=None, wind_speed=26), "Wind gusts are high"),
        (replace(FINE, temperature=67), "Outdoor temperature will fall below 68°F"),
        (replace(FINE, temperature=79), "Outdoor temperature will rise above 78°F"),
    ],
)
async def test_close_for_weather_in_the_lookahead(
    ventilation: Ventilation, hour: Hour, reason: str
) -> None:
    await opened(ventilation)

    # The third and last hour of the lookahead.
    await ventilation.forecast(FINE, FINE, hour)

    assert await closed_because(ventilation) == f"{reason} within the next 3 hours"


@pytest.mark.parametrize(
    "hour",
    [
        replace(FINE, precipitation_probability=20),
        replace(FINE, precipitation=0.01),
        replace(FINE, dew_point=60),
        replace(FINE, wind_gust_speed=25, wind_speed=30),
        replace(FINE, temperature=68),
        replace(FINE, temperature=78),
    ],
)
async def test_weather_at_each_limit_still_opens(ventilation: Ventilation, hour: Hour) -> None:
    await ventilation.forecast(hour, hour, hour)

    await opened(ventilation)


async def test_weather_beyond_the_lookahead_is_ignored(ventilation: Ventilation) -> None:
    await ventilation.forecast(FINE, FINE, FINE, replace(FINE, precipitation_probability=90))

    await opened(ventilation)


async def test_every_forecast_reason_is_given(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.forecast(
        replace(FINE, precipitation_probability=50), replace(FINE, wind_gust_speed=40)
    )

    assert await closed_because(ventilation) == (
        "Rain is forecast, Wind gusts are high within the next 3 hours"
    )


@pytest.mark.parametrize(
    ("indoor", "outdoor", "reason"),
    [
        (82, 67, "Outdoor temperature is below 68°F"),
        (82, 79, "Outdoor temperature is above 78°F"),
        (76, 75, "Outdoor air is too warm to cool the home"),
        (67, 68, "Outdoor air is too cool to warm the home"),
    ],
)
async def test_close_when_outdoor_air_would_not_help(
    ventilation: Ventilation, indoor: float, outdoor: float, reason: str
) -> None:
    await opened(ventilation)

    await ventilation.temperatures(indoor, outdoor)

    assert await closed_because(ventilation) == reason


async def test_close_gives_every_reason(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.household.set_home(0)
    await ventilation.aqi(90)
    await ventilation.forecast(replace(FINE, precipitation_probability=50))

    assert await closed_because(ventilation) == (
        "No household members are home, Outdoor AQI is 90, "
        "Rain is forecast within the next 3 hours"
    )


# Neutral: incomplete data changes nothing


NEUTRAL = [
    pytest.param(INDOOR, id="indoor"),
    pytest.param(OUTDOOR, id="outdoor"),
    pytest.param(AQI, id="aqi"),
]


@pytest.mark.parametrize("sensor", NEUTRAL)
async def test_missing_data_never_closes(ventilation: Ventilation, sensor: str) -> None:
    await opened(ventilation)

    await ventilation.household.set_home(0)
    await ventilation.sensors.set(sensor, "unavailable")
    await ventilation.wait(60)

    assert ventilation.sent() == [OPEN]
    assert ventilation.is_active


@pytest.mark.parametrize("sensor", NEUTRAL)
async def test_missing_data_never_opens(ventilation: Ventilation, sensor: str) -> None:
    await ventilation.sensors.set(sensor, "unknown")
    await ventilation.start()

    await ventilation.wait(60)

    assert ventilation.notifications == []


async def test_a_hold_carries_on_through_missing_data(ventilation: Ventilation) -> None:
    await ventilation.start()
    # The hold starts at 12:05 and ends at 12:15.
    await ventilation.wait(5)

    await ventilation.aqi("unavailable")
    await ventilation.wait(5)
    await ventilation.aqi(20)
    await ventilation.wait(4)

    assert ventilation.sent() == [OPEN]


async def test_a_temperature_reason_is_not_repeated(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.temperatures(69, 61)
    await ventilation.forecast(replace(FINE, temperature=61))

    assert await closed_because(ventilation) == (
        "Outdoor temperature will fall below 68°F within the next 3 hours"
    )


async def test_a_short_forecast_never_closes(ventilation: Ventilation) -> None:
    await opened(ventilation)

    await ventilation.household.set_home(0)
    await ventilation.weather.set_hourly([FINE, FINE])
    await ventilation.wait(60)

    assert ventilation.sent() == [OPEN]


async def test_a_short_forecast_never_opens(ventilation: Ventilation) -> None:
    await ventilation.weather.set_hourly([FINE, FINE])
    await ventilation.start()

    await ventilation.wait(60)

    assert ventilation.notifications == []


async def test_the_lookahead_is_an_input(ventilation: Ventilation) -> None:
    await ventilation.weather.set_hourly([FINE, FINE])

    await opened(ventilation, lookahead=2)


# Without an AQI sensor


async def test_without_an_aqi_sensor_the_aqi_is_not_checked(
    ventilation: Ventilation,
) -> None:
    await ventilation.aqi("unavailable")

    await opened(ventilation, aqi=False)
    await ventilation.forecast(replace(FINE, precipitation_probability=50))

    assert await closed_because(ventilation) == "Rain is forecast within the next 3 hours"


# Active hours


async def test_at_the_start_of_active_hours_open_is_sent_at_once(
    ventilation: Ventilation,
) -> None:
    await ventilation.at(6, 50)
    await ventilation.start()

    await ventilation.at(7)

    assert ventilation.sent() == [OPEN]
    assert ventilation.is_active


async def test_at_the_start_of_active_hours_close_is_sent_at_once(
    ventilation: Ventilation,
) -> None:
    await ventilation.set_active(True)
    await ventilation.forecast(replace(FINE, precipitation_probability=50))
    await ventilation.at(6, 50)
    await ventilation.start()

    await ventilation.at(7)

    assert ventilation.sent() == [
        (
            "Close the windows",
            "Rain is forecast within the next 3 hours. Indoor 76°F, outdoor 72°F.",
        )
    ]
    assert not ventilation.is_active


async def test_at_the_start_of_active_hours_nothing_is_closed_that_was_not_open(
    ventilation: Ventilation,
) -> None:
    await ventilation.forecast(replace(FINE, precipitation_probability=50))
    await ventilation.at(6, 50)
    await ventilation.start()

    await ventilation.at(7)

    assert ventilation.notifications == []


async def test_nothing_is_sent_outside_active_hours(ventilation: Ventilation) -> None:
    await ventilation.at(22)
    await ventilation.start()
    await ventilation.wait(60)
    assert ventilation.notifications == []

    await ventilation.set_active(True)
    await ventilation.household.set_home(0)
    await ventilation.wait(60)

    assert ventilation.notifications == []
    assert ventilation.is_active


async def test_a_hold_does_not_carry_past_the_end_of_active_hours(
    ventilation: Ventilation,
) -> None:
    await ventilation.at(21, 50)
    await ventilation.start()

    await ventilation.wait(30)

    assert ventilation.notifications == []


async def test_active_hours_are_inputs(ventilation: Ventilation) -> None:
    await ventilation.at(6, 50)
    await ventilation.start(active_start="09:00:00", active_end="21:00:00")
    await ventilation.at(7)
    await ventilation.wait(30)
    assert ventilation.notifications == []

    await ventilation.at(9)
    assert ventilation.sent() == [OPEN]

    await ventilation.household.set_home(0)
    await ventilation.at(21)
    await ventilation.wait(60)

    assert len(ventilation.notifications) == 1
