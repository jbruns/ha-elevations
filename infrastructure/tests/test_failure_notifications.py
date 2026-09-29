"""Behaviour of the Failure Notifications blueprint, run in a real Home Assistant core.

Each test watches one Monitored System. Unless a test says otherwise, the grace
period is the blueprint's default of 5 minutes.
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

BLUEPRINT = "infrastructure/blueprints/automation/infrastructure_failure_notifications.yaml"
MINUTE = timedelta(minutes=1)
FIRST = "light.example_probe_1"
SECOND = "light.example_probe_2"
NAMES = {FIRST: "Probe Light 1", SECOND: "Probe Light 2"}


@dataclass
class Monitor:
    hass: HomeAssistant
    clock: Clock
    sensors: Sensors
    administrator: Phone

    async def start(self, entities: list[str] | None = None, **inputs: Any) -> None:
        """Create the automation for a Monitored System, with any other inputs."""
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "system": "Probe lights",
                        "entities": entities or list(NAMES),
                        "administrator": self.administrator.device_id,
                        **inputs,
                    },
                    alias="Probe lights failure",
                )
            ],
        )

    async def set(self, entity_id: str, state: Any, **attributes: Any) -> None:
        await self.sensors.set(
            entity_id, state, friendly_name=NAMES.get(entity_id), **attributes
        )
        await self.clock.let_runs_start()

    async def wait(self, minutes: float) -> None:
        """Let time pass, without waiting for a run that waits for a Recovery."""
        await self.clock.advance(timedelta(minutes=minutes), step=MINUTE / 2, settle=False)

    @property
    def notifications(self) -> list[tuple[str, str]]:
        return [(n["title"], n["message"]) for n in self.administrator.notifications]


@pytest.fixture
async def monitor(
    hass: HomeAssistant, clock: Clock, sensors: Sensors, administrator: Phone
) -> Monitor:
    monitor = Monitor(hass, clock, sensors, administrator)
    for entity_id in NAMES:
        await monitor.set(entity_id, "on")
    return monitor


# Unavailable or unknown


async def test_an_entity_unavailable_for_the_grace_period_is_a_failure(
    monitor: Monitor,
) -> None:
    await monitor.start(test="unavailable")

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(4.5)
    assert monitor.notifications == []
    await monitor.wait(0.5)

    assert monitor.notifications == [
        ("Probe lights failed", "Probe Light 1 is unavailable.")
    ]


async def test_unknown_counts_as_unavailable(monitor: Monitor) -> None:
    await monitor.start(test="unavailable")

    await monitor.set(FIRST, "unknown")
    await monitor.wait(5)

    assert monitor.notifications == [("Probe lights failed", "Probe Light 1 is unknown.")]


async def test_a_brief_dropout_is_not_a_failure(monitor: Monitor) -> None:
    await monitor.start(test="unavailable")

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(4)
    await monitor.set(FIRST, "on")
    await monitor.wait(10)

    assert monitor.notifications == []


async def test_the_grace_period_is_an_input(monitor: Monitor) -> None:
    await monitor.start(test="unavailable", grace={"minutes": 1})

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(0.5)
    assert monitor.notifications == []
    await monitor.wait(0.5)

    assert len(monitor.notifications) == 1


# Any or all, for each failed test

HEALTHY = {"unavailable": "on", "not_expected": "on", "below": "50", "above": "50"}
FAILING = {"unavailable": "unavailable", "not_expected": "off", "below": "20", "above": "95"}
INPUTS: dict[str, dict[str, Any]] = {
    "unavailable": {},
    "not_expected": {"expected_states": ["on"]},
    "below": {"limit": 30},
    "above": {"limit": 90},
}


@pytest.mark.parametrize("test", list(INPUTS))
async def test_with_any_one_failing_entity_is_a_failure(monitor: Monitor, test: str) -> None:
    for entity_id in NAMES:
        await monitor.set(entity_id, HEALTHY[test])
    await monitor.start(test=test, require="any", **INPUTS[test])

    await monitor.set(SECOND, FAILING[test])
    await monitor.wait(5)

    assert [title for title, _ in monitor.notifications] == ["Probe lights failed"]


@pytest.mark.parametrize("test", list(INPUTS))
async def test_with_all_one_failing_entity_is_not_a_failure(
    monitor: Monitor, test: str
) -> None:
    for entity_id in NAMES:
        await monitor.set(entity_id, HEALTHY[test])
    await monitor.start(test=test, require="all", **INPUTS[test])

    await monitor.set(SECOND, FAILING[test])
    await monitor.wait(10)

    assert monitor.notifications == []


@pytest.mark.parametrize("test", list(INPUTS))
async def test_with_all_every_entity_failing_is_a_failure(monitor: Monitor, test: str) -> None:
    for entity_id in NAMES:
        await monitor.set(entity_id, HEALTHY[test])
    await monitor.start(test=test, require="all", **INPUTS[test])

    await monitor.set(FIRST, FAILING[test])
    await monitor.wait(3)
    await monitor.set(SECOND, FAILING[test])
    await monitor.wait(4.5)
    assert monitor.notifications == []
    await monitor.wait(0.5)

    assert [title for title, _ in monitor.notifications] == ["Probe lights failed"]


# What the Failure says


async def test_every_failing_entity_is_named(monitor: Monitor) -> None:
    await monitor.start(test="unavailable", require="all")

    await monitor.set(FIRST, "unavailable")
    await monitor.set(SECOND, "unavailable")
    await monitor.wait(5)

    assert monitor.notifications == [
        ("Probe lights failed", "Probe Light 1 is unavailable. Probe Light 2 is unavailable.")
    ]


async def test_a_state_failure_names_the_expected_states(monitor: Monitor) -> None:
    await monitor.start([FIRST], test="not_expected", expected_states=["on", "ready"])

    await monitor.set(FIRST, "offline")
    await monitor.wait(5)

    assert monitor.notifications == [
        ("Probe lights failed", "Probe Light 1 is offline, not on or ready.")
    ]


async def test_unavailable_is_not_an_expected_state(monitor: Monitor) -> None:
    await monitor.start([FIRST], test="not_expected", expected_states=["on"])

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(5)

    assert monitor.notifications == [
        ("Probe lights failed", "Probe Light 1 is unavailable, not on.")
    ]


async def test_a_numeric_failure_gives_the_value_and_the_limit(monitor: Monitor) -> None:
    await monitor.set(FIRST, "80", unit="%")
    await monitor.start([FIRST], test="below", limit=30)

    await monitor.set(FIRST, "24.5", unit="%")
    await monitor.wait(5)

    assert monitor.notifications == [
        ("Probe lights failed", "Probe Light 1 is 24.5 %, below 30 %.")
    ]


async def test_an_above_failure_gives_the_value_and_the_limit(monitor: Monitor) -> None:
    await monitor.set(FIRST, "80", unit="°F")
    await monitor.start([FIRST], test="above", limit=90.5)

    await monitor.set(FIRST, "92", unit="°F")
    await monitor.wait(5)

    assert monitor.notifications == [
        ("Probe lights failed", "Probe Light 1 is 92 °F, above 90.5 °F.")
    ]


@pytest.mark.parametrize(("test", "before"), [("below", "50"), ("above", "10")])
async def test_a_value_at_the_limit_is_not_failing(
    monitor: Monitor, test: str, before: str
) -> None:
    await monitor.set(FIRST, before)
    await monitor.start([FIRST], test=test, limit=30)

    await monitor.set(FIRST, "30")
    await monitor.wait(10)

    assert monitor.notifications == []


async def test_a_numeric_test_ignores_a_state_that_is_not_a_number(monitor: Monitor) -> None:
    await monitor.set(FIRST, "80")
    await monitor.start([FIRST], test="below", limit=30)

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(10)

    assert monitor.notifications == []


# Recovery


async def failed(monitor: Monitor, **inputs: Any) -> None:
    await monitor.start(**{"test": "unavailable", **inputs})
    await monitor.set(FIRST, "unavailable")
    await monitor.wait(5)
    assert len(monitor.notifications) == 1


async def test_the_recovery_silently_replaces_the_failure(monitor: Monitor) -> None:
    await failed(monitor)

    await monitor.set(FIRST, "on")

    failure, recovery = monitor.administrator.notifications
    assert (recovery["title"], recovery["message"]) == (
        "Probe lights recovered",
        "Probe lights is healthy again.",
    )
    assert recovery["data"]["tag"] == failure["data"]["tag"]
    assert failure["data"]["push"] == {"sound": "default"}
    assert recovery["data"]["push"] == {"sound": "none", "interruption-level": "passive"}


async def test_recovery_waits_for_the_test_to_stop_holding(monitor: Monitor) -> None:
    await failed(monitor)

    await monitor.set(FIRST, "unknown")
    await monitor.wait(10)
    assert len(monitor.notifications) == 1
    await monitor.set(FIRST, "on")

    assert [title for title, _ in monitor.notifications] == [
        "Probe lights failed",
        "Probe lights recovered",
    ]


async def test_a_numeric_entity_dropping_out_after_a_failure_is_not_a_recovery(
    monitor: Monitor,
) -> None:
    await monitor.set(FIRST, "80")
    await monitor.start([FIRST], test="below", limit=30)
    await monitor.set(FIRST, "20")
    await monitor.wait(5)

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(10)
    assert [title for title, _ in monitor.notifications] == ["Probe lights failed"]
    await monitor.set(FIRST, "50")

    assert [title for title, _ in monitor.notifications] == [
        "Probe lights failed",
        "Probe lights recovered",
    ]


async def test_with_all_one_entity_back_is_a_recovery(monitor: Monitor) -> None:
    await monitor.start(test="unavailable", require="all")
    await monitor.set(FIRST, "unavailable")
    await monitor.set(SECOND, "unavailable")
    await monitor.wait(5)

    await monitor.set(SECOND, "on")

    assert [title for title, _ in monitor.notifications] == [
        "Probe lights failed",
        "Probe lights recovered",
    ]


async def test_no_recovery_without_a_failure(monitor: Monitor) -> None:
    await monitor.start(test="unavailable")

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(4)
    await monitor.set(FIRST, "on")
    await monitor.wait(10)

    assert monitor.notifications == []


async def test_a_second_failure_after_a_recovery_is_notified(monitor: Monitor) -> None:
    await failed(monitor)
    await monitor.set(FIRST, "on")

    await monitor.set(FIRST, "unavailable")
    await monitor.wait(5)

    assert [title for title, _ in monitor.notifications] == [
        "Probe lights failed",
        "Probe lights recovered",
        "Probe lights failed",
    ]


async def test_each_monitored_system_has_its_own_tag(monitor: Monitor) -> None:
    await async_setup_automations(
        monitor.hass,
        [
            blueprint_automation(
                BLUEPRINT,
                {
                    "system": name,
                    "entities": [entity_id],
                    "administrator": monitor.administrator.device_id,
                },
                alias=f"{name} failure",
            )
            for name, entity_id in [("Probe one", FIRST), ("Probe two", SECOND)]
        ],
    )

    await monitor.set(FIRST, "unavailable")
    await monitor.set(SECOND, "unavailable")
    await monitor.wait(5)

    tags = {n["title"]: n["data"]["tag"] for n in monitor.administrator.notifications}
    assert set(tags) == {"Probe one failed", "Probe two failed"}
    assert tags["Probe one failed"] != tags["Probe two failed"]


# Home Assistant starting


async def test_no_notification_at_start_for_a_condition_already_true(
    monitor: Monitor,
) -> None:
    await monitor.set(FIRST, "unavailable")

    await monitor.start(test="unavailable")
    await monitor.wait(10)
    await monitor.set(FIRST, "on")
    await monitor.wait(1)

    assert monitor.notifications == []


async def test_a_numeric_value_already_past_the_limit_at_start_is_not_notified(
    monitor: Monitor,
) -> None:
    await monitor.set(FIRST, "20")

    await monitor.start([FIRST], test="below", limit=30)
    await monitor.wait(10)

    assert monitor.notifications == []


# Only the Administrator


async def test_only_the_administrator_is_notified(
    monitor: Monitor, recipient: Phone, other_recipient: Phone
) -> None:
    notify_calls: list[str] = []
    monitor.hass.bus.async_listen(
        "call_service",
        lambda event: notify_calls.append(
            f"{event.data['domain']}.{event.data['service']}"
        )
        if event.data["domain"] == "notify"
        else None,
    )
    await failed(monitor)
    await monitor.set(FIRST, "on")

    assert notify_calls == [monitor.administrator.notify] * 2
    assert recipient.notifications == []
    assert other_recipient.notifications == []
