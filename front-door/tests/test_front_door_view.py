"""The front-door dashboard view that a tapped Alert Notification opens."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from conftest import BLUEPRINT, REVIEW_CARD_ID
from testing.displays import (
    DisplaySource,
    assert_custom_cards_listed,
    assert_entities_documented,
    async_create_placeholder_entities,
    async_load_support_packages,
    async_render_templates,
)

ROOT = Path(__file__).parents[2]
DISPLAY = ROOT / "dashboards" / "our-home" / "display.yaml"
CUSTOM_CARDS = ROOT / "dashboards" / "custom-cards.yaml"


@pytest.fixture
def display_source() -> DisplaySource:
    return DisplaySource.load(DISPLAY)


@pytest.fixture
def display(display_source: DisplaySource) -> dict[str, Any]:
    return display_source.render()


@pytest.fixture
def view(display: dict[str, Any]) -> dict[str, Any]:
    [view] = display["views"]
    return view


@pytest.fixture
def stack(view: dict[str, Any]) -> list[dict[str, Any]]:
    # A panel view shows only its first card.
    [stack] = view["cards"]
    assert stack["type"] == "vertical-stack"
    return stack["cards"]


@pytest.fixture
def card(stack: list[dict[str, Any]]) -> dict[str, Any]:
    [card] = [c for c in stack if c["type"] == "custom:advanced-camera-card"]
    return card


@pytest.fixture
def snooze_rows(stack: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Each Recipient's (Snooze state template, Resume button)."""
    rows = []
    for row in stack:
        if row["type"] != "horizontal-stack":
            continue
        [state] = [c for c in row["cards"] if c["type"] == "markdown"]
        [resume] = [c for c in row["cards"] if c.get("name") == "Resume"]
        rows.append((state["content"], resume))
    return rows


def helper(resume: dict[str, Any]) -> str:
    return resume["tap_action"]["target"]["entity_id"]


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


def test_view_shows_both_recipients_snoozes(snooze_rows: list) -> None:
    helpers = [helper(resume) for _, resume in snooze_rows]
    assert len(set(helpers)) == 2
    # ADR 0004: placeholders, named as the blueprint expects.
    assert all(h.startswith("input_datetime.snooze_phone_") for h in helpers)
    for content, resume in snooze_rows:
        assert helper(resume) in content


def test_display_documents_every_entity_it_references(
    display: dict[str, Any], display_source: DisplaySource
) -> None:
    assert_entities_documented(display, display_source)


def test_display_custom_cards_are_in_the_manifest(display: dict[str, Any]) -> None:
    assert_custom_cards_listed(display, CUSTOM_CARDS)


async def render(hass: HomeAssistant, content: str) -> str:
    return Template(content, hass).async_render(parse_result=False)


@pytest.fixture
async def helpers(
    hass: HomeAssistant, snooze_rows: list, display_source: DisplaySource
) -> list[str]:
    await async_load_support_packages(hass, display_source)
    await async_create_placeholder_entities(hass, display_source)
    ids = [helper(resume) for _, resume in snooze_rows]
    return ids


async def snooze(hass: HomeAssistant, entity_id: str, minutes: int) -> None:
    await hass.services.async_call(
        "input_datetime",
        "set_datetime",
        {"timestamp": (dt_util.now() + timedelta(minutes=minutes)).timestamp()},
        target={"entity_id": entity_id},
        blocking=True,
    )


async def test_snooze_state_reads_snoozed_until_or_not_snoozed(
    hass: HomeAssistant, snooze_rows: list, helpers: list[str]
) -> None:
    (snoozed, _), (not_snoozed, _) = snooze_rows
    await snooze(hass, helpers[0], 30)
    until = dt_util.as_local(dt_util.now() + timedelta(minutes=30)).strftime("%H:%M")

    assert f"Snoozed until {until}" in await render(hass, snoozed)
    assert "Not snoozed" in await render(hass, not_snoozed)


async def test_display_seam_renders_every_template(
    hass: HomeAssistant, display: dict[str, Any], display_source: DisplaySource
) -> None:
    await async_load_support_packages(hass, display_source)
    await async_create_placeholder_entities(hass, display_source)

    rendered = await async_render_templates(hass, display)

    assert any("Not snoozed" in value for value in rendered)


async def test_resume_ends_that_recipients_snooze_only(
    hass: HomeAssistant, snooze_rows: list, helpers: list[str]
) -> None:
    (content, resume), (other_content, _) = snooze_rows
    for h in helpers:
        await snooze(hass, h, 120)

    action = resume["tap_action"]
    assert action["action"] == "perform-action"
    domain, service = action["perform_action"].split(".")
    await hass.services.async_call(
        domain, service, action.get("data", {}), target=action["target"], blocking=True
    )

    assert "Not snoozed" in await render(hass, content)
    assert "Snoozed until" in await render(hass, other_content)


class _BlueprintLoader(yaml.SafeLoader):
    pass


_BlueprintLoader.add_constructor("!input", lambda loader, node: loader.construct_scalar(node))
