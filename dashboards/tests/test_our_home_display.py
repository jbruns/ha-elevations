from datetime import datetime
from pathlib import Path

import pytest

from homeassistant.core import HomeAssistant

from testing.clock import Clock

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
OVERLAY = ROOT / "dashboards" / "our-home" / "our-home.local.example.yaml"
CUSTOM_CARDS = ROOT / "dashboards" / "custom-cards.yaml"


def test_our_home_contains_every_live_view() -> None:
    display = DisplaySource.load(DISPLAY).render(OVERLAY)

    assert [view["path"] for view in display["views"]] == [
        "mushroom",
        "lighting",
        "climate",
        "cameras",
        "devices",
        "tv",
        "front-door",
    ]


def test_our_home_documents_every_entity_it_references() -> None:
    source = DisplaySource.load(DISPLAY)
    display = source.render(OVERLAY)

    assert_entities_documented(display, source)


def test_our_home_custom_cards_are_in_the_manifest() -> None:
    display = DisplaySource.load(DISPLAY).render(OVERLAY)

    assert_custom_cards_listed(display, CUSTOM_CARDS)


async def test_our_home_display_seam_renders_every_template(hass: HomeAssistant) -> None:
    source = DisplaySource.load(DISPLAY)
    display = source.render(OVERLAY)
    await async_load_support_packages(hass, source)
    await async_create_placeholder_entities(hass, source)

    rendered = await async_render_templates(hass, display)

    assert rendered
    assert all(isinstance(value, str) for value in rendered)


def greeting_title(display: dict) -> str:
    [conditions] = [view for view in display["views"] if view["path"] == "mushroom"]
    return conditions["cards"][0]["cards"][0]["title"]


def test_our_home_reads_no_greeting_helper() -> None:
    source = DisplaySource.load(DISPLAY)
    display = source.render(OVERLAY)

    assert "ui_frontend_greeting" not in str(display)
    assert not any("greeting" in entity for entity in source.prerequisites)


@pytest.mark.parametrize(
    ("hour", "greeting"),
    [
        (5, "Good Night"),
        (6, "Good Morning"),
        (11, "Good Morning"),
        (12, "Good Afternoon"),
        (16, "Good Afternoon"),
        (17, "Good Evening"),
        (21, "Good Evening"),
        (22, "Good Night"),
        (0, "Good Night"),
    ],
)
async def test_our_home_greets_by_time_of_day(
    hass: HomeAssistant, clock: Clock, hour: int, greeting: str
) -> None:
    display = DisplaySource.load(DISPLAY).render(OVERLAY)
    await clock.move_to(datetime(2026, 10, 1, hour, 30))

    [rendered] = await async_render_templates(hass, {"title": greeting_title(display)})

    assert rendered.strip() == greeting
