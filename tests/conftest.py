"""Runs the Alert Notifications blueprint in a real Home Assistant core.

Tests publish Frigate MQTT payloads and read back the notify calls a Recipient
would receive. Every value here is a placeholder (ADR 0004).
"""

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_mock_service,
)

BLUEPRINT = (
    Path(__file__).parent.parent
    / "blueprints"
    / "automation"
    / "jbruns"
    / "alert_notifications.yaml"
)
CAMERA_ENTITY = "camera.example"
CAMERA_NAME = "front_door"
LAST_NOTIFICATION = "input_datetime.example_last_notification"
BASE_URL = "https://ha.example.com"
REVIEW_ID = "1790000000.000000-rev1"


@pytest.fixture
def expected_lingering_timers() -> bool:
    # The mocked MQTT client leaves its periodic housekeeping timer behind.
    return True


@pytest.fixture
def expected_lingering_tasks() -> bool:
    # A run waits for its Review's next message until the Review ends.
    return True


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    target = Path(hass_tmp_config_dir) / "blueprints" / "automation" / "jbruns"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target / BLUEPRINT.name)
    return hass_tmp_config_dir


def review(
    kind: str,
    detections: list[str],
    objects: list[str],
    zones: list[str],
    *,
    sub_labels: list[str] | None = None,
    severity: str = "alert",
    before_severity: str | None = None,
    camera: str = CAMERA_NAME,
    review_id: str = REVIEW_ID,
) -> dict[str, Any]:
    """A frigate/reviews message, shaped like Frigate's review maintainer."""

    def segment(sev: str) -> dict[str, Any]:
        return {
            "id": review_id,
            "camera": camera,
            "start_time": 1790000000.0,
            "end_time": 1790000030.0 if kind == "end" else None,
            "severity": sev,
            "thumb_path": f"/media/frigate/clips/review/thumb-{camera}-{review_id}.webp",
            "data": {
                "detections": detections,
                "objects": objects,
                "verified_objects": [o for o in objects if o.endswith("-verified")],
                "sub_labels": sub_labels or [],
                "zones": zones,
                "audio": [],
                "thumb_time": 1790000001.0,
                "metadata": None,
            },
        }

    return {
        "type": kind,
        "before": segment(before_severity or severity),
        "after": segment(severity),
    }


def tracked_object(
    object_id: str,
    label: str,
    *,
    stationary: bool = False,
    current_zones: list[str] | None = None,
    entered_zones: list[str] | None = None,
    sub_label: str | None = None,
    snapshot_time: float | None = 1790000001.0,
    kind: str = "update",
    camera: str = CAMERA_NAME,
) -> dict[str, Any]:
    """A frigate/events message for one Tracked Object."""
    current = current_zones or []
    after = {
        "id": object_id,
        "camera": camera,
        "frame_time": snapshot_time or 1790000001.0,
        "snapshot": None
        if snapshot_time is None
        else {"frame_time": snapshot_time, "box": [0, 0, 10, 10], "score": 0.9},
        "label": label,
        "sub_label": None if sub_label is None else [sub_label, 0.95],
        "start_time": float(object_id.split("-")[0]),
        "end_time": None,
        "active": not stationary,
        "stationary": stationary,
        "current_zones": current,
        "entered_zones": entered_zones if entered_zones is not None else current,
        "has_snapshot": True,
        "false_positive": False,
    }
    return {"type": kind, "before": after, "after": after}


@dataclass
class Frigate:
    hass: HomeAssistant

    async def publish_review(self, payload: dict[str, Any]) -> None:
        async_fire_mqtt_message(self.hass, "frigate/reviews", json.dumps(payload))
        await settle()

    async def publish_event(self, payload: dict[str, Any]) -> None:
        async_fire_mqtt_message(self.hass, "frigate/events", json.dumps(payload))
        await settle()


async def settle() -> None:
    """Let runs process a message and return to waiting for the next one.

    hass.async_block_till_done() would wait for the runs themselves, which
    only finish when their Review ends. No timed sleeps: the freezer fixture
    stops the event loop's clock.
    """
    for _ in range(3):
        for _ in range(200):
            await asyncio.sleep(0)
        await asyncio.sleep(0)


@dataclass
class Recipient:
    device_id: str
    calls: list[ServiceCall]

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return [dict(call.data) for call in self.calls]


@pytest.fixture
async def recipient(hass: HomeAssistant) -> Recipient:
    entry = MockConfigEntry(domain="mobile_app", data={"device_name": "Test Phone"})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mobile_app", "test-phone")},
        name="Test Phone",
    )
    calls = async_mock_service(hass, "notify", "mobile_app_test_phone")
    return Recipient(device.id, calls)


@pytest.fixture
def blueprint_input() -> dict[str, Any]:
    """Extra blueprint inputs; override in a test module or with parametrize."""
    return {}


@pytest.fixture
async def frigate(
    hass: HomeAssistant,
    mqtt_mock: Any,
    recipient: Recipient,
    blueprint_input: dict[str, Any],
) -> Frigate:
    hass.states.async_set(CAMERA_ENTITY, "idle", {"camera_name": CAMERA_NAME})
    assert await async_setup_component(
        hass,
        "input_datetime",
        {
            "input_datetime": {
                LAST_NOTIFICATION.split(".")[1]: {"has_date": True, "has_time": True}
            }
        },
    )
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Alert Notifications",
                "use_blueprint": {
                    "path": "jbruns/alert_notifications.yaml",
                    "input": {
                        "camera": CAMERA_ENTITY,
                        "recipients": [recipient.device_id],
                        "base_url": BASE_URL,
                        "last_notification": LAST_NOTIFICATION,
                        **blueprint_input,
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get("automation.alert_notifications") is not None
    return Frigate(hass)
