"""Behaviour of the Comfort Policy blueprint, run in a real Home Assistant core.

The household is home, the thermostat heats, and the day is mild unless a test
says otherwise. Temperatures are in °F, the blueprint's defaults. Sleep starts
at 22:00 and the household wakes at 07:00.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.helpers import Helpers
from testing.household import Household
from testing.sensors import Sensors
from testing.thermostat import Thermostat
from testing.weather import Hour, Weather

BLUEPRINT = "climate/blueprints/automation/climate_comfort_policy.yaml"
HUMIDITY = "sensor.example_indoor_humidity"
OUTDOOR = "sensor.example_outdoor_temperature"
# Tests start at 06:00 on DAY; earlier hours are the next morning.
DAY = datetime(2026, 6, 1)
MILD = (60, 70)


@pytest.fixture(autouse=True)
def fahrenheit(hass: HomeAssistant) -> None:
    """°F before the thermostat and weather entities take their units."""
    hass.config.units = US_CUSTOMARY_SYSTEM


@dataclass
class Policy:
    hass: HomeAssistant
    clock: Clock
    sensors: Sensors
    household: Household
    weather: Weather
    thermostat: Thermostat
    door_pause: str
    humidity_state: str
    sleep_start: str
    wake_time: str
    inputs: dict[str, Any]

    async def start(self, **thermostat: Any) -> None:
        """Set the thermostat, as if by hand, then create the automation."""
        if thermostat:
            await self.thermostat.set(**thermostat)
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "thermostat": self.thermostat.entity_id,
                        "indoor_humidity": HUMIDITY,
                        "outdoor_temperature": OUTDOOR,
                        "weather": self.weather.entity_id,
                        "sleep_start": self.sleep_start,
                        "wake_time": self.wake_time,
                        "door_pause": self.door_pause,
                        "humidity_state": self.humidity_state,
                        **self.inputs,
                    },
                    alias="Comfort Policy",
                )
            ],
        )

    async def forecast(self, low: float, high: float, *, outdoor: float | None = None) -> None:
        """An hourly forecast whose low and high are these, with the outdoor
        temperature between them unless given."""
        await self.sensors.set(OUTDOOR, (low + high) / 2 if outdoor is None else outdoor)
        await self.weather.set_hourly([Hour(temperature=high), Hour(temperature=low)])

    async def humidity(self, percent: float) -> None:
        await self.sensors.set(HUMIDITY, percent, device_class="humidity", unit="%")

    async def already(self, percent: float, state: str) -> None:
        """Humidity that has been at percent long enough to be humid, dry or normal."""
        await self.humidity(percent)
        await self.hass.services.async_call(
            "input_select",
            "select_option",
            {"entity_id": self.humidity_state, "option": state},
            blocking=True,
        )

    @property
    def humidity_is(self) -> str:
        return self.hass.states.get(self.humidity_state).state

    async def set_time(self, helper: str, time: str) -> None:
        await self.hass.services.async_call(
            "input_datetime", "set_datetime", {"entity_id": helper, "time": time}, blocking=True
        )

    async def at(self, hour: int, minute: int = 0) -> None:
        """Move to a time of day, from 06:00 on DAY to 05:59 the next morning."""
        day = DAY if hour >= 6 else DAY + timedelta(days=1)
        await self.clock.move_to(day.replace(hour=hour, minute=minute))

    async def wake(self) -> None:
        await self.at(7)

    async def sleep(self) -> None:
        await self.at(22)

    async def set_door_pause(self, on: bool, *, finish: bool = True) -> None:
        """Turn the Door Pause helper on or off. With finish=False, don't wait
        for the runs it starts, such as one waiting for the thermostat."""
        await self.hass.services.async_call(
            "input_boolean",
            "turn_on" if on else "turn_off",
            {"entity_id": self.door_pause},
            blocking=True,
        )
        if finish:
            await self.hass.async_block_till_done()
        else:
            for _ in range(50):
                await asyncio.sleep(0)

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        """Every call the thermostat has received."""
        return self.thermostat.calls

    def settings(self) -> list[dict[str, Any]]:
        """The data of each set_temperature call."""
        return [data for service, data in self.calls if service == "set_temperature"]

    def last_setting(self) -> dict[str, Any]:
        return self.settings()[-1]


@pytest.fixture
def policy_input() -> dict[str, Any]:
    """Extra blueprint inputs; override with parametrize."""
    return {}


@pytest.fixture
async def policy(
    hass: HomeAssistant,
    clock: Clock,
    helpers: Helpers,
    sensors: Sensors,
    household: Household,
    weather: Weather,
    thermostat: Thermostat,
    policy_input: dict[str, Any],
) -> Policy:
    """Everything the blueprint reads, set up at 06:00; call start() to create it."""
    # Time-only helpers, as the household creates them in the UI. Created
    # before the clock moves, while the websocket's access token is valid.
    sleep_start = await helpers.input_datetime("Example Sleep Start", has_date=False)
    wake_time = await helpers.input_datetime("Example Wake Time", has_date=False)
    door_pause = await helpers.input_boolean("Example Door Pause")
    humidity_state = await helpers.input_select(
        "Example Humidity", ["normal", "humid", "dry"], initial="normal"
    )
    await clock.move_to(DAY.replace(hour=6))
    policy = Policy(
        hass,
        clock,
        sensors,
        household,
        weather,
        thermostat,
        door_pause,
        humidity_state,
        sleep_start,
        wake_time,
        policy_input,
    )
    await policy.set_time(sleep_start, "22:00:00")
    await policy.set_time(wake_time, "07:00:00")
    await policy.humidity(45)
    await policy.forecast(*MILD)
    await household.set_home(1)
    await thermostat.set(hvac_mode="heat", temperature=66)
    return policy


# Comfort Targets


@pytest.mark.parametrize(
    ("low", "heating"),
    [(41, 66), (40, 68), (21, 68), (20, 70), (5, 70)],
)
async def test_daytime_heating_target_rises_as_the_forecast_low_falls(
    policy: Policy, low: float, heating: float
) -> None:
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.forecast(low, 70)

    await policy.wake()

    assert policy.last_setting() == {"temperature": heating}


@pytest.mark.parametrize(
    ("high", "cooling"),
    [(79, 76), (80, 74), (89, 74), (90, 72), (100, 72)],
)
async def test_daytime_cooling_target_falls_as_the_forecast_high_rises(
    policy: Policy, high: float, cooling: float
) -> None:
    await policy.start(hvac_mode="cool", temperature=78)
    await policy.forecast(60, high)

    await policy.wake()

    assert policy.last_setting() == {"temperature": cooling}


async def test_heat_cool_sets_the_daytime_pair(policy: Policy) -> None:
    await policy.start(hvac_mode="heat_cool", target_temp_low=60, target_temp_high=80)
    await policy.forecast(20, 90)

    await policy.wake()

    # The cooling target, 72, sits the minimum gap above the heating one.
    assert policy.last_setting() == {"target_temp_low": 70, "target_temp_high": 73}


@pytest.mark.parametrize(
    ("mode", "setting"),
    [
        ("heat", {"temperature": 65}),
        ("cool", {"temperature": 67}),
        # The cooling target sits at least the minimum gap above the heating one.
        ("heat_cool", {"target_temp_low": 65, "target_temp_high": 68}),
    ],
)
async def test_sleep_start_applies_the_sleep_targets(
    policy: Policy, mode: str, setting: dict[str, float]
) -> None:
    await policy.start(
        hvac_mode=mode, temperature=70, target_temp_low=64, target_temp_high=70
    )
    await policy.at(21, 59)

    await policy.sleep()

    assert policy.last_setting() == setting


async def test_sleep_targets_ignore_the_forecast_tiers(policy: Policy) -> None:
    await policy.start(hvac_mode="heat", temperature=70)
    await policy.forecast(10, 70)

    await policy.sleep()

    assert policy.last_setting() == {"temperature": 65}


@pytest.mark.parametrize("policy_input", [{"heat_cool_gap": 5}])
async def test_the_heat_cool_gap_is_an_input(policy: Policy) -> None:
    await policy.start(hvac_mode="heat_cool", target_temp_low=60, target_temp_high=80)

    await policy.sleep()

    assert policy.last_setting() == {"target_temp_low": 65, "target_temp_high": 70}


@pytest.mark.parametrize(
    ("time", "heating"),
    [
        ((6, 59), 65),
        ((7, 0), 66),
        ((12, 0), 66),
        ((21, 59), 66),
        ((22, 0), 65),
        ((23, 30), 65),
        ((0, 0), 65),
        ((3, 0), 65),
    ],
)
async def test_sleeping_compares_now_with_the_time_only_helpers(
    policy: Policy, time: tuple[int, int], heating: float
) -> None:
    """The automation this replaces kept today_at() in a variable, which renders to a
    string, then compared now() with that string, and failed on every run."""
    await policy.household.set_home(0)
    await policy.at(*time)
    await policy.start(hvac_mode="heat", temperature=62)

    await policy.household.set_home(1)

    assert policy.last_setting() == {"temperature": heating}


async def test_sleeping_can_start_after_midnight(policy: Policy) -> None:
    await policy.set_time(policy.sleep_start, "00:30:00")
    await policy.household.set_home(0)
    await policy.at(23)
    await policy.start(hvac_mode="heat", temperature=62)

    await policy.household.set_home(1)
    await policy.at(1)

    assert policy.settings() == [{"temperature": 66}, {"temperature": 65}]


async def test_the_current_outdoor_temperature_counts_towards_the_forecast(
    policy: Policy,
) -> None:
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.forecast(50, 70, outdoor=18)

    await policy.wake()

    assert policy.last_setting() == {"temperature": 70}


@pytest.mark.parametrize(("mild_hours", "heating"), [(11, 70), (12, 66)])
async def test_forecast_hours_beyond_the_lookahead_are_ignored(
    policy: Policy, mild_hours: int, heating: float
) -> None:
    """From 07:00 the 12 h lookahead covers the hours starting 07:00 to 18:00."""
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.sensors.set(OUTDOOR, 60)
    await policy.weather.set_hourly(
        [Hour(temperature=60)] * mild_hours + [Hour(temperature=10)]
    )

    await policy.wake()

    assert policy.last_setting() == {"temperature": heating}


async def test_without_a_forecast_the_outdoor_temperature_decides(policy: Policy) -> None:
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.weather.set_hourly([])
    await policy.sensors.set(OUTDOOR, 15)

    await policy.wake()

    assert policy.last_setting() == {"temperature": 70}


async def test_home_assistant_starting_applies_the_target(
    hass: HomeAssistant, policy: Policy
) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat", temperature=62)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert policy.settings() == [{"temperature": 66}]


# Humidity


async def test_humid_air_caps_the_cooling_target_once_it_lasts(policy: Policy) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="cool", temperature=76)

    await policy.humidity(62)
    await policy.clock.advance(timedelta(minutes=14))
    assert policy.settings() == []
    await policy.clock.advance(timedelta(minutes=1))

    assert policy.settings() == [{"temperature": 74}]


async def test_humid_air_leaves_a_lower_cooling_target(policy: Policy) -> None:
    await policy.already(65, "humid")
    await policy.start(hvac_mode="cool", temperature=78)
    await policy.forecast(60, 90)

    await policy.wake()

    assert policy.last_setting() == {"temperature": 72}


async def test_the_cap_lifts_once_humidity_stays_below_the_leave_threshold(
    policy: Policy,
) -> None:
    await policy.at(12)
    await policy.already(65, "humid")
    await policy.start(hvac_mode="cool", temperature=74)

    await policy.humidity(57)
    await policy.clock.advance(timedelta(minutes=20))
    assert policy.settings() == []
    await policy.humidity(54)
    await policy.clock.advance(timedelta(minutes=15))

    assert policy.settings() == [{"temperature": 76}]
    assert policy.humidity_is == "normal"


async def test_humid_air_keeps_the_cap_until_it_leaves(policy: Policy) -> None:
    """Between the leave and enter thresholds the air stays humid."""
    await policy.at(12)
    await policy.start(hvac_mode="cool", temperature=76)
    await policy.humidity(62)
    await policy.clock.advance(timedelta(minutes=15))
    await policy.humidity(57)

    await policy.sleep()
    await policy.clock.advance(timedelta(hours=9))  # wake time

    assert policy.humidity_is == "humid"
    assert policy.last_setting() == {"temperature": 74}


async def test_a_brief_humid_spell_does_not_cap_the_target(policy: Policy) -> None:
    await policy.start(hvac_mode="cool", temperature=78)
    await policy.at(6, 55)
    await policy.humidity(62)

    await policy.wake()

    assert policy.humidity_is == "normal"
    assert policy.last_setting() == {"temperature": 76}


async def test_dry_air_keeps_its_adjustment_until_it_leaves(policy: Policy) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat", temperature=66)
    await policy.humidity(30)
    await policy.clock.advance(timedelta(minutes=30))
    await policy.humidity(37)

    await policy.sleep()
    await policy.clock.advance(timedelta(hours=9))  # wake time

    assert policy.humidity_is == "dry"
    assert policy.last_setting() == {"temperature": 65}


async def test_humidity_is_tracked_while_away(policy: Policy) -> None:
    await policy.at(12)
    await policy.start()
    await policy.household.set_home(0)

    await policy.humidity(65)
    await policy.clock.advance(timedelta(minutes=15))

    assert policy.humidity_is == "humid"


@pytest.mark.parametrize(
    ("low", "heating"),
    [
        (60, 65),  # 66 lowered by 1
        (40, 67),  # 68 lowered by 1
    ],
)
async def test_dry_air_lowers_the_heating_target_once_it_lasts(
    policy: Policy, low: float, heating: float
) -> None:
    await policy.at(12)
    await policy.forecast(low, 70)
    await policy.start(hvac_mode="heat", temperature=66)

    await policy.humidity(30)
    await policy.clock.advance(timedelta(minutes=29))
    assert policy.settings() == []
    await policy.clock.advance(timedelta(minutes=1))

    assert policy.settings() == [{"temperature": heating}]


async def test_dry_air_never_lowers_the_heating_target_below_the_floor(
    policy: Policy,
) -> None:
    await policy.already(30, "dry")
    await policy.start(hvac_mode="heat", temperature=70)

    await policy.sleep()

    assert policy.last_setting() == {"temperature": 65}


async def test_the_dry_adjustment_ends_once_humidity_stays_above_the_leave_threshold(
    policy: Policy,
) -> None:
    await policy.at(12)
    await policy.already(30, "dry")
    await policy.start(hvac_mode="heat", temperature=65)

    await policy.humidity(39)
    await policy.clock.advance(timedelta(minutes=30))

    assert policy.settings() == [{"temperature": 66}]


# Setback


@pytest.mark.parametrize(
    ("mode", "setting"),
    [
        ("heat", {"temperature": 60}),
        ("cool", {"temperature": 74}),
        ("heat_cool", {"target_temp_low": 60, "target_temp_high": 74}),
    ],
)
async def test_setback_follows_the_household_being_away_for_a_while(
    policy: Policy, mode: str, setting: dict[str, float]
) -> None:
    await policy.at(12)
    await policy.start(
        hvac_mode=mode, temperature=70, target_temp_low=66, target_temp_high=76
    )

    await policy.household.set_home(0)
    await policy.clock.advance(timedelta(minutes=29))
    assert policy.settings() == []
    await policy.clock.advance(timedelta(minutes=1))

    assert policy.settings() == [setting]


async def test_returning_home_restores_the_comfort_target(policy: Policy) -> None:
    await policy.at(12)
    await policy.start()
    await policy.household.set_home(0)
    await policy.clock.advance(timedelta(minutes=30))

    await policy.household.set_home(2)

    assert policy.settings() == [{"temperature": 60}, {"temperature": 66}]


async def test_home_assistant_starting_while_away_applies_the_setback(
    hass: HomeAssistant, policy: Policy
) -> None:
    await policy.household.set_home(0)
    await policy.at(12)
    await policy.start(hvac_mode="heat", temperature=66)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert policy.settings() == [{"temperature": 60}]


async def test_one_person_leaving_is_not_a_setback(policy: Policy) -> None:
    await policy.household.set_home(2)
    await policy.at(12)
    await policy.start()

    await policy.household.set_home(1)
    await policy.clock.advance(timedelta(hours=1))

    assert policy.calls == []


async def test_while_away_the_comfort_target_and_band_leave_the_setback(policy: Policy) -> None:
    await policy.at(12)
    await policy.start()
    await policy.household.set_home(0)
    await policy.clock.advance(timedelta(minutes=30))
    policy.calls.clear()

    await policy.sleep()
    await policy.thermostat.set(temperature=55)
    await policy.clock.advance(timedelta(minutes=30), step=timedelta(minutes=5))

    assert policy.calls == []


# Door Pause


async def test_nothing_changes_during_a_door_pause(policy: Policy) -> None:
    await policy.at(12)
    await policy.start()
    await policy.set_door_pause(True)

    await policy.household.set_home(0)
    await policy.clock.advance(timedelta(minutes=30))
    await policy.household.set_home(1)
    await policy.sleep()
    await policy.thermostat.set(temperature=80)
    await policy.clock.advance(timedelta(minutes=15))

    assert policy.calls == []


async def test_the_target_returns_when_a_door_pause_ends(policy: Policy) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.set_door_pause(True)
    await policy.sleep()

    await policy.set_door_pause(False)

    assert policy.settings() == [{"temperature": 65}]


async def test_the_target_waits_for_the_thermostat_to_resume_after_a_door_pause(
    policy: Policy,
) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.set_door_pause(True)
    await policy.thermostat.set(hvac_mode="off")

    await policy.set_door_pause(False, finish=False)
    assert policy.calls == []
    await policy.thermostat.set(hvac_mode="heat")
    await policy.hass.async_block_till_done()

    assert policy.settings() == [{"temperature": 66}]


async def test_a_door_pause_ending_while_away_changes_nothing(policy: Policy) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat", temperature=62)
    await policy.set_door_pause(True)
    await policy.household.set_home(0)

    await policy.set_door_pause(False)

    assert policy.calls == []


# Comfort Band


@pytest.mark.parametrize("temperature", [64, 66, 70, 79])
async def test_a_manual_setting_inside_the_band_is_left_alone(
    policy: Policy, temperature: float
) -> None:
    await policy.at(12)
    await policy.start()

    await policy.thermostat.set(temperature=temperature)
    await policy.clock.advance(timedelta(hours=1), step=timedelta(minutes=5))

    assert policy.calls == []


@pytest.mark.parametrize(("manual", "edge"), [(63, 64), (58, 64), (80, 79), (85, 79)])
async def test_a_manual_setting_outside_the_band_is_pulled_to_its_edge(
    policy: Policy, manual: float, edge: float
) -> None:
    await policy.at(12)
    await policy.start()

    await policy.thermostat.set(temperature=manual)
    await policy.hass.async_block_till_done()

    assert policy.settings() == [{"temperature": edge}]


async def test_a_heat_cool_pair_outside_the_band_is_pulled_to_its_edges(
    policy: Policy,
) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat_cool", target_temp_low=66, target_temp_high=76)

    await policy.thermostat.set(target_temp_low=55, target_temp_high=85)
    await policy.hass.async_block_till_done()

    assert policy.settings() == [{"target_temp_low": 64, "target_temp_high": 79}]


@pytest.mark.parametrize(
    ("low", "high", "setting"),
    [
        (70, 71, {"target_temp_low": 70, "target_temp_high": 73}),
        # The gap never pushes the cooling setting past the Band's top edge.
        (79, 79, {"target_temp_low": 76, "target_temp_high": 79}),
    ],
)
async def test_the_band_restores_the_heat_cool_gap(
    policy: Policy, low: float, high: float, setting: dict[str, float]
) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="heat_cool", target_temp_low=66, target_temp_high=76)

    await policy.thermostat.set(target_temp_low=low, target_temp_high=high)
    await policy.hass.async_block_till_done()

    assert policy.settings() == [setting]


async def test_the_band_follows_the_forecast_at_each_interval(policy: Policy) -> None:
    await policy.at(12, 1)
    await policy.start(hvac_mode="heat", temperature=65)

    await policy.forecast(15, 40)
    await policy.clock.advance(timedelta(minutes=13))
    assert policy.calls == []
    await policy.clock.advance(timedelta(minutes=1))

    assert policy.settings() == [{"temperature": 68}]


@pytest.mark.parametrize("policy_input", [{"band_interval": "/30"}])
async def test_the_band_interval_is_an_input(policy: Policy) -> None:
    await policy.at(12, 1)
    await policy.start(hvac_mode="heat", temperature=65)

    await policy.forecast(15, 40)
    await policy.clock.advance(timedelta(minutes=28), step=timedelta(minutes=1))
    assert policy.calls == []
    await policy.clock.advance(timedelta(minutes=1))

    assert policy.settings() == [{"temperature": 68}]


# The Band replaces the previous automation's own range table, a deliberate behaviour
# change. For representative 12 h forecasts, with normal indoor humidity:
#
# | Forecast (low / high) | Previous range      | New Comfort Band |
# |-----------------------|---------------------|------------------|
# | Mild (60 / 70)        | 64 – 79             | 64 – 79          |
# | Cool (41 / 70)        | 66 – 76             | 64 – 79          |
# | Cold (40 / 55)        | 66 – 76             | 66 – 79          |
# | Freezing (15 / 35)    | 68 – 74             | 68 – 79          |
# | Warm (60 / 79)        | 68 – 77             | 64 – 79          |
# | Hot (65 / 85)         | 68 – 77             | 64 – 77          |
# | Very hot (75 / 95)    | 70 – 75             | 64 – 75          |
# | Sleeping (mild)       | 64 – 76             | 63 – 70          |
#
# Now only the cold moves the Band's bottom edge and only the heat moves its
# top edge, each with the Comfort Target it derives from.
@pytest.mark.parametrize(
    ("forecast", "hour", "band"),
    [
        ((60, 70), 12, (64, 79)),
        ((41, 70), 12, (64, 79)),
        ((40, 55), 12, (66, 79)),
        ((15, 35), 12, (68, 79)),
        ((60, 79), 12, (64, 79)),
        ((65, 85), 12, (64, 77)),
        ((75, 95), 12, (64, 75)),
        ((60, 70), 23, (63, 70)),
    ],
)
async def test_the_band_for_representative_forecasts(
    policy: Policy, forecast: tuple[float, float], hour: int, band: tuple[float, float]
) -> None:
    await policy.forecast(*forecast)
    await policy.at(hour, 1)
    await policy.start(hvac_mode="heat_cool", target_temp_low=66, target_temp_high=69)

    await policy.thermostat.set(target_temp_low=50, target_temp_high=95)
    await policy.hass.async_block_till_done()

    assert policy.settings() == [{"target_temp_low": band[0], "target_temp_high": band[1]}]


# An off thermostat


async def test_an_off_thermostat_is_never_changed(policy: Policy) -> None:
    await policy.at(12)
    await policy.start(hvac_mode="off", temperature=50)

    await policy.household.set_home(0)
    await policy.clock.advance(timedelta(minutes=30))
    await policy.household.set_home(1)
    await policy.humidity(65)
    await policy.clock.advance(timedelta(minutes=15))
    await policy.set_door_pause(True)
    await policy.set_door_pause(False, finish=False)
    await policy.clock.advance(timedelta(minutes=1))
    await policy.sleep()
    await policy.clock.advance(timedelta(hours=9))  # wake time
    await policy.thermostat.set(temperature=40)
    await policy.clock.advance(timedelta(hours=1), step=timedelta(minutes=5))

    assert policy.calls == []
