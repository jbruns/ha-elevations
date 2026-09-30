"""Tests set calendar events and Home Assistant answers calendar.get_events."""

from datetime import date, datetime, timedelta

from homeassistant.core import HomeAssistant

from testing.calendar import Calendar, Event


async def test_calendars_answer_get_events(
    hass: HomeAssistant, calendars: dict[str, Calendar]
) -> None:
    district = calendars["district"]
    await district.set_events(
        [
            Event(date(2026, 9, 1), date(2026, 9, 2), "First day of school"),
            Event(date(2026, 9, 3), date(2026, 9, 4), "No school"),
        ]
    )

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": district.entity_id,
            "start_date_time": datetime(2026, 9, 1),
            "end_date_time": datetime(2026, 9, 2) + timedelta(hours=12),
        },
        blocking=True,
        return_response=True,
    )

    events = response[district.entity_id]["events"]
    assert [event["summary"] for event in events] == ["First day of school"]
