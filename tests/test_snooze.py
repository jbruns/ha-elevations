"""Snooze: a Recipient pauses Alert Notifications to their own phone."""

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from conftest import (
    DOORBELL_ENTITY,
    REVIEW_ID,
    SNOOZE_HELPERS,
    Frigate,
    Recipient,
    review,
    settle,
    tracked_object,
)

PERSON = "1790000000.100000-person"
NEXT_REVIEW_ID = "1790000040.000000-rev2"
NEXT_PERSON = "1790000040.100000-person"


@pytest.fixture
def blueprint_input(recipient: Recipient, other_recipient: Recipient) -> dict[str, Any]:
    return {"recipients": [recipient.device_id, other_recipient.device_id]}


@pytest.fixture
def doorbell_input(recipient: Recipient, other_recipient: Recipient) -> dict[str, Any]:
    return {"recipients": [recipient.device_id, other_recipient.device_id]}


@pytest.fixture
def automations(
    alert_automation: dict[str, Any], doorbell_automation: dict[str, Any]
) -> list[dict[str, Any]]:
    return [alert_automation, doorbell_automation]


def action_titled(notification: dict, title: str) -> dict:
    [action] = [a for a in notification["data"]["actions"] if a["title"] == title]
    return action


async def tap(hass: HomeAssistant, notification: dict, title: str) -> None:
    """Taps an action button, as the iOS app reports it to Home Assistant."""
    action = action_titled(notification, title)
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": action["action"], "action_data": notification["data"]["action_data"]},
    )
    await settle()


def hh_mm(minutes_from_now: int) -> str:
    return dt_util.as_local(dt_util.now() + timedelta(minutes=minutes_from_now)).strftime(
        "%H:%M"
    )


def is_silent(notification: dict) -> bool:
    return notification["data"]["push"]["sound"] == "none"


async def alert(frigate: Frigate, review_id: str = REVIEW_ID, person: str = PERSON) -> None:
    await frigate.publish_review(
        review("new", [person], ["person"], ["entry_breezeway"], review_id=review_id)
    )


async def alert_and_end(frigate: Frigate, review_id: str, person: str) -> None:
    await alert(frigate, review_id, person)
    await frigate.publish_review(
        review("end", [person], ["person"], ["entry_breezeway"], review_id=review_id)
    )


def for_review(recipient: Recipient, review_id: str) -> list[dict]:
    return [n for n in recipient.notifications if n["data"]["tag"] == review_id]


async def snoozed_for_30_min(hass: HomeAssistant, frigate: Frigate, recipient: Recipient) -> None:
    await alert_and_end(frigate, REVIEW_ID, PERSON)
    await tap(hass, recipient.notifications[0], "Snooze 30 min")


async def test_alert_notification_offers_snooze_actions(
    frigate: Frigate, recipient: Recipient
) -> None:
    await alert(frigate)

    [notification] = recipient.notifications
    titles = [a["title"] for a in notification["data"]["actions"]]
    assert "Snooze 30 min" in titles
    assert "Snooze 2 h" in titles


@pytest.mark.parametrize(("title", "minutes"), [("Snooze 30 min", 30), ("Snooze 2 h", 120)])
async def test_snoozing_updates_the_tapped_notification_silently_in_place(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient, title: str, minutes: int
) -> None:
    await alert(frigate)
    [notification] = recipient.notifications

    await tap(hass, notification, title)

    first, confirmation = recipient.notifications
    assert confirmation["message"] == f"Snoozed until {hh_mm(minutes)}"
    assert confirmation["data"]["tag"] == first["data"]["tag"]
    assert confirmation["data"]["push"] == {"sound": "none", "interruption-level": "passive"}
    assert not any(a["title"].startswith("Snooze") for a in confirmation["data"]["actions"])


async def test_snoozed_recipient_gets_no_alert_notifications_the_other_is_unaffected(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient, other_recipient: Recipient
) -> None:
    await snoozed_for_30_min(hass, frigate, recipient)
    before = len(recipient.notifications)

    await alert_and_end(frigate, NEXT_REVIEW_ID, NEXT_PERSON)

    assert len(recipient.notifications) == before
    assert for_review(other_recipient, NEXT_REVIEW_ID)


async def test_snooze_stops_updates_to_the_alert_already_under_way(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient, other_recipient: Recipient
) -> None:
    await alert(frigate)
    await tap(hass, recipient.notifications[0], "Snooze 30 min")
    confirmation = recipient.notifications[-1]

    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["entry_breezeway"], snapshot_time=1790000009.0
        )
    )
    await frigate.publish_review(review("end", [PERSON], ["person"], ["entry_breezeway"]))

    assert recipient.notifications[-1] == confirmation
    assert len(other_recipient.notifications) > 1


async def test_alerts_resume_when_the_snooze_expires_and_nothing_is_sent(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    frigate: Frigate,
    recipient: Recipient,
) -> None:
    await snoozed_for_30_min(hass, frigate, recipient)
    before = len(recipient.notifications)

    freezer.tick(timedelta(minutes=30, seconds=1))
    async_fire_time_changed(hass)
    await settle()
    assert len(recipient.notifications) == before

    await alert(frigate, NEXT_REVIEW_ID, NEXT_PERSON)
    assert for_review(recipient, NEXT_REVIEW_ID)


async def test_snooze_lasts_its_full_duration(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    frigate: Frigate,
    recipient: Recipient,
) -> None:
    await snoozed_for_30_min(hass, frigate, recipient)

    freezer.tick(timedelta(minutes=29))
    await alert(frigate, NEXT_REVIEW_ID, NEXT_PERSON)

    assert for_review(recipient, NEXT_REVIEW_ID) == []


async def test_resuming_ends_the_snooze_at_once(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await snoozed_for_30_min(hass, frigate, recipient)

    await hass.services.async_call(
        "input_datetime", "set_datetime", {"timestamp": 0}, target={"entity_id": SNOOZE_HELPERS[0]}
    )
    await settle()
    await alert(frigate, NEXT_REVIEW_ID, NEXT_PERSON)

    assert for_review(recipient, NEXT_REVIEW_ID)


async def test_snooze_never_holds_back_a_doorbell_press(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await snoozed_for_30_min(hass, frigate, recipient)
    before = len(recipient.notifications)

    hass.states.async_set(DOORBELL_ENTITY, "on")
    await settle()

    [press] = recipient.notifications[before:]
    assert press["message"] == "Someone pressed the doorbell"
    assert not is_silent(press)


async def test_snoozing_on_one_phone_never_snoozes_the_other(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient, other_recipient: Recipient
) -> None:
    await alert(frigate)
    await tap(hass, recipient.notifications[0], "Snooze 2 h")

    assert len(other_recipient.notifications) == 1
    assert hass.states.get(SNOOZE_HELPERS[1]).attributes["timestamp"] <= dt_util.now().timestamp()


async def test_phone_without_a_snooze_helper_is_not_offered_snooze(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    hass.states.async_remove(SNOOZE_HELPERS[0])

    await alert(frigate)

    [notification] = recipient.notifications
    assert [a["title"] for a in notification["data"]["actions"]] == ["Live"]
