"""Behaviour of the Battery Digest blueprint, run in a real Home Assistant core.

Each test starts at 08:00 and, unless it says otherwise, the Digest is due at
the blueprint's default time of 09:00.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.phones import Phone
from testing.sensors import Sensors

BLUEPRINT = "infrastructure/blueprints/automation/infrastructure_battery_digest.yaml"
START = datetime(2026, 6, 1, 8, 0)
NINE = START.replace(hour=9)


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
                    alias="Battery Digest",
                )
            ],
        )

    async def battery(
        self, entity_id: str, level: Any, name: str, integration: str = "example_zwave"
    ) -> None:
        """A battery level sensor."""
        await self.sensors.battery(
            entity_id, level, integration=integration, friendly_name=name
        )

    async def binary(
        self,
        entity_id: str,
        state: str,
        name: str,
        device_class: str | None = "battery",
        integration: str = "example_zigbee",
    ) -> None:
        """A binary sensor, by default one that is on when its battery is low."""
        await self.sensors.set(
            entity_id,
            state,
            device_class=device_class,
            friendly_name=name,
            integration=integration,
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


# Low numeric sensors


async def test_a_battery_below_the_threshold_is_listed(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.start()

    await digest.run_at()

    assert digest.notifications == [("Battery Digest", "Example Lock Battery (15%)")]


async def test_a_battery_at_the_threshold_is_not_low(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 25, "Example Lock Battery")
    await digest.battery("sensor.example_door_battery", 24.5, "Example Door Battery")
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Example Door Battery (24.5%)"]


async def test_the_threshold_is_an_input(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 35, "Example Lock Battery")
    await digest.start(threshold=40)

    await digest.run_at()

    assert digest.listed == ["Example Lock Battery (35%)"]


async def test_the_list_is_sorted(digest: Digest) -> None:
    await digest.battery("sensor.example_b_battery", 10, "Basement Sensor")
    await digest.battery("sensor.example_c_battery", "20.0", "Closet Sensor")
    await digest.battery("sensor.example_a_battery", 5, "Attic Sensor")
    await digest.start()

    await digest.run_at()

    assert digest.listed == [
        "Attic Sensor (5%)",
        "Basement Sensor (10%)",
        "Closet Sensor (20%)",
    ]


async def test_a_sensor_without_the_battery_device_class_is_not_listed(
    digest: Digest,
) -> None:
    await digest.sensors.set(
        "sensor.example_humidity",
        10,
        device_class="humidity",
        unit="%",
        friendly_name="Example Humidity",
    )
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


# The time of day


async def test_the_digest_is_sent_at_nine_by_default(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.start()

    await digest.run_at(NINE - timedelta(minutes=1))
    assert digest.notifications == []
    await digest.run_at(NINE)

    assert len(digest.notifications) == 1


async def test_the_time_of_day_is_an_input(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.start(time="18:30:00")

    await digest.run_at(NINE)
    assert digest.notifications == []
    await digest.run_at(START.replace(hour=18, minute=30))

    assert len(digest.notifications) == 1


# An empty list


async def test_nothing_is_sent_when_no_battery_needs_attention(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 80, "Example Lock Battery")
    await digest.binary("binary_sensor.example_leak_battery_low", "off", "Leak Battery")
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


# Battery binary sensors


async def test_a_battery_binary_sensor_that_is_on_is_listed(digest: Digest) -> None:
    await digest.binary("binary_sensor.example_leak_battery_low", "on", "Leak Battery")
    await digest.binary("binary_sensor.example_motion_battery_low", "off", "Motion Battery")
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Leak Battery (low)"]


# Included entities

SMOKE_LOW = "binary_sensor.example_smoke_bridge_low_battery"


async def test_an_included_entity_that_is_on_is_listed(digest: Digest) -> None:
    await digest.binary(SMOKE_LOW, "on", "Smoke Alarm Low Battery", device_class=None)
    await digest.start(included_entities=[SMOKE_LOW])

    await digest.run_at()

    assert digest.listed == ["Smoke Alarm Low Battery (low)"]


async def test_an_included_entity_that_is_off_is_not_listed(digest: Digest) -> None:
    await digest.binary(SMOKE_LOW, "off", "Smoke Alarm Low Battery", device_class=None)
    await digest.start(included_entities=[SMOKE_LOW])

    await digest.run_at()

    assert digest.notifications == []


async def test_a_binary_sensor_that_is_not_included_is_not_listed(digest: Digest) -> None:
    await digest.binary(SMOKE_LOW, "on", "Smoke Alarm Low Battery", device_class=None)
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


async def test_an_included_battery_binary_sensor_is_listed_once(digest: Digest) -> None:
    await digest.binary("binary_sensor.example_leak_battery_low", "on", "Leak Battery")
    await digest.start(included_entities=["binary_sensor.example_leak_battery_low"])

    await digest.run_at()

    assert digest.listed == ["Leak Battery (low)"]


# Batteries that have stopped reporting


async def gone_since(
    digest: Digest, hours: float, add: Callable[[], Awaitable[None]] | None = None
) -> None:
    """add() an entity, by default the lock's battery unavailable, hours before 09:00."""
    await digest.clock.move_to(NINE - timedelta(hours=hours))
    if add is None:
        await digest.battery("sensor.example_lock_battery", "unavailable", "Example Lock Battery")
    else:
        await add()
    await digest.clock.move_to(START)


async def test_a_battery_unavailable_for_over_a_day_is_listed(digest: Digest) -> None:
    await gone_since(digest, 25)
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Example Lock Battery (unavailable)"]


async def test_a_battery_unknown_for_over_a_day_is_listed(digest: Digest) -> None:
    await gone_since(
        digest,
        25,
        lambda: digest.battery("sensor.example_lock_battery", "unknown", "Example Lock Battery"),
    )
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Example Lock Battery (unknown)"]


async def test_a_battery_briefly_unavailable_is_not_listed(digest: Digest) -> None:
    await gone_since(digest, 23)
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


async def test_unavailable_after_is_an_input(digest: Digest) -> None:
    await gone_since(digest, 3)
    await digest.start(unavailable_after={"hours": 2})

    await digest.run_at()

    assert digest.listed == ["Example Lock Battery (unavailable)"]


async def test_a_battery_binary_sensor_unavailable_for_over_a_day_is_listed(
    digest: Digest,
) -> None:
    await gone_since(
        digest,
        25,
        lambda: digest.binary(
            "binary_sensor.example_leak_battery_low", "unavailable", "Leak Battery"
        ),
    )
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Leak Battery (unavailable)"]


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_an_included_entity_that_is_not_reporting_is_not_listed(
    digest: Digest, state: str
) -> None:
    """Some low-battery sensors only get a state when the device sends one."""
    await gone_since(
        digest,
        25,
        lambda: digest.binary(SMOKE_LOW, state, "Smoke Alarm Low Battery", device_class=None),
    )
    await digest.start(included_entities=[SMOKE_LOW])

    await digest.run_at()

    assert digest.notifications == []


async def test_other_unavailable_entities_are_not_listed(digest: Digest) -> None:
    await gone_since(
        digest,
        25,
        lambda: digest.sensors.set(
            "sensor.example_humidity",
            "unavailable",
            device_class="humidity",
            friendly_name="Example Humidity",
            integration="example_zwave",
        ),
    )
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


# Exclusions


@pytest.mark.parametrize("integration", ["mobile_app", "nut"])
async def test_phones_and_upses_are_excluded_by_default(
    digest: Digest, integration: str
) -> None:
    await digest.battery("sensor.example_phone_battery", 10, "Phone", integration)
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.start()

    await digest.run_at()

    assert digest.listed == ["Example Lock Battery (15%)"]


async def test_the_excluded_integrations_are_an_input(digest: Digest) -> None:
    await digest.battery("sensor.example_phone_battery", 10, "Phone", "mobile_app")
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.binary("binary_sensor.example_leak_battery_low", "on", "Leak Battery")
    await digest.start(excluded_integrations=["example_zwave", "example_zigbee"])

    await digest.run_at()

    assert digest.listed == ["Phone (10%)"]


async def test_an_excluded_integrations_unavailable_battery_is_not_listed(
    digest: Digest,
) -> None:
    await gone_since(
        digest,
        25,
        lambda: digest.battery(
            "sensor.example_phone_battery", "unavailable", "Phone", "mobile_app"
        ),
    )
    await digest.start()

    await digest.run_at()

    assert digest.notifications == []


async def test_an_excluded_entity_is_not_listed(digest: Digest) -> None:
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.battery("sensor.example_door_battery", 10, "Example Door Battery")
    await digest.binary("binary_sensor.example_leak_battery_low", "on", "Leak Battery")
    await digest.start(
        excluded_entities=[
            "sensor.example_lock_battery",
            "binary_sensor.example_leak_battery_low",
        ]
    )

    await digest.run_at()

    assert digest.listed == ["Example Door Battery (10%)"]


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
    await digest.battery("sensor.example_lock_battery", 15, "Example Lock Battery")
    await digest.start()

    await digest.run_at()

    assert notify_calls == [digest.administrator.notify]
    assert recipient.notifications == []
    assert other_recipient.notifications == []
