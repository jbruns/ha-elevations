"""The front-door dashboard view that a tapped Alert Notification opens."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import BLUEPRINT, REVIEW_CARD_ID

VIEW = Path(__file__).parent.parent / "dashboards" / "front_door_view.yaml"


@pytest.fixture
def view() -> dict[str, Any]:
    return yaml.safe_load(VIEW.read_text())


@pytest.fixture
def card(view: dict[str, Any]) -> dict[str, Any]:
    [card] = view["cards"]
    return card


@pytest.fixture
def camera(card: dict[str, Any]) -> dict[str, Any]:
    [camera] = card["cameras"]
    return camera


def test_view_path_is_the_blueprints_default_review_view(view: dict) -> None:
    # The blueprint uses !input tags, which safe_load can't read.
    blueprint = yaml.load(BLUEPRINT.read_text(), Loader=_BlueprintLoader)
    default = blueprint["blueprint"]["input"]["review_view"]["default"]
    assert default.rstrip("/").split("/")[-1] == view["path"]


def test_view_is_a_full_screen_subview(view: dict) -> None:
    assert view["type"] == "panel"
    assert view["subview"] is True


def test_card_answers_the_notifications_url_action(card: dict) -> None:
    assert card["type"] == "custom:advanced-camera-card"
    assert card["card_id"] == REVIEW_CARD_ID


def test_card_opens_the_latest_review_reviewed_or_not(card: dict, camera: dict) -> None:
    assert card["view"]["default"] == "review"
    assert camera["media"] == {"type": "reviews", "reviewed": "all"}


def test_card_shows_a_timeline_to_scrub(card: dict) -> None:
    assert "scrubbing" in card["profiles"]


def test_camera_is_a_placeholder(camera: dict) -> None:
    # ADR 0004: the real camera is picked when the view is installed.
    assert camera["camera_entity"] == "camera.example"


class _BlueprintLoader(yaml.SafeLoader):
    pass


_BlueprintLoader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))
