"""Behaviour of the Special Classes blueprint, run in a real Home Assistant core.

One automation sets one child's Special Class helper at 06:05 from its weekday
rotation. Tests use placeholder helper names only; children's names never appear.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.helpers import Helpers

BLUEPRINT = "family/blueprints/automation/family_special_classes.yaml"
DAY = datetime(2026, 9, 28)  # Monday
ROTATION = {
    "monday": "Library",
    "tuesday": "PE",
    "wednesday": "Art",
    "thursday": "Music",
    "friday": "Lab",
}
OPTIONS = ["Not set", "No class", *ROTATION.values()]


@dataclass
class SpecialClasses:
    hass: HomeAssistant
    clock: Clock
    helper: str
    school_day: str

    async def start(self, **inputs: Any) -> None:
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "helper": self.helper,
                        "school_day": self.school_day,
                        **ROTATION,
                        **inputs,
                    },
                    alias="Special Classes",
                )
            ],
        )

    async def school_is(self, on: bool) -> None:
        await self.hass.services.async_call(
            "input_boolean",
            "turn_on" if on else "turn_off",
            {"entity_id": self.school_day},
            blocking=True,
        )

    async def on_day_at_0605(self, offset_days: int) -> None:
        day = DAY + timedelta(days=offset_days)
        await self.clock.move_to(day.replace(hour=6, minute=4))
        await self.clock.move_to(day.replace(hour=6, minute=5))

    @property
    def special_class(self) -> str:
        return self.hass.states.get(self.helper).state


@pytest.fixture
async def special_classes(
    hass: HomeAssistant, clock: Clock, helpers: Helpers
) -> SpecialClasses:
    helper = await helpers.input_select("Example Special Class", OPTIONS, initial="Not set")
    school_day = await helpers.input_boolean("Example School Day", initial=True)
    await clock.move_to(DAY.replace(hour=6))
    return SpecialClasses(hass, clock, helper, school_day)


@pytest.mark.parametrize(
    ("offset_days", "special_class"),
    [
        (0, "Library"),
        (1, "PE"),
        (2, "Art"),
        (3, "Music"),
        (4, "Lab"),
    ],
)
async def test_each_weekday_sets_the_rotated_special_class(
    special_classes: SpecialClasses, offset_days: int, special_class: str
) -> None:
    await special_classes.start()

    await special_classes.on_day_at_0605(offset_days)

    assert special_classes.special_class == special_class


async def test_a_non_school_day_shows_no_class(special_classes: SpecialClasses) -> None:
    await special_classes.school_is(False)
    await special_classes.start()

    await special_classes.on_day_at_0605(0)

    assert special_classes.special_class == "No class"


async def test_school_day_turning_off_reselects_no_class(
    special_classes: SpecialClasses,
) -> None:
    await special_classes.start()
    await special_classes.on_day_at_0605(0)
    assert special_classes.special_class == "Library"

    await special_classes.school_is(False)

    assert special_classes.special_class == "No class"


async def test_weekends_show_no_class(special_classes: SpecialClasses) -> None:
    await special_classes.start()

    await special_classes.on_day_at_0605(5)

    assert special_classes.special_class == "No class"


async def test_school_day_helper_is_optional(special_classes: SpecialClasses) -> None:
    await special_classes.school_is(False)
    await special_classes.start(school_day=[])

    await special_classes.on_day_at_0605(0)

    assert special_classes.special_class == "Library"
