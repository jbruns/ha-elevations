"""Tests freeze and advance time to cover for: durations, time triggers and
time patterns."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations
from testing.clock import Clock
from testing.phones import Phone


def notify_on(recipient: Phone, trigger: dict) -> dict:
    return {
        "alias": "Notify",
        "triggers": [trigger],
        "actions": [{"action": recipient.notify, "data": {"message": "fired"}}],
    }


async def test_advancing_past_a_for_duration_fires_the_trigger(
    hass: HomeAssistant, clock: Clock, recipient: Phone
) -> None:
    hass.states.async_set("binary_sensor.example_door", "off")
    await async_setup_automations(
        hass,
        [
            notify_on(
                recipient,
                {
                    "trigger": "state",
                    "entity_id": "binary_sensor.example_door",
                    "to": "on",
                    "for": {"minutes": 5},
                },
            )
        ],
    )
    hass.states.async_set("binary_sensor.example_door", "on")
    await hass.async_block_till_done()

    await clock.advance(timedelta(minutes=4, seconds=59))
    assert recipient.notifications == []

    await clock.advance(timedelta(seconds=1))
    assert len(recipient.notifications) == 1


async def test_moving_to_a_local_time_fires_a_time_trigger(
    hass: HomeAssistant, clock: Clock, recipient: Phone
) -> None:
    await clock.move_to(datetime(2026, 6, 1, 6, 59))
    await async_setup_automations(
        hass, [notify_on(recipient, {"trigger": "time", "at": "07:00:00"})]
    )

    await clock.move_to(datetime(2026, 6, 1, 7, 0))

    assert len(recipient.notifications) == 1
    assert clock.now() == datetime(2026, 6, 1, 7, 0, tzinfo=clock.now().tzinfo)


async def test_advancing_in_steps_fires_a_time_pattern_each_time(
    hass: HomeAssistant, clock: Clock, recipient: Phone
) -> None:
    await clock.move_to(datetime(2026, 6, 1, 12, 0, 30))
    await async_setup_automations(
        hass, [notify_on(recipient, {"trigger": "time_pattern", "minutes": "/15"})]
    )

    await clock.advance(timedelta(hours=1), step=timedelta(minutes=1))

    assert len(recipient.notifications) == 4


async def test_advancing_without_settling_reaches_a_waiting_run_timeout(
    hass: HomeAssistant, clock: Clock, recipient: Phone
) -> None:
    """A run waiting in a wait_template or delay never settles, so the clock
    moves on without waiting for it."""
    await async_setup_automations(
        hass,
        [
            {
                "alias": "Notify after a wait",
                "triggers": [{"trigger": "event", "event_type": "example_start"}],
                "actions": [
                    {"wait_template": "{{ false }}", "timeout": {"minutes": 5}},
                    {"action": recipient.notify, "data": {"message": "waited"}},
                ],
            }
        ],
    )
    hass.bus.async_fire("example_start")

    await clock.advance(timedelta(minutes=4), step=timedelta(minutes=1), settle=False)
    assert recipient.notifications == []
    await clock.advance(timedelta(minutes=1), settle=False)

    assert [n["message"] for n in recipient.notifications] == ["waited"]
