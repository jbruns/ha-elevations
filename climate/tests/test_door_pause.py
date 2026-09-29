"""Behaviour of the Door Pause blueprint, run in a real Home Assistant core.

Two Exterior Doors start closed, and the thermostat heats. Durations are the
blueprint's defaults: 5 minutes open starts a Door Pause, and 5 minutes with
every door closed ends it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.helpers import Helpers
from testing.phones import Phone
from testing.sensors import Sensors
from testing.thermostat import Thermostat

BLUEPRINT = "climate/blueprints/automation/climate_door_pause.yaml"
PATIO = "binary_sensor.example_patio_door"
BACK = "binary_sensor.example_back_door"
DOORS = {PATIO: "Patio Door", BACK: "Back Door"}
MINUTE = timedelta(minutes=1)


@dataclass
class DoorPause:
    hass: HomeAssistant
    clock: Clock
    sensors: Sensors
    thermostat: Thermostat
    recipient: Phone
    helper: str
    saved_mode: str

    async def start(self, **inputs: Any) -> None:
        """Create the automation, with any other inputs."""
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "exterior_doors": list(DOORS),
                        "thermostat": self.thermostat.entity_id,
                        "door_pause": self.helper,
                        "saved_mode": self.saved_mode,
                        "recipients": [self.recipient.device_id],
                        **inputs,
                    },
                    alias="Door Pause",
                )
            ],
        )

    async def door(self, entity_id: str, is_open: bool, *, settle: bool = True) -> None:
        await self.sensors.set(
            entity_id,
            "on" if is_open else "off",
            device_class="door",
            friendly_name=DOORS[entity_id],
        )
        if settle:
            await self.hass.async_block_till_done()
        else:
            await self.clock.let_runs_start()

    async def wait(self, minutes: float, *, settle: bool = True) -> None:
        """Let time pass. settle=False while a run waits, such as after a restart."""
        await self.clock.advance(timedelta(minutes=minutes), step=MINUTE / 2, settle=settle)

    @property
    def paused(self) -> bool:
        return self.hass.states.get(self.helper).state == "on"

    @property
    def saved(self) -> str:
        return self.hass.states.get(self.saved_mode).state

    @property
    def mode(self) -> str:
        return self.hass.states.get(self.thermostat.entity_id).state

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return self.thermostat.calls

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return self.recipient.notifications


@pytest.fixture
async def door_pause(
    hass: HomeAssistant,
    clock: Clock,
    helpers: Helpers,
    sensors: Sensors,
    thermostat: Thermostat,
    recipient: Phone,
) -> DoorPause:
    """Everything the blueprint reads; call start() to create it."""
    helper = await helpers.input_boolean("Example Door Pause")
    saved_mode = await helpers.input_select(
        "Example Saved Mode", ["heat_cool", "heat", "cool", "off"], initial="heat_cool"
    )
    await clock.move_to(datetime(2026, 1, 15, 12))
    door_pause = DoorPause(hass, clock, sensors, thermostat, recipient, helper, saved_mode)
    for door in DOORS:
        await door_pause.door(door, False)
    await thermostat.set(hvac_mode="heat", temperature=66)
    return door_pause


# Starting a Door Pause


async def test_a_door_open_for_the_duration_pauses_the_thermostat(
    door_pause: DoorPause,
) -> None:
    await door_pause.start()

    await door_pause.door(PATIO, True)
    await door_pause.wait(5)

    assert door_pause.paused
    assert door_pause.saved == "heat"
    assert door_pause.calls == [("set_hvac_mode", {"hvac_mode": "off"})]
    assert [(n["title"], n["message"]) for n in door_pause.notifications] == [
        ("HVAC paused", "Patio Door is open, so the thermostat is off.")
    ]


async def test_the_paused_notification_names_every_open_door(door_pause: DoorPause) -> None:
    await door_pause.start()

    await door_pause.door(PATIO, True)
    await door_pause.door(BACK, True)
    await door_pause.wait(5)

    assert [n["message"] for n in door_pause.notifications] == [
        "Patio Door and Back Door are open, so the thermostat is off."
    ]


@pytest.mark.parametrize("mode", ["cool", "heat_cool"])
async def test_each_active_mode_is_saved(door_pause: DoorPause, mode: str) -> None:
    await door_pause.thermostat.set(hvac_mode=mode)
    await door_pause.start()

    await door_pause.door(PATIO, True)
    await door_pause.wait(5)

    assert door_pause.saved == mode
    assert door_pause.mode == "off"


async def test_a_door_open_briefly_changes_nothing(door_pause: DoorPause) -> None:
    await door_pause.start()

    await door_pause.door(PATIO, True)
    await door_pause.wait(4)
    await door_pause.door(PATIO, False)
    await door_pause.wait(10)

    assert not door_pause.paused
    assert door_pause.calls == []
    assert door_pause.notifications == []


async def test_an_off_thermostat_is_never_paused(door_pause: DoorPause) -> None:
    await door_pause.thermostat.set(hvac_mode="off")
    await door_pause.start()

    await door_pause.door(PATIO, True)
    await door_pause.wait(10)

    assert not door_pause.paused
    assert door_pause.saved == "heat_cool"
    assert door_pause.calls == []
    assert door_pause.notifications == []


async def test_turning_the_thermostat_on_with_a_door_long_open_pauses_it(
    door_pause: DoorPause,
) -> None:
    await door_pause.thermostat.set(hvac_mode="off")
    await door_pause.start()
    await door_pause.door(PATIO, True)
    await door_pause.wait(10)

    await door_pause.thermostat.set(hvac_mode="cool")
    await door_pause.hass.async_block_till_done()

    assert door_pause.paused
    assert door_pause.saved == "cool"
    assert door_pause.mode == "off"


async def test_the_open_duration_is_an_input(door_pause: DoorPause) -> None:
    await door_pause.start(open_for={"minutes": 2})

    await door_pause.door(PATIO, True)
    await door_pause.wait(2)

    assert door_pause.paused


# Ending a Door Pause


async def paused(door_pause: DoorPause, *doors: str, **inputs: Any) -> None:
    """Start the automation and open the doors long enough for a Door Pause."""
    await door_pause.start(**inputs)
    for door in doors or (PATIO,):
        await door_pause.door(door, True)
    await door_pause.wait(5)
    assert door_pause.paused


async def test_the_door_pause_ends_once_every_door_has_been_closed_for_the_duration(
    door_pause: DoorPause,
) -> None:
    await paused(door_pause, PATIO, BACK)

    await door_pause.door(PATIO, False)
    await door_pause.wait(10)
    assert door_pause.paused
    await door_pause.door(BACK, False)
    await door_pause.wait(4)
    assert door_pause.paused
    await door_pause.wait(1)

    assert not door_pause.paused
    assert door_pause.mode == "heat"


async def test_a_door_reopened_before_the_duration_keeps_the_door_pause(
    door_pause: DoorPause,
) -> None:
    await paused(door_pause)

    await door_pause.door(PATIO, False)
    await door_pause.wait(3)
    await door_pause.door(PATIO, True)
    await door_pause.wait(3)
    await door_pause.door(PATIO, False)
    await door_pause.wait(3)

    assert door_pause.paused
    assert door_pause.mode == "off"


@pytest.mark.parametrize("mode", ["heat", "cool", "heat_cool"])
async def test_the_previous_mode_is_restored(door_pause: DoorPause, mode: str) -> None:
    await door_pause.thermostat.set(hvac_mode=mode)
    await paused(door_pause)

    await door_pause.door(PATIO, False)
    await door_pause.wait(5)

    assert door_pause.calls[-1] == ("set_hvac_mode", {"hvac_mode": mode})
    assert door_pause.mode == mode


async def test_the_resumed_notification_silently_replaces_the_paused_one(
    door_pause: DoorPause,
) -> None:
    await door_pause.thermostat.set(hvac_mode="heat_cool")
    await paused(door_pause)

    await door_pause.door(PATIO, False)
    await door_pause.wait(5)

    paused_notification, resumed = door_pause.notifications
    assert (resumed["title"], resumed["message"]) == (
        "HVAC resumed",
        "Every door is closed, so the thermostat is back on Heat/Cool.",
    )
    assert resumed["data"]["tag"] == paused_notification["data"]["tag"]
    assert resumed["data"]["push"] == {"sound": "none", "interruption-level": "passive"}
    assert paused_notification["data"]["push"] == {"sound": "default"}


async def test_a_second_door_opening_during_a_door_pause_changes_nothing(
    door_pause: DoorPause,
) -> None:
    await paused(door_pause)
    calls, notifications = list(door_pause.calls), list(door_pause.notifications)

    await door_pause.door(BACK, True)
    await door_pause.wait(10)

    assert door_pause.paused
    assert door_pause.saved == "heat"
    assert door_pause.calls == calls
    assert door_pause.notifications == notifications


async def test_a_thermostat_turned_on_during_a_door_pause_keeps_its_mode(
    door_pause: DoorPause,
) -> None:
    await paused(door_pause)
    await door_pause.thermostat.set(hvac_mode="cool")

    await door_pause.door(PATIO, False)
    await door_pause.wait(5)

    assert not door_pause.paused
    assert door_pause.mode == "cool"
    assert door_pause.notifications[-1]["message"] == (
        "Every door is closed, so the thermostat is back on Cool."
    )


async def test_the_door_pause_waits_for_the_thermostat_to_be_available(
    door_pause: DoorPause,
) -> None:
    await paused(door_pause)
    await door_pause.thermostat.set(available=False)

    await door_pause.door(PATIO, False)
    await door_pause.wait(10)
    assert door_pause.paused
    await door_pause.thermostat.set(available=True)
    await door_pause.hass.async_block_till_done()

    assert not door_pause.paused
    assert door_pause.mode == "heat"


async def test_the_closed_duration_is_an_input(door_pause: DoorPause) -> None:
    await paused(door_pause, closed_for={"minutes": 10})

    await door_pause.door(PATIO, False)
    await door_pause.wait(9)
    assert door_pause.paused
    await door_pause.wait(1)

    assert not door_pause.paused


async def test_a_door_sensor_that_drops_out_counts_as_closed(door_pause: DoorPause) -> None:
    await paused(door_pause)

    await door_pause.sensors.set(PATIO, "unavailable")
    await door_pause.wait(5)

    assert not door_pause.paused
    assert door_pause.mode == "heat"


async def test_a_door_sensor_that_drops_out_then_reads_closed_ends_the_door_pause(
    door_pause: DoorPause,
) -> None:
    await paused(door_pause)

    await door_pause.sensors.set(PATIO, "unavailable")
    await door_pause.wait(2)
    await door_pause.door(PATIO, False)
    await door_pause.wait(4)
    assert door_pause.paused
    await door_pause.wait(1)

    assert not door_pause.paused


# Restarting Home Assistant


async def restart(door_pause: DoorPause) -> None:
    """Home Assistant starts with the doors, helpers and thermostat as they are."""
    await door_pause.start()
    door_pause.hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)


async def pause_before_restart(door_pause: DoorPause, saved: str = "cool") -> None:
    """A Door Pause that was on when Home Assistant stopped."""
    await door_pause.hass.services.async_call(
        "input_select",
        "select_option",
        {"entity_id": door_pause.saved_mode, "option": saved},
        blocking=True,
    )
    await door_pause.hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": door_pause.helper}, blocking=True
    )
    await door_pause.thermostat.set(hvac_mode="off")


async def test_a_door_pause_ends_after_a_restart_with_every_door_closed(
    door_pause: DoorPause,
) -> None:
    await pause_before_restart(door_pause)

    await restart(door_pause)
    await door_pause.wait(4, settle=False)
    assert door_pause.paused
    await door_pause.wait(1, settle=False)

    assert not door_pause.paused
    assert door_pause.mode == "cool"


async def test_a_door_open_across_a_restart_still_pauses(door_pause: DoorPause) -> None:
    await door_pause.door(PATIO, True)

    await restart(door_pause)
    await door_pause.wait(4, settle=False)
    assert not door_pause.paused
    await door_pause.wait(1, settle=False)

    assert door_pause.paused
    assert door_pause.mode == "off"


async def test_a_door_opened_soon_after_a_restart_keeps_the_door_pause(
    door_pause: DoorPause,
) -> None:
    await pause_before_restart(door_pause)
    await restart(door_pause)
    await door_pause.wait(2, settle=False)

    await door_pause.door(BACK, True)
    await door_pause.wait(3)
    await door_pause.door(BACK, False)
    await door_pause.wait(4)
    assert door_pause.paused
    await door_pause.wait(1)

    assert not door_pause.paused
    assert door_pause.mode == "cool"


async def test_a_door_sensor_settling_after_a_restart_still_ends_the_door_pause(
    door_pause: DoorPause,
) -> None:
    await pause_before_restart(door_pause)
    await door_pause.sensors.set(PATIO, "unknown")
    await restart(door_pause)
    await door_pause.wait(1, settle=False)

    await door_pause.door(PATIO, False, settle=False)
    await door_pause.wait(3, settle=False)
    assert door_pause.paused
    await door_pause.wait(1, settle=False)

    assert not door_pause.paused
    assert door_pause.mode == "cool"
