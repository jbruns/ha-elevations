"""Behaviour of the Unresponsive Devices Digest blueprint, run in a real Home Assistant core.

Each Device's communication-error sensor is an isy994 sensor named, as the
integration names it, "<Device> Device Communication Errors". It is 0 while the
Device answers and 1 once it stops. Each test starts at 08:00 and, unless it
says otherwise, the Digest is due at the blueprint's default time of 09:00.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.phones import Phone
from testing.sensors import Sensors

BLUEPRINT = (
    "infrastructure/blueprints/automation/infrastructure_unresponsive_devices_digest.yaml"
)
START = datetime(2026, 6, 1, 8, 0)
NINE = START.replace(hour=9)
DIMMER = "sensor.example_dimmer_device_communication_errors"
KEYPAD = "sensor.example_keypad_device_communication_errors"


@dataclass
class Digest:
    hass: HomeAssistant
    clock: Clock
    sensors: Sensors
    administrator: Phone

    async def start(self, **inputs: Any) -> None:
        """Create the automation, with any inputs besides the Administrator."""
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {"administrator": self.administrator.device_id, **inputs},
                    alias="Unresponsive Devices Digest",
                )
            ],
        )

    async def errors(
        self,
        entity_id: str,
        state: Any,
        device: str,
        integration: str = "isy994",
    ) -> None:
        """A Device's communication-error sensor."""
        await self.sensors.set(
            entity_id,
            state,
            integration=integration,
            device=device,
            friendly_name=f"{device} Device Communication Errors",
        )

    async def run_at(self, when: datetime = NINE) -> None:
        await self.clock.move_to(when)

    @property
    def notifications(self) -> list[tuple[str, str]]:
        return [(n["title"], n["message"]) for n in self.administrator.notifications]

    @property
    def listed(self) -> list[str]:
        """The lines of the only Digest sent."""
        assert len(self.notifications) == 1, self.notifications
        return self.notifications[0][1].split("\n")


@pytest.fixture
async def digest(
    hass: HomeAssistant, clock: Clock, sensors: Sensors, administrator: Phone
) -> Digest:
    await clock.move_to(START)
    return Digest(hass, clock, sensors, administrator)


# Devices reporting errors


async def test_a_device_reporting_errors_is_listed_by_its_name(digest: Digest) -> None:
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.start()

    await digest.run_at()

    assert digest.notifications == [("Unresponsive Devices Digest", "Example Dimmer")]


async def test_a_device_that_answers_is_not_listed(digest: Digest) -> None:
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.errors(KEYPAD, 0, "Example Keypad")
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Example Dimmer"]


async def test_the_list_is_sorted(digest: Digest) -> None:
    await digest.errors(KEYPAD, 1, "Example Keypad")
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.errors(
        "sensor.example_attic_relay_device_communication_errors", 1, "Attic Relay"
    )
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Attic Relay", "Example Dimmer", "Example Keypad"]


@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_a_sensor_without_a_count_is_not_listed(digest: Digest, state: str) -> None:
    """unknown: the Controller has not reported errors since Home Assistant
    started. unavailable: the Device is disabled on the Controller."""
    await digest.errors(DIMMER, state, "Example Dimmer")
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


async def test_other_isy994_sensors_are_not_listed(digest: Digest) -> None:
    await digest.sensors.set(
        "sensor.example_keypad_button_b",
        255,
        integration="isy994",
        device="Example Keypad",
        friendly_name="Example Keypad Button B",
    )
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


async def test_another_integrations_error_sensor_is_not_listed(digest: Digest) -> None:
    await digest.errors(DIMMER, 1, "Example Dimmer", integration="example_zwave")
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


# An empty list


async def test_nothing_is_sent_when_every_device_answers(digest: Digest) -> None:
    await digest.errors(DIMMER, 0, "Example Dimmer")
    await digest.errors(KEYPAD, 0, "Example Keypad")
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


# The time of day


async def test_the_digest_is_sent_at_nine_by_default(digest: Digest) -> None:
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.start()

    await digest.run_at(NINE - timedelta(minutes=1))
    assert digest.notifications == []
    await digest.run_at(NINE)

    assert len(digest.notifications) == 1


async def test_the_time_of_day_is_an_input(digest: Digest) -> None:
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.start(time="18:30:00")

    await digest.run_at(NINE)
    assert digest.notifications == []
    await digest.run_at(START.replace(hour=18, minute=30))

    assert len(digest.notifications) == 1


# Exclusions


async def test_an_excluded_device_is_not_listed(digest: Digest) -> None:
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.errors(KEYPAD, 1, "Example Keypad")
    await digest.start(excluded_entities=[KEYPAD])

    await digest.run_at()

    assert digest.listed == ["Example Dimmer"]


# Only the Administrator


async def test_only_the_administrator_is_notified(
    digest: Digest, recipient: Phone, other_recipient: Phone
) -> None:
    notify_calls: list[str] = []
    digest.hass.bus.async_listen(
        "call_service",
        lambda event: notify_calls.append(
            f"{event.data['domain']}.{event.data['service']}"
        )
        if event.data["domain"] == "notify"
        else None,
    )
    await digest.errors(DIMMER, 1, "Example Dimmer")
    await digest.start()

    await digest.run_at()

    assert notify_calls == [digest.administrator.notify]
    assert recipient.notifications == []
    assert other_recipient.notifications == []
