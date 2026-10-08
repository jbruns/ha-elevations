"""The Wallboard package's Wallboard-local roles (ADR 0010), run in Home Assistant."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, State
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
    setup_test_component_platform,
)

from scripts.render_assets import load_overlay, render_text
from testing.calendar import Calendar, Event
from testing.clock import Clock
from testing.household import Household
from testing.sun import Sun

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "wallboard" / "package.yaml"
OVERLAY = ROOT / "wallboard" / "wallboard.local.example.yaml"

UNLOCKED_DOOR = "binary_sensor.wallboard_unlocked_door"
UNLOCKED_DOORS = "sensor.wallboard_unlocked_doors"
FRONT_DOOR = "lock.example_front_door"
SIDE_DOOR = "lock.example_side_door"
DOOR_INTO_GARAGE = "lock.example_door_into_garage"
GARAGE_DOOR = "cover.example_garage_door"


@pytest.fixture
def expected_lingering_timers() -> bool:
    # Appliance-running sensors debounce with delays; other roles refresh on time patterns.
    return True


@pytest.fixture(autouse=True)
async def wallboard_calendars(hass: HomeAssistant) -> dict[str, Calendar]:
    """The calendars the package reads, at their placeholder IDs; empty unless a test fills them."""
    calendars = {
        name: Calendar(f"calendar.example_{name}", name)
        for name in ("collection", "trips_breaks", "us_holidays", "birthdays")
    }
    setup_test_component_platform(hass, "calendar", list(calendars.values()))
    assert await async_setup_component(hass, "calendar", {"calendar": {"platform": "test"}})
    await hass.async_block_till_done()
    return calendars


def rendered_package_config() -> dict[str, Any]:
    return yaml.safe_load(render_text(PACKAGE.read_text(), load_overlay(OVERLAY), source=PACKAGE))


async def setup_package(hass: HomeAssistant) -> None:
    for domain, config in rendered_package_config().items():
        assert await async_setup_component(hass, domain, {domain: config})
    await hass.async_block_till_done()


async def start_home_assistant(hass: HomeAssistant) -> None:
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


async def lock_states(hass: HomeAssistant, *, unlocked: list[str], garage_door: str = "closed") -> None:
    for lock in (FRONT_DOOR, SIDE_DOOR, DOOR_INTO_GARAGE):
        hass.states.async_set(lock, "unlocked" if lock in unlocked else "locked")
    hass.states.async_set(GARAGE_DOOR, garage_door)
    await hass.async_block_till_done()


def unlocked_doors(hass: HomeAssistant) -> tuple[str, str]:
    return hass.states.get(UNLOCKED_DOOR).state, hass.states.get(UNLOCKED_DOORS).state


# Unlocked Door


async def test_a_door_left_unlocked_after_sunset_is_an_unlocked_door(
    hass: HomeAssistant, sun: Sun, household: Household
) -> None:
    await household.set_home(1)
    await sun.set_elevation(-5.0)
    await lock_states(hass, unlocked=[FRONT_DOOR])
    await setup_package(hass)

    assert unlocked_doors(hass) == ("on", "Front Door")


async def test_a_door_unlocked_in_daytime_with_someone_home_is_normal(
    hass: HomeAssistant, sun: Sun, household: Household
) -> None:
    await household.set_home(1)
    await lock_states(hass, unlocked=[FRONT_DOOR, SIDE_DOOR])
    await setup_package(hass)

    assert unlocked_doors(hass) == ("off", "")


async def test_a_door_unlocked_in_daytime_while_nobody_is_home_is_an_unlocked_door(
    hass: HomeAssistant, sun: Sun, household: Household
) -> None:
    await household.set_home(1)
    await lock_states(hass, unlocked=[FRONT_DOOR, SIDE_DOOR])
    await setup_package(hass)

    await household.set_home(0)

    assert unlocked_doors(hass) == ("on", "Front Door, Side Door")


async def test_the_door_into_the_garage_counts_only_while_the_garage_door_is_open(
    hass: HomeAssistant, sun: Sun, household: Household
) -> None:
    await household.set_home(1)
    await sun.set_elevation(-5.0)
    await lock_states(hass, unlocked=[DOOR_INTO_GARAGE], garage_door="closed")
    await setup_package(hass)

    assert unlocked_doors(hass) == ("off", "")

    hass.states.async_set(GARAGE_DOOR, "open")
    await hass.async_block_till_done()

    assert unlocked_doors(hass) == ("on", "Door into Garage")


# Finished Cycle

ACKNOWLEDGE = "script.wallboard_acknowledge"
WASHER_POWER = "sensor.example_washer_power"
DRYER_POWER = "sensor.example_dryer_power"
DISHWASHER_POWER = "sensor.example_dishwasher_power"
WASHER_FINISHED = "binary_sensor.wallboard_washer_finished_cycle"
DRYER_FINISHED = "binary_sensor.wallboard_dryer_finished_cycle"


async def run_load(hass: HomeAssistant, clock: Clock, power_sensor: str, watts: str) -> None:
    """Run an appliance for an hour, then let its running sensor see it stop."""
    hass.states.async_set(power_sensor, watts)
    await clock.advance(timedelta(hours=1), step=timedelta(minutes=1))
    hass.states.async_set(power_sensor, "2")
    await clock.advance(timedelta(minutes=31), step=timedelta(minutes=1))


async def acknowledge(hass: HomeAssistant, attention_item: str) -> None:
    await hass.services.async_call(
        "script", "wallboard_acknowledge", {"attention_item": attention_item}, blocking=True
    )
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("power_sensor", "watts", "finished"),
    [(WASHER_POWER, "350", WASHER_FINISHED), (DRYER_POWER, "120", DRYER_FINISHED)],
)
async def test_a_finished_load_is_a_finished_cycle_until_acknowledged(
    hass: HomeAssistant, clock: Clock, power_sensor: str, watts: str, finished: str
) -> None:
    hass.states.async_set(power_sensor, "2")
    await setup_package(hass)
    assert hass.states.get(finished).state != "on"

    await run_load(hass, clock, power_sensor, watts)
    assert hass.states.get(finished).state == "on"

    await acknowledge(hass, finished)
    assert hass.states.get(finished).state == "off"


async def test_a_running_load_is_not_a_finished_cycle(hass: HomeAssistant, clock: Clock) -> None:
    hass.states.async_set(WASHER_POWER, "2")
    await setup_package(hass)

    hass.states.async_set(WASHER_POWER, "350")
    await clock.advance(timedelta(minutes=10), step=timedelta(minutes=1))

    assert hass.states.get("binary_sensor.wallboard_washer_active").state == "on"
    assert hass.states.get(WASHER_FINISHED).state != "on"


async def test_acknowledging_one_finished_cycle_leaves_the_other(hass: HomeAssistant, clock: Clock) -> None:
    hass.states.async_set(WASHER_POWER, "2")
    hass.states.async_set(DRYER_POWER, "2")
    await setup_package(hass)
    await run_load(hass, clock, WASHER_POWER, "350")
    await run_load(hass, clock, DRYER_POWER, "120")

    await acknowledge(hass, DRYER_FINISHED)

    assert hass.states.get(WASHER_FINISHED).state == "on"
    assert hass.states.get(DRYER_FINISHED).state == "off"


async def test_starting_the_next_load_ends_a_finished_cycle(hass: HomeAssistant, clock: Clock) -> None:
    hass.states.async_set(WASHER_POWER, "2")
    await setup_package(hass)
    await run_load(hass, clock, WASHER_POWER, "350")

    hass.states.async_set(WASHER_POWER, "350")
    await clock.advance(timedelta(minutes=3), step=timedelta(minutes=1))

    assert hass.states.get(WASHER_FINISHED).state == "off"


async def test_a_finished_cycle_ages_out_after_three_hours(hass: HomeAssistant, clock: Clock) -> None:
    hass.states.async_set(WASHER_POWER, "2")
    await setup_package(hass)
    await run_load(hass, clock, WASHER_POWER, "350")

    # The cycle finished a minute before run_load returned.
    await clock.advance(timedelta(hours=2, minutes=58))
    assert hass.states.get(WASHER_FINISHED).state == "on"

    await clock.advance(timedelta(minutes=2))
    assert hass.states.get(WASHER_FINISHED).state == "off"


async def test_a_finished_cycle_and_its_time_left_survive_a_restart(hass: HomeAssistant, clock: Clock) -> None:
    ages_out = dt_util.utcnow() + timedelta(hours=1)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(WASHER_FINISHED, "on"),
                {"auto_off_time": {"__type": str(type(ages_out)), "isoformat": ages_out.isoformat()}},
            )
        ],
    )
    hass.states.async_set(WASHER_POWER, "2")
    await setup_package(hass)
    assert hass.states.get(WASHER_FINISHED).state == "on"

    await clock.advance(timedelta(hours=1))
    assert hass.states.get(WASHER_FINISHED).state == "off"


async def test_each_appliance_shows_running_past_its_threshold(hass: HomeAssistant, clock: Clock) -> None:
    hass.states.async_set(WASHER_POWER, "350")
    hass.states.async_set(DRYER_POWER, "120")
    hass.states.async_set(DISHWASHER_POWER, "600")
    await setup_package(hass)
    await clock.advance(timedelta(minutes=2))

    assert hass.states.get("binary_sensor.wallboard_washer_active").state == "on"
    assert hass.states.get("binary_sensor.wallboard_dryer_active").state == "on"
    assert hass.states.get("binary_sensor.wallboard_dishwasher_active").state == "on"


async def test_a_load_that_ends_across_a_restart_is_still_a_finished_cycle(
    hass: HomeAssistant, clock: Clock
) -> None:
    mock_restore_cache_with_extra_data(
        hass, [(State(WASHER_FINISHED, "off", {"running": True}), {"auto_off_time": None})]
    )
    hass.states.async_set(WASHER_POWER, "2")
    await setup_package(hass)

    await clock.advance(timedelta(minutes=31), step=timedelta(minutes=1))

    assert hass.states.get(WASHER_FINISHED).state == "on"


async def test_the_dishwasher_shows_running_but_never_a_finished_cycle(
    hass: HomeAssistant, clock: Clock
) -> None:
    hass.states.async_set(DISHWASHER_POWER, "2")
    await setup_package(hass)

    await run_load(hass, clock, DISHWASHER_POWER, "600")
    assert hass.states.get("binary_sensor.wallboard_dishwasher_finished_cycle") is None

    hass.states.async_set(DISHWASHER_POWER, "600")
    await clock.advance(timedelta(minutes=2))
    assert hass.states.get("binary_sensor.wallboard_dishwasher_active").state == "on"


# Collection Day

BINS_OUT = "binary_sensor.wallboard_bins_out"


def all_day(day: date, summary: str) -> Event:
    return Event(day, day + timedelta(days=1), summary)


async def test_the_evening_before_collection_day_the_bins_go_out(
    hass: HomeAssistant, clock: Clock, wallboard_calendars: dict[str, Calendar]
) -> None:
    await wallboard_calendars["collection"].set_events(
        [
            all_day(date(2026, 10, 13), "Recycle"),
            all_day(date(2026, 10, 13), "Solid Waste"),
            all_day(date(2026, 10, 13), "Recycle"),
        ]
    )
    await clock.move_to(datetime(2026, 10, 12, 16, 30))
    await setup_package(hass)
    await start_home_assistant(hass)
    assert hass.states.get(BINS_OUT).state == "off"

    await clock.move_to(datetime(2026, 10, 12, 17, 0))
    bins_out = hass.states.get(BINS_OUT)
    assert bins_out.state == "on"
    assert bins_out.attributes["bins"] == "Recycle + Solid Waste"

    await clock.advance(timedelta(hours=7), step=timedelta(hours=1))
    assert hass.states.get(BINS_OUT).state == "off"


async def test_no_bins_go_out_when_tomorrow_is_not_a_collection_day(
    hass: HomeAssistant, clock: Clock, wallboard_calendars: dict[str, Calendar]
) -> None:
    await wallboard_calendars["collection"].set_events([all_day(date(2026, 10, 14), "Recycle")])
    await clock.move_to(datetime(2026, 10, 12, 16, 30))
    await setup_package(hass)
    await start_home_assistant(hass)

    await clock.move_to(datetime(2026, 10, 12, 17, 0))

    assert hass.states.get(BINS_OUT).state == "off"


async def test_bins_out_cleared_by_a_tap_stays_cleared_until_the_next_collection_day_eve(
    hass: HomeAssistant, clock: Clock, wallboard_calendars: dict[str, Calendar]
) -> None:
    await wallboard_calendars["collection"].set_events(
        [all_day(date(2026, 10, 13), "Solid Waste"), all_day(date(2026, 10, 20), "Solid Waste")]
    )
    await clock.move_to(datetime(2026, 10, 12, 17, 30))
    await setup_package(hass)
    await start_home_assistant(hass)
    assert hass.states.get(BINS_OUT).state == "on"

    await acknowledge(hass, BINS_OUT)
    assert hass.states.get(BINS_OUT).state == "off"

    await clock.advance(timedelta(hours=2), step=timedelta(minutes=30))
    assert hass.states.get(BINS_OUT).state == "off"

    await clock.move_to(datetime(2026, 10, 19, 17, 0))
    assert hass.states.get(BINS_OUT).state == "on"


async def test_bins_out_cleared_by_a_tap_stays_cleared_across_a_restart(
    hass: HomeAssistant, clock: Clock, wallboard_calendars: dict[str, Calendar]
) -> None:
    await wallboard_calendars["collection"].set_events([all_day(date(2026, 10, 13), "Solid Waste")])
    await clock.move_to(datetime(2026, 10, 12, 18, 0))
    mock_restore_cache_with_extra_data(
        hass, [(State(BINS_OUT, "off", {"bins": "Solid Waste", "acknowledged": "2026-10-13"}), {"auto_off_time": None})]
    )
    await setup_package(hass)
    await start_home_assistant(hass)

    assert hass.states.get(BINS_OUT).state == "off"


# Countdown

COUNTDOWNS = "sensor.wallboard_countdowns"


def local(when: datetime) -> datetime:
    return when.replace(tzinfo=dt_util.get_default_time_zone())


def countdowns(hass: HomeAssistant) -> list[dict[str, Any]]:
    return hass.states.get(COUNTDOWNS).attributes["countdowns"]


async def test_countdowns_show_the_four_soonest_anticipated_events(
    hass: HomeAssistant, clock: Clock, wallboard_calendars: dict[str, Calendar]
) -> None:
    await wallboard_calendars["trips_breaks"].set_events(
        [all_day(date(2026, 11, 20), "Beach trip"), all_day(date(2027, 1, 7), "Ski trip")]
    )
    await wallboard_calendars["us_holidays"].set_events(
        [
            all_day(date(2026, 10, 12), "Columbus Day"),
            all_day(date(2026, 10, 31), "Halloween"),
            all_day(date(2026, 11, 26), "Thanksgiving Day"),
            all_day(date(2026, 12, 25), "Christmas Day"),
        ]
    )
    await wallboard_calendars["birthdays"].set_events(
        [all_day(date(2026, 10, 20), "Sam's birthday"), all_day(date(2026, 11, 15), "Alex's birthday")]
    )
    await clock.move_to(datetime(2026, 10, 8, 10, 0))
    await setup_package(hass)
    await start_home_assistant(hass)

    # Columbus Day is not allowlisted; the ski trip is past 90 days, Alex's birthday past 30.
    assert countdowns(hass) == [
        {"title": "Sam's birthday", "date": "2026-10-20", "days": 12},
        {"title": "Halloween", "date": "2026-10-31", "days": 23},
        {"title": "Beach trip", "date": "2026-11-20", "days": 43},
        {"title": "Thanksgiving Day", "date": "2026-11-26", "days": 49},
    ]
    assert hass.states.get(COUNTDOWNS).state == "4"


async def test_a_countdown_disappears_once_its_event_starts(
    hass: HomeAssistant, clock: Clock, wallboard_calendars: dict[str, Calendar]
) -> None:
    await wallboard_calendars["trips_breaks"].set_events(
        [
            all_day(date(2026, 10, 8), "Fall break"),
            Event(local(datetime(2026, 10, 8, 14, 0)), local(datetime(2026, 10, 8, 18, 0)), "Flight out"),
        ]
    )
    await wallboard_calendars["us_holidays"].set_events([all_day(date(2027, 1, 1), "New Year's Day")])
    await clock.move_to(datetime(2026, 10, 8, 10, 0))
    await setup_package(hass)
    await start_home_assistant(hass)

    assert countdowns(hass) == [
        {"title": "Flight out", "date": "2026-10-08", "days": 0},
        {"title": "New Year's Day", "date": "2027-01-01", "days": 85},
    ]

    await clock.move_to(datetime(2026, 10, 8, 14, 0))

    assert countdowns(hass) == [{"title": "New Year's Day", "date": "2027-01-01", "days": 85}]
