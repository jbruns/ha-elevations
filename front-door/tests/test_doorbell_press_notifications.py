"""Behaviour of the Doorbell Press Notifications blueprint, as seen by a Recipient."""

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from conftest import (
    CAMERA_ENTITY,
    DOORBELL_ENTITY,
    REVIEW_ID,
    Frigate,
    Recipient,
    review,
    settle,
)

PERSON = "1790000000.100000-person"


@pytest.fixture
def automations(
    alert_automation: dict[str, Any], doorbell_automation: dict[str, Any]
) -> list[dict[str, Any]]:
    return [alert_automation, doorbell_automation]


async def press(hass: HomeAssistant) -> None:
    hass.states.async_set(DOORBELL_ENTITY, "on")
    await settle()
    hass.states.async_set(DOORBELL_ENTITY, "off")
    await settle()


def talk_action(notification: dict) -> dict:
    [action] = [a for a in notification["data"]["actions"] if a["title"] == "Talk"]
    return action


def is_silent(notification: dict) -> bool:
    return notification["data"]["push"]["sound"] == "none"


async def test_press_delivers_a_time_sensitive_notification_with_sound(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await press(hass)

    [notification] = recipient.notifications
    assert notification["title"] == "Front Door"
    assert notification["message"] == "Someone pressed the doorbell"
    assert notification["data"]["push"] == {
        "sound": "default",
        "interruption-level": "time-sensitive",
    }


async def test_long_press_shows_the_cameras_live_stream(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await press(hass)

    [notification] = recipient.notifications
    # The iOS app streams this camera in the expanded Notification.
    assert notification["data"]["entity_id"] == CAMERA_ENTITY


async def test_talk_and_tap_open_the_cameras_view_in_the_app(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await press(hass)

    [notification] = recipient.notifications
    action = talk_action(notification)
    assert action["action"] == "URI"
    assert action["uri"] == "/our-home/cameras"
    assert notification["data"]["url"] == "/our-home/cameras"


@pytest.mark.parametrize(
    "doorbell_input", [{"cameras_view": " /our-dashboard/cams/ "}]
)
async def test_cameras_view_is_an_input(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await press(hass)

    [notification] = recipient.notifications
    assert talk_action(notification)["uri"] == "/our-dashboard/cams"
    assert notification["data"]["url"] == "/our-dashboard/cams"


async def test_doorbell_coming_back_online_sends_nothing(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    hass.states.async_set(DOORBELL_ENTITY, "unavailable")
    await settle()
    hass.states.async_set(DOORBELL_ENTITY, "off")
    await settle()

    assert recipient.notifications == []


async def test_each_press_gets_its_own_notification(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await press(hass)
    await press(hass)

    first, second = recipient.notifications
    assert first["data"]["tag"] != second["data"]["tag"]


async def test_press_during_an_alert_is_a_second_separate_notification(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )

    await press(hass)

    alert, doorbell = recipient.notifications
    assert alert["data"]["tag"] == REVIEW_ID
    assert doorbell["data"]["tag"] != alert["data"]["tag"]
    # A separate group keeps iOS from stacking it under the Alert.
    assert doorbell["data"]["group"] != alert["data"]["group"]
    assert not is_silent(doorbell)


async def test_alert_updates_never_replace_the_doorbell_notification(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await press(hass)
    await frigate.publish_review(
        review("end", [PERSON], ["person"], ["entry_breezeway"])
    )

    doorbell_tag = recipient.notifications[1]["data"]["tag"]
    later = recipient.notifications[2:]
    assert later
    assert all(n["data"]["tag"] == REVIEW_ID for n in later)
    assert doorbell_tag != REVIEW_ID


async def test_press_right_after_an_alert_still_makes_a_sound(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    frigate: Frigate,
    recipient: Recipient,
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    freezer.tick(timedelta(seconds=5))

    await press(hass)

    doorbell = recipient.notifications[-1]
    assert not is_silent(doorbell)
