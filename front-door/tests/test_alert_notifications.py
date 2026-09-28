"""Behaviour of the Alert Notifications blueprint, as seen by a Recipient."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from conftest import (
    BASE_URL,
    REVIEW_CARD_ID,
    REVIEW_ID,
    Frigate,
    Recipient,
    review,
    settle,
    tracked_object,
)

PERSON = "1790000000.100000-person"
CAR = "1789990000.000000-car"
NEXT_REVIEW_ID = "1790000040.000000-rev2"
NEXT_PERSON = "1790000040.100000-person"


def image_url(notification: dict) -> str:
    return notification["data"]["attachment"]["url"]


def image_object(notification: dict) -> str:
    return urlparse(image_url(notification)).path.split("/")[-2]


def is_silent(notification: dict) -> bool:
    return notification["data"]["push"]["sound"] == "none"


async def test_person_walking_up_gets_one_notification_showing_the_person(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )

    [notification] = recipient.notifications
    assert notification["title"] == "Front Door"
    assert notification["message"] == "Person in Entry Breezeway"
    url = urlparse(image_url(notification))
    assert f"{url.scheme}://{url.netloc}" == BASE_URL
    assert url.path == f"/api/frigate/notifications/{PERSON}/snapshot.jpg"
    query = parse_qs(url.query)
    assert query["bbox"] == ["1"]
    assert query["crop"] == ["1"]
    assert notification["data"]["tag"] == REVIEW_ID
    assert not is_silent(notification)


async def test_reviews_that_are_not_alerts_on_the_camera_get_no_notification(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["parking"], severity="detection")
    )
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["driveway"], camera="back_yard")
    )

    assert recipient.notifications == []


async def test_review_upgraded_to_alert_gets_a_notification(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["parking"], severity="detection")
    )
    await frigate.publish_review(
        review(
            "update",
            [PERSON],
            ["person"],
            ["parking", "driveway"],
            before_severity="detection",
        )
    )

    [notification] = recipient.notifications
    assert notification["message"] == "Person in Driveway"
    assert not is_silent(notification)


async def test_better_snapshot_replaces_the_notification_silently_with_a_new_url(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["entry_breezeway"], snapshot_time=1790000005.0
        )
    )
    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["entry_breezeway"], snapshot_time=1790000009.0
        )
    )

    first, *updates = recipient.notifications
    assert len(updates) >= 1
    assert all(is_silent(u) for u in updates)
    assert all(u["data"]["tag"] == first["data"]["tag"] for u in updates)
    assert all(image_object(u) == PERSON for u in updates)
    urls = [image_url(n) for n in recipient.notifications]
    assert len(set(urls)) == len(urls)


async def test_unchanged_tracked_object_sends_no_update(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    snapshot = tracked_object(
        PERSON, "person", current_zones=["entry_breezeway"], snapshot_time=1790000005.0
    )
    await frigate.publish_event(snapshot)
    count = len(recipient.notifications)
    await frigate.publish_event(snapshot)

    assert len(recipient.notifications) == count


async def test_review_end_silently_shows_final_snapshot_with_unchanged_text(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["driveway"], snapshot_time=1790000005.0
        )
    )
    before_end = recipient.notifications[-1]
    count = len(recipient.notifications)
    await frigate.publish_review(
        review("end", [PERSON], ["person"], ["entry_breezeway", "driveway"])
    )

    assert len(recipient.notifications) == count + 1
    final = recipient.notifications[-1]
    assert is_silent(final)
    assert final["message"] == before_end["message"]
    assert image_object(final) == PERSON
    assert image_url(final) != image_url(before_end)


async def test_run_stops_after_the_review_ends(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await frigate.publish_review(
        review("end", [PERSON], ["person"], ["entry_breezeway"])
    )
    count = len(recipient.notifications)
    await frigate.publish_event(
        tracked_object(PERSON, "person", current_zones=["driveway"], snapshot_time=1790000050.0)
    )

    assert len(recipient.notifications) == count


async def test_car_arriving_is_named_as_arriving(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(review("new", [CAR], ["car"], ["driveway"]))

    [notification] = recipient.notifications
    assert notification["message"] == "Car arriving in Driveway"
    assert image_object(notification) == CAR


async def test_person_joining_the_review_updates_the_text_silently(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(review("new", [CAR], ["car"], ["driveway"]))
    await frigate.publish_event(
        tracked_object(CAR, "car", current_zones=["driveway"], snapshot_time=1790000002.0)
    )
    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["entry_breezeway"], snapshot_time=1790000003.0
        )
    )
    await frigate.publish_review(
        review("update", [CAR, PERSON], ["car", "person"], ["driveway", "entry_breezeway"])
    )

    update = recipient.notifications[-1]
    assert is_silent(update)
    assert update["message"] == "Car arriving in Driveway and Person in Entry Breezeway"


async def test_parked_car_already_in_the_review_is_never_named_or_shown(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [CAR, PERSON], ["car", "person"], ["driveway"])
    )
    await frigate.publish_event(
        tracked_object(CAR, "car", stationary=True, current_zones=["driveway"])
    )
    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["driveway"], snapshot_time=1790000004.0
        )
    )
    await frigate.publish_review(
        review("end", [CAR, PERSON], ["car", "person"], ["driveway"])
    )

    assert recipient.notifications
    assert not is_silent(recipient.notifications[0])
    for notification in recipient.notifications:
        assert notification["message"] == "Person in Driveway"
        assert image_object(notification) == PERSON


async def test_objects_that_never_report_moving_are_never_named_or_shown(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [CAR, PERSON], ["car", "person"], ["driveway"])
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await settle()
    assert recipient.notifications == []

    await frigate.publish_event(
        tracked_object(PERSON, "person", current_zones=["driveway"])
    )

    [notification] = recipient.notifications
    assert not is_silent(notification)
    assert notification["message"] == "Person in Driveway"
    assert image_object(notification) == PERSON


async def test_car_that_parks_is_dropped_when_a_person_joins(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(review("new", [CAR], ["car"], ["driveway"]))
    await frigate.publish_event(
        tracked_object(CAR, "car", stationary=True, current_zones=["driveway"])
    )
    parked = recipient.notifications[-1]
    await frigate.publish_event(
        tracked_object(PERSON, "person", current_zones=["driveway"], snapshot_time=1790000020.0)
    )
    await frigate.publish_review(
        review("update", [CAR, PERSON], ["car", "person"], ["driveway"])
    )

    assert parked["message"] == "Car arriving in Driveway"
    update = recipient.notifications[-1]
    assert update["message"] == "Person in Driveway"
    assert image_object(update) == PERSON


async def test_recognized_face_from_the_review_replaces_the_label(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review(
            "new",
            [PERSON],
            ["person-verified"],
            ["entry_breezeway"],
            sub_labels=["Test Face"],
        )
    )

    [notification] = recipient.notifications
    assert notification["message"] == "Test Face in Entry Breezeway"


async def test_face_recognized_later_updates_the_text_silently(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await frigate.publish_event(
        tracked_object(
            PERSON, "person", current_zones=["entry_breezeway"], sub_label="Test Face"
        )
    )

    update = recipient.notifications[-1]
    assert is_silent(update)
    assert update["message"] == "Test Face in Entry Breezeway"


async def test_recognized_plate_replaces_the_label_for_an_arriving_car(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(review("new", [CAR], ["car"], ["driveway"]))
    await frigate.publish_event(
        tracked_object(CAR, "car", current_zones=["driveway"], sub_label="Test Plate")
    )

    assert recipient.notifications[-1]["message"] == "Test Plate arriving in Driveway"


async def test_review_that_goes_quiet_still_gets_its_final_update(
    hass: HomeAssistant, frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    [first] = recipient.notifications

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await settle()

    assert len(recipient.notifications) == 2
    final = recipient.notifications[-1]
    assert is_silent(final)
    assert final["message"] == first["message"]


async def person_walks_up_and_leaves(frigate: Frigate) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await frigate.publish_review(
        review("end", [PERSON], ["person"], ["entry_breezeway"])
    )


async def next_alert_notification(frigate: Frigate, recipient: Recipient) -> dict:
    """Publishes a second, new Alert for a person and returns its Notification."""
    await frigate.publish_review(
        review(
            "new",
            [NEXT_PERSON],
            ["person"],
            ["entry_breezeway"],
            review_id=NEXT_REVIEW_ID,
        )
    )
    [notification] = [
        n for n in recipient.notifications if n["data"]["tag"] == NEXT_REVIEW_ID
    ]
    return notification


async def test_new_alert_within_the_quiet_window_arrives_silently(
    freezer: FrozenDateTimeFactory, frigate: Frigate, recipient: Recipient
) -> None:
    await person_walks_up_and_leaves(frigate)
    freezer.tick(timedelta(seconds=40))

    notification = await next_alert_notification(frigate, recipient)

    assert not is_silent(recipient.notifications[0])
    assert is_silent(notification)
    assert notification["message"] == "Person in Entry Breezeway"


async def test_new_alert_after_the_quiet_window_makes_a_sound(
    freezer: FrozenDateTimeFactory, frigate: Frigate, recipient: Recipient
) -> None:
    await person_walks_up_and_leaves(frigate)
    freezer.tick(timedelta(minutes=2, seconds=1))

    notification = await next_alert_notification(frigate, recipient)

    assert not is_silent(notification)


@pytest.mark.parametrize("blueprint_input", [{"quiet_window": 5}])
async def test_quiet_window_length_is_an_input(
    freezer: FrozenDateTimeFactory, frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    freezer.tick(timedelta(minutes=4))

    notification = await next_alert_notification(frigate, recipient)

    assert is_silent(notification)


async def test_alert_that_sent_no_notification_opens_no_quiet_window(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [CAR, PERSON], ["car", "person"], ["driveway"])
    )
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await settle()
    await frigate.publish_review(
        review("end", [CAR, PERSON], ["car", "person"], ["driveway"])
    )
    assert recipient.notifications == []
    freezer.tick(timedelta(seconds=30))

    notification = await next_alert_notification(frigate, recipient)

    assert not is_silent(notification)


def live_action(notification: dict) -> dict:
    [action] = [a for a in notification["data"]["actions"] if a["title"] == "Live"]
    return action


async def test_tapping_the_notification_opens_the_latest_review_in_the_app(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )

    [notification] = recipient.notifications
    url = urlparse(notification["data"]["url"])
    # A relative path stays in the app, on whatever connection it uses.
    assert not url.scheme and not url.netloc
    assert url.path == "/lovelace/front-door"
    assert url.query == f"advanced-camera-card-action.{REVIEW_CARD_ID}.review"


async def test_live_action_opens_the_cameras_view_in_the_app(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )

    [notification] = recipient.notifications
    action = live_action(notification)
    assert action["action"] == "URI"
    assert action["uri"] == "/lovelace/cameras"


async def test_updates_keep_the_tap_and_live_action(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )
    await frigate.publish_review(
        review("end", [PERSON], ["person"], ["entry_breezeway"])
    )

    first, *updates = recipient.notifications
    assert updates
    for update in updates:
        assert update["data"]["url"] == first["data"]["url"]
        assert live_action(update) == live_action(first)


@pytest.mark.parametrize(
    "blueprint_input",
    [{"review_view": "/our-dashboard/porch/", "live_view": " /our-dashboard/cams "}],
)
async def test_review_and_live_views_are_inputs(
    frigate: Frigate, recipient: Recipient
) -> None:
    await frigate.publish_review(
        review("new", [PERSON], ["person"], ["entry_breezeway"])
    )

    [notification] = recipient.notifications
    assert urlparse(notification["data"]["url"]).path == "/our-dashboard/porch"
    assert live_action(notification)["uri"] == "/our-dashboard/cams"
