"""Calendar entities whose events tests set directly."""

from dataclasses import dataclass
from datetime import date, datetime, time

import pytest
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import setup_test_component_platform


@dataclass(frozen=True)
class Event:
    """One calendar event; dates are all-day, datetimes are timed."""

    start: date | datetime
    end: date | datetime
    summary: str

    def as_calendar_event(self) -> CalendarEvent:
        return CalendarEvent(start=self.start, end=self.end, summary=self.summary)


class Calendar(CalendarEntity):
    _attr_should_poll = False

    def __init__(self, entity_id: str, name: str) -> None:
        self.entity_id = entity_id
        self._attr_name = name
        self._events: list[Event] = []

    @property
    def event(self) -> CalendarEvent | None:
        if not self._events:
            return None
        return min(
            self._events, key=lambda event: _as_datetime(event.start)
        ).as_calendar_event()

    async def set_events(self, events: list[Event]) -> None:
        self._events = list(events)
        self.async_write_ha_state()

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            event.as_calendar_event()
            for event in self._events
            if _as_datetime(event.start) < end_date
            and _as_datetime(event.end) > start_date
        ]


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        when = value
    else:
        when = datetime.combine(value, time.min)
    if when.tzinfo is None:
        return dt_util.as_local(when.replace(tzinfo=dt_util.UTC))
    return when


@pytest.fixture
async def calendars(hass: HomeAssistant) -> dict[str, Calendar]:
    district = Calendar("calendar.example_district", "Example District")
    closures = Calendar("calendar.example_closures", "Example Closures")
    setup_test_component_platform(hass, "calendar", [district, closures])
    assert await async_setup_component(
        hass, "calendar", {"calendar": {"platform": "test"}}
    )
    await hass.async_block_till_done()
    return {"district": district, "closures": closures}
