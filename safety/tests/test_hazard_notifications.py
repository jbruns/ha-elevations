"""Behaviour of the Hazard Notifications blueprint, run in a real Home Assistant core.

Each test watches one sensor. Unless a test says otherwise, the Hazard is the
sensor being on, held for no time at all, with a critical sound.
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

BLUEPRINT = "safety/blueprints/automation/safety_hazard_notifications.yaml"
SENSOR = "binary_sensor.example_leak"
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
        """Create the automation for one Hazard, with any other inputs."""
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "sensor": SENSOR,
                        "title": "Water leak",
                        "message": "Leak at the kitchen sink",
                        "recipients": [
                            phone.device_id for phone in recipients or [self.recipient]
                        ],
                        **inputs,
                    },
                    alias="Kitchen sink leak",
                )
            ],
        )

    async def set(self, state: str) -> None:
        await self.sensors.set(SENSOR, state)
        await self.clock.let_runs_start()

    async def wait(self, minutes: float) -> None:
        """Let time pass, without waiting for a run that waits for the Hazard to clear."""
        await self.clock.advance(
            timedelta(minutes=minutes), step=timedelta(seconds=30), settle=False
        )

    @property
    def notifications(self) -> list[tuple[str, str]]:
        return [(n["title"], n["message"]) for n in self.recipient.notifications]


@pytest.fixture
async def watch(
    hass: HomeAssistant, clock: Clock, sensors: Sensors, recipient: Phone
) -> Watch:
    watch = Watch(hass, clock, sensors, recipient)
    await watch.set("off")
    return watch


# Hazard


async def test_the_sensor_turning_on_is_a_hazard(watch: Watch) -> None:
    await watch.start()

    await watch.set("on")

    assert watch.notifications == [("Water leak", "Leak at the kitchen sink")]


async def test_the_sensor_turning_off_is_not_a_hazard(watch: Watch) -> None:
    await watch.set("on")
    await watch.start()

    await watch.set("off")

    assert watch.notifications == []


async def test_with_inverted_polarity_the_sensor_turning_off_is_a_hazard(
    watch: Watch,
) -> None:
    await watch.set("on")
    await watch.start(hazard_state="off")

    await watch.set("off")

    assert watch.notifications == [("Water leak", "Leak at the kitchen sink")]


async def test_with_inverted_polarity_the_sensor_turning_on_is_not_a_hazard(
    watch: Watch,
) -> None:
    await watch.start(hazard_state="off")

    await watch.set("on")

    assert watch.notifications == []


# Held for


async def test_a_hazard_is_notified_once_held_for_the_duration(watch: Watch) -> None:
    await watch.start(held_for={"minutes": 10})

    await watch.set("on")
    await watch.wait(9.5)
    assert watch.notifications == []
    await watch.wait(0.5)

    assert watch.notifications == [("Water leak", "Leak at the kitchen sink")]


async def test_a_hazard_that_ends_before_the_duration_is_not_notified(watch: Watch) -> None:
    await watch.start(held_for={"minutes": 10})

    await watch.set("on")
    await watch.wait(9)
    await watch.set("off")
    await watch.wait(10)

    assert watch.notifications == []


# Critical


async def test_a_hazard_is_critical_by_default(watch: Watch) -> None:
    await watch.start()

    await watch.set("on")

    (hazard,) = watch.recipient.notifications
    assert hazard["data"]["push"] == CRITICAL


async def test_a_hazard_that_is_not_critical_has_the_normal_sound(watch: Watch) -> None:
    await watch.start(critical=False)

    await watch.set("on")

    (hazard,) = watch.recipient.notifications
    assert hazard["data"]["push"] == NORMAL


# Cleared


async def test_clearing_silently_replaces_the_hazard(watch: Watch) -> None:
    await watch.start()
    await watch.set("on")

    await watch.set("off")

    hazard, cleared = watch.recipient.notifications
    assert (cleared["title"], cleared["message"]) == (
        "Water leak cleared",
        "Leak at the kitchen sink",
    )
    assert cleared["data"]["tag"] == hazard["data"]["tag"]
    assert cleared["data"]["push"] == SILENT


@pytest.mark.parametrize("critical", [True, False])
async def test_clearing_is_silent_however_the_hazard_sounded(
    watch: Watch, critical: bool
) -> None:
    await watch.start(critical=critical)
    await watch.set("on")

    await watch.set("off")

    assert watch.recipient.notifications[-1]["data"]["push"] == SILENT


async def test_with_inverted_polarity_the_sensor_turning_on_clears_the_hazard(
    watch: Watch,
) -> None:
    await watch.set("on")
    await watch.start(hazard_state="off")
    await watch.set("off")

    await watch.set("on")

    assert watch.notifications == [
        ("Water leak", "Leak at the kitchen sink"),
        ("Water leak cleared", "Leak at the kitchen sink"),
    ]


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_the_sensor_dropping_out_does_not_clear_the_hazard(
    watch: Watch, state: str
) -> None:
    await watch.start()
    await watch.set("on")

    await watch.set(state)
    await watch.wait(10)
    assert [title for title, _ in watch.notifications] == ["Water leak"]
    await watch.set("off")

    assert [title for title, _ in watch.notifications] == [
        "Water leak",
        "Water leak cleared",
    ]


async def test_no_clear_without_a_hazard(watch: Watch) -> None:
    await watch.set("unavailable")
    await watch.start()

    await watch.set("off")

    assert watch.notifications == []


async def test_the_sensor_coming_back_in_the_hazard_state_warns_again(watch: Watch) -> None:
    await watch.start()
    await watch.set("on")
    await watch.set("unavailable")

    await watch.set("on")
    await watch.set("off")

    hazard, again, cleared = watch.recipient.notifications
    assert [n["title"] for n in (hazard, again, cleared)] == [
        "Water leak",
        "Water leak",
        "Water leak cleared",
    ]
    assert again["data"]["push"] == CRITICAL
    assert again["data"]["tag"] == hazard["data"]["tag"]


async def test_a_second_hazard_after_clearing_is_notified(watch: Watch) -> None:
    await watch.start()
    await watch.set("on")
    await watch.set("off")

    await watch.set("on")

    assert [title for title, _ in watch.notifications] == [
        "Water leak",
        "Water leak cleared",
        "Water leak",
    ]


# Recipients


async def test_every_recipient_is_warned_and_told_it_cleared(
    watch: Watch, other_recipient: Phone
) -> None:
    await watch.start(recipients=[watch.recipient, other_recipient])

    await watch.set("on")
    await watch.set("off")

    for phone in (watch.recipient, other_recipient):
        assert [n["title"] for n in phone.notifications] == [
            "Water leak",
            "Water leak cleared",
        ]


async def test_each_hazard_has_its_own_tag(watch: Watch, sensors: Sensors) -> None:
    other = "binary_sensor.example_smoke"
    await sensors.set(other, "off")
    await async_setup_automations(
        watch.hass,
        [
            blueprint_automation(
                BLUEPRINT,
                {
                    "sensor": sensor,
                    "title": title,
                    "message": title,
                    "recipients": [watch.recipient.device_id],
                },
                alias=title,
            )
            for sensor, title in [(SENSOR, "Water leak"), (other, "Smoke")]
        ],
    )

    await watch.set("on")
    await sensors.set(other, "on")
    await watch.clock.let_runs_start()

    tags = {n["title"]: n["data"]["tag"] for n in watch.recipient.notifications}
    assert set(tags) == {"Water leak", "Smoke"}
    assert tags["Water leak"] != tags["Smoke"]


# Home Assistant starting


async def test_a_sensor_coming_back_in_the_hazard_state_is_a_hazard(watch: Watch) -> None:
    await watch.set("unavailable")
    await watch.start()

    await watch.set("on")

    assert watch.notifications == [("Water leak", "Leak at the kitchen sink")]


async def test_a_hazard_already_under_way_at_start_is_not_notified(watch: Watch) -> None:
    await watch.set("on")

    await watch.start()
    await watch.wait(10)
    await watch.set("off")

    assert watch.notifications == []
