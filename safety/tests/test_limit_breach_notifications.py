"""Behaviour of the Limit Breach Notifications blueprint, run in a real Home Assistant core.

Each test watches one freezer temperature sensor, in °F. Unless a test says
otherwise, the Limit Breach is the reading above 15, for no time at all,
with the normal sound.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.phones import Phone
from testing.sensors import Sensors

BLUEPRINT = "safety/blueprints/automation/safety_limit_breach_notifications.yaml"
SENSOR = "sensor.example_freezer_temperature"
TITLE = "Freezer too warm"
MESSAGE = "The chest freezer is above 15 °F."
CRITICAL = {
    "sound": {"name": "default", "critical": 1, "volume": 1.0},
    "interruption-level": "critical",
}
NORMAL = {"sound": "default"}
SILENT = {"sound": "none", "interruption-level": "passive"}


@dataclass
class Watch:
    hass: HomeAssistant
    clock: Clock
    sensors: Sensors
    recipient: Phone

    async def start(self, recipients: list[Phone] | None = None, **inputs: Any) -> None:
        """Create the automation for one reading, with any other inputs."""
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "sensor": SENSOR,
                        "direction": "above",
                        "limit": 15,
                        "title": TITLE,
                        "message": MESSAGE,
                        "recipients": [
                            phone.device_id for phone in recipients or [self.recipient]
                        ],
                        **inputs,
                    },
                    alias="Freezer temperature",
                )
            ],
        )

    async def set(self, state: str, unit: str | None = "°F") -> None:
        await self.sensors.set(SENSOR, state, unit=unit)
        await self.clock.let_runs_start()

    async def wait(self, minutes: float) -> None:
        """Let time pass, without waiting for a run that waits for the Breach to clear."""
        await self.clock.advance(
            timedelta(minutes=minutes), step=timedelta(seconds=30), settle=False
        )

    @property
    def notifications(self) -> list[tuple[str, str]]:
        return [(n["title"], n["message"]) for n in self.recipient.notifications]

    @property
    def titles(self) -> list[str]:
        return [n["title"] for n in self.recipient.notifications]


@pytest.fixture
async def watch(
    hass: HomeAssistant, clock: Clock, sensors: Sensors, recipient: Phone
) -> Watch:
    watch = Watch(hass, clock, sensors, recipient)
    await watch.set("5")
    return watch


# Breach


async def test_a_reading_above_the_limit_is_a_breach(watch: Watch) -> None:
    await watch.start()

    await watch.set("18")

    assert watch.notifications == [(TITLE, f"{MESSAGE} Now 18 °F.")]


async def test_a_reading_at_the_limit_is_not_a_breach(watch: Watch) -> None:
    await watch.start()

    await watch.set("15")

    assert watch.notifications == []


async def test_a_reading_below_the_limit_is_a_breach_when_watching_below(
    watch: Watch,
) -> None:
    await watch.set("40")
    await watch.start(direction="below", limit=32, title="Too cold", message="Brr.")

    await watch.set("31.5")

    assert watch.notifications == [("Too cold", "Brr. Now 31.5 °F.")]


async def test_a_reading_above_the_limit_is_not_a_breach_when_watching_below(
    watch: Watch,
) -> None:
    await watch.start(direction="below", limit=0)

    await watch.set("18")

    assert watch.notifications == []


async def test_the_value_is_given_without_a_unit_when_the_sensor_has_none(
    watch: Watch,
) -> None:
    await watch.set("50", unit=None)
    await watch.start(limit=100, title="Air quality", message="Outdoor AQI is above 100.")

    await watch.set("142", unit=None)

    assert watch.notifications == [("Air quality", "Outdoor AQI is above 100. Now 142.")]


async def test_a_reading_moving_further_past_the_limit_is_not_a_new_breach(
    watch: Watch,
) -> None:
    await watch.start()
    await watch.set("18")

    await watch.set("22")

    assert watch.titles == [TITLE]


# Duration


async def test_a_breach_is_notified_once_past_the_limit_for_the_duration(
    watch: Watch,
) -> None:
    await watch.start(duration={"minutes": 30})

    await watch.set("18")
    await watch.wait(29.5)
    assert watch.notifications == []
    await watch.set("19")
    await watch.wait(0.5)

    assert watch.notifications == [(TITLE, f"{MESSAGE} Now 19 °F.")]


async def test_a_reading_back_within_the_limit_before_the_duration_is_not_a_breach(
    watch: Watch,
) -> None:
    await watch.start(duration={"minutes": 30})

    await watch.set("18")
    await watch.wait(29)
    await watch.set("12")
    await watch.wait(30)

    assert watch.notifications == []


# Critical


async def test_a_breach_has_the_normal_sound_by_default(watch: Watch) -> None:
    await watch.start()

    await watch.set("18")

    (breach,) = watch.recipient.notifications
    assert breach["data"]["push"] == NORMAL


async def test_a_critical_breach_has_the_critical_sound(watch: Watch) -> None:
    await watch.start(critical=True)

    await watch.set("18")

    (breach,) = watch.recipient.notifications
    assert breach["data"]["push"] == CRITICAL


# Cleared


async def test_clearing_silently_replaces_the_breach(watch: Watch) -> None:
    await watch.start()
    await watch.set("18")

    await watch.set("12")

    breach, cleared = watch.recipient.notifications
    assert (cleared["title"], cleared["message"]) == (
        f"{TITLE} cleared",
        f"{MESSAGE} Now 12 °F.",
    )
    assert cleared["data"]["tag"] == breach["data"]["tag"]
    assert cleared["data"]["push"] == SILENT


async def test_a_reading_at_the_limit_clears_the_breach(watch: Watch) -> None:
    await watch.start()
    await watch.set("18")

    await watch.set("15")

    assert watch.titles == [TITLE, f"{TITLE} cleared"]


async def test_clearing_is_silent_even_after_a_critical_breach(watch: Watch) -> None:
    await watch.start(critical=True)
    await watch.set("18")

    await watch.set("12")

    assert watch.recipient.notifications[-1]["data"]["push"] == SILENT


async def test_when_watching_below_a_reading_back_above_clears_the_breach(
    watch: Watch,
) -> None:
    await watch.set("40")
    await watch.start(direction="below", limit=32)
    await watch.set("30")

    await watch.set("33")

    assert watch.titles == [TITLE, f"{TITLE} cleared"]


async def test_a_breach_clears_only_once(watch: Watch) -> None:
    await watch.start()

    await watch.set("18")
    await watch.set("12")
    await watch.set("10")

    assert watch.titles == [TITLE, f"{TITLE} cleared"]


async def test_a_second_breach_after_clearing_is_notified(watch: Watch) -> None:
    await watch.start()
    await watch.set("18")
    await watch.set("12")

    await watch.set("17")

    assert watch.titles == [TITLE, f"{TITLE} cleared", TITLE]


# Unavailable readings


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_an_unavailable_reading_does_not_clear_the_breach(
    watch: Watch, state: str
) -> None:
    await watch.start()
    await watch.set("18")

    await watch.set(state, unit=None)
    await watch.wait(10)
    assert watch.titles == [TITLE]
    await watch.set("12")

    assert watch.titles == [TITLE, f"{TITLE} cleared"]


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_an_unavailable_reading_is_not_a_breach(watch: Watch, state: str) -> None:
    await watch.start(direction="below", limit=0)

    await watch.set(state, unit=None)
    await watch.wait(10)

    assert watch.notifications == []


async def test_no_clear_after_an_unavailable_reading_without_a_breach(
    watch: Watch,
) -> None:
    await watch.set("unavailable", unit=None)
    await watch.start()

    await watch.set("12")

    assert watch.notifications == []


async def test_a_reading_coming_back_past_the_limit_warns_again(watch: Watch) -> None:
    await watch.start()
    await watch.set("18")
    await watch.set("unavailable", unit=None)

    await watch.set("19")
    await watch.set("12")

    breach, again, cleared = watch.recipient.notifications
    assert watch.titles == [TITLE, TITLE, f"{TITLE} cleared"]
    assert again["message"] == f"{MESSAGE} Now 19 °F."
    assert again["data"]["tag"] == breach["data"]["tag"]


# Recipients


async def test_every_recipient_is_warned_and_told_it_cleared(
    watch: Watch, other_recipient: Phone
) -> None:
    await watch.start(recipients=[watch.recipient, other_recipient])

    await watch.set("18")
    await watch.set("12")

    for phone in (watch.recipient, other_recipient):
        assert [n["title"] for n in phone.notifications] == [TITLE, f"{TITLE} cleared"]


async def test_each_reading_has_its_own_tag(watch: Watch, sensors: Sensors) -> None:
    other = "sensor.example_outdoor_aqi"
    await sensors.set(other, "50")
    await async_setup_automations(
        watch.hass,
        [
            blueprint_automation(
                BLUEPRINT,
                {
                    "sensor": sensor,
                    "direction": "above",
                    "limit": limit,
                    "title": title,
                    "message": title,
                    "recipients": [watch.recipient.device_id],
                },
                alias=title,
            )
            for sensor, limit, title in [(SENSOR, 15, TITLE), (other, 100, "Air quality")]
        ],
    )

    await watch.set("18")
    await sensors.set(other, "142")
    await watch.clock.let_runs_start()

    tags = {n["title"]: n["data"]["tag"] for n in watch.recipient.notifications}
    assert set(tags) == {TITLE, "Air quality"}
    assert tags[TITLE] != tags["Air quality"]


# Home Assistant starting


async def test_a_breach_already_under_way_at_start_is_not_notified(watch: Watch) -> None:
    await watch.set("18")

    await watch.start()
    await watch.wait(10)
    await watch.set("12")

    assert watch.notifications == []
