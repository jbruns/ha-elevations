"""Tests create helpers by name, as a household would in the UI."""

from homeassistant.core import HomeAssistant

from testing.helpers import Helpers


async def test_helpers_are_created_by_name(hass: HomeAssistant, helpers: Helpers) -> None:
    setback = await helpers.input_boolean("Example Setback")
    mode = await helpers.input_select("Example Comfort Mode", ["Home", "Sleep", "Away"])
    since = await helpers.input_datetime("Example Last Opened")
    reason = await helpers.input_text("Example Reason")

    assert [setback, mode, since, reason] == [
        "input_boolean.example_setback",
        "input_select.example_comfort_mode",
        "input_datetime.example_last_opened",
        "input_text.example_reason",
    ]
    assert hass.states.get(setback).state == "off"
    assert hass.states.get(mode).attributes["options"] == ["Home", "Sleep", "Away"]
    assert hass.states.get(since).attributes["has_date"] is True
    assert hass.states.get(since).attributes["has_time"] is True
    assert hass.states.get(reason).state == ""


async def test_helpers_answer_their_own_actions(
    hass: HomeAssistant, helpers: Helpers
) -> None:
    first = await helpers.input_boolean("Example First")
    second = await helpers.input_boolean("Example Second")
    mode = await helpers.input_select("Example Mode", ["Home", "Away"], initial="Away")

    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": second}, blocking=True
    )
    await hass.services.async_call(
        "input_select", "select_option", {"entity_id": mode, "option": "Home"}, blocking=True
    )

    assert hass.states.get(first).state == "off"
    assert hass.states.get(second).state == "on"
    assert hass.states.get(mode).state == "Home"


async def test_a_date_only_helper(hass: HomeAssistant, helpers: Helpers) -> None:
    day = await helpers.input_datetime("Example Day", has_time=False)

    assert hass.states.get(day).attributes["has_time"] is False
