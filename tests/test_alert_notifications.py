"""Behaviour of the Alert Notifications blueprint, as seen by a Recipient."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from conftest import (
    BASE_URL,
    REVIEW_ID,
    Frigate,
    Recipient,
    review,
    settle,
    tracked_object,
)

PERSON = "1790000000.100000-person"
CAR = "1789990000.000000-car"


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
