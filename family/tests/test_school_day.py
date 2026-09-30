"""Behaviour of the School Day blueprint, run in a real Home Assistant core."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.calendar import Calendar, Event
from testing.clock import Clock
from testing.helpers import Helpers

BLUEPRINT = "family/blueprints/automation/family_school_day.yaml"
FIRST_DAY = date(2026, 9, 1)
LAST_DAY = date(2027, 6, 18)


@dataclass
class SchoolDay:
    hass: HomeAssistant
    clock: Clock
    district: Calendar
    closures: Calendar
    today_helper: str
    tomorrow_helper: str

    async def start(self, **inputs: Any) -> None:
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "district_calendar": self.district.entity_id,
                        "closures_calendar": self.closures.entity_id,
                        "today_school_day": self.today_helper,
                        "tomorrow_school_day": self.tomorrow_helper,
                        **inputs,
                    },
                    alias="School Day",
                )
            ],
        )

    async def run_at_six(self, day: date) -> None:
        await self.clock.move_to(
            datetime.combine(day, datetime.min.time()).replace(hour=6)
        )
        await self.hass.services.async_call(
            "automation",
            "trigger",
            {"entity_id": "automation.school_day"},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    @property
    def today(self) -> bool:
        return self.hass.states.get(self.today_helper).state == "on"

    @property
    def tomorrow(self) -> bool:
        return self.hass.states.get(self.tomorrow_helper).state == "on"


@pytest.fixture
async def school_day(
    hass: HomeAssistant,
    clock: Clock,
    helpers: Helpers,
    calendars: dict[str, Calendar],
) -> SchoolDay:
    today = await helpers.input_boolean("Example School Day Today")
    tomorrow = await helpers.input_boolean("Example School Day Tomorrow")
    district = calendars["district"]
    closures = calendars["closures"]
    await district.set_events(
        [
            Event(FIRST_DAY, FIRST_DAY + timedelta(days=1), "First day of school"),
            Event(LAST_DAY, LAST_DAY + timedelta(days=1), "Last day of school"),
        ]
    )
    await closures.set_events([])
    return SchoolDay(hass, clock, district, closures, today, tomorrow)


async def test_a_normal_weekday_is_a_school_day(school_day: SchoolDay) -> None:
    await school_day.start()

    await school_day.run_at_six(date(2026, 9, 2))

    assert school_day.today is True
    assert school_day.tomorrow is True


async def test_a_weekend_is_not_a_school_day(school_day: SchoolDay) -> None:
    await school_day.start()

    await school_day.run_at_six(date(2026, 9, 5))

    assert school_day.today is False
    assert school_day.tomorrow is False


async def test_a_closure_is_not_a_school_day(school_day: SchoolDay) -> None:
    await school_day.closures.set_events(
        [Event(date(2026, 9, 3), date(2026, 9, 4), "No school")]
    )
    await school_day.start()

    await school_day.run_at_six(date(2026, 9, 3))

    assert school_day.today is False
    assert school_day.tomorrow is True


@pytest.mark.parametrize("day", [date(2026, 8, 31), date(2027, 6, 19)])
async def test_before_the_first_day_and_after_the_last_day_are_not_school_days(
    school_day: SchoolDay, day: date
) -> None:
    await school_day.start()

    await school_day.run_at_six(day)

    assert school_day.today is False


async def test_a_friday_publishes_that_tomorrow_is_not_a_school_day(
    school_day: SchoolDay,
) -> None:
    await school_day.start()

    await school_day.run_at_six(date(2026, 9, 4))

    assert school_day.today is True
    assert school_day.tomorrow is False


async def test_summary_phrases_are_inputs(school_day: SchoolDay) -> None:
    await school_day.district.set_events(
        [
            Event(FIRST_DAY, FIRST_DAY + timedelta(days=1), "Classes begin"),
            Event(LAST_DAY, LAST_DAY + timedelta(days=1), "Classes end"),
        ]
    )
    await school_day.closures.set_events(
        [Event(date(2026, 9, 2), date(2026, 9, 3), "Buildings closed")]
    )
    await school_day.start(
        first_day_phrase="classes begin",
        last_day_phrase="classes end",
        closure_phrase="buildings closed",
    )

    await school_day.run_at_six(date(2026, 9, 2))

    assert school_day.today is False
    assert school_day.tomorrow is True
