from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from scripts.render_assets import load_overlay, render_text
from testing.clock import Clock
from testing.displays import (
    DisplaySource,
    assert_custom_cards_listed,
    assert_entities_documented,
    async_create_placeholder_entities,
    async_load_support_packages,
    async_render_templates,
    referenced_entities,
)

ROOT = Path(__file__).parents[2]
DISPLAY = ROOT / "wallboard" / "display.yaml"
CUSTOM_CARDS = ROOT / "custom-cards.yaml"
PACKAGE = ROOT / "wallboard" / "package.yaml"
OVERLAY = ROOT / "wallboard" / "wallboard.local.example.yaml"


@pytest.fixture
def display_source() -> DisplaySource:
    return DisplaySource.load(DISPLAY)


@pytest.fixture
def expected_lingering_timers() -> bool:
    # The package's appliance-running binary sensors intentionally debounce with delays.
    return True


@pytest.fixture
def display(display_source: DisplaySource) -> dict[str, Any]:
    return display_source.render(OVERLAY)


def rendered_package_config() -> dict[str, Any]:
    return yaml.safe_load(render_text(PACKAGE.read_text(), load_overlay(OVERLAY), source=PACKAGE))


def test_wallboard_uses_the_shared_safety_and_climate_roles(display: dict[str, Any]) -> None:
    entities = referenced_entities(display)
    assert "binary_sensor.example_immediate_hazards" in entities
    assert "binary_sensor.example_exterior_doors" in entities
    assert "binary_sensor.wallboard_hazards" not in entities
    assert "binary_sensor.wallboard_open_doors" not in entities


def test_wallboard_documents_every_entity_it_references(display: dict[str, Any], display_source: DisplaySource) -> None:
    assert_entities_documented(display, display_source)


def test_wallboard_custom_cards_are_in_the_manifest(display: dict[str, Any]) -> None:
    assert_custom_cards_listed(display, CUSTOM_CARDS)


async def test_wallboard_display_seam_renders_every_home_assistant_template(
    hass: HomeAssistant, display: dict[str, Any], display_source: DisplaySource
) -> None:
    await async_load_support_packages(hass, display_source, OVERLAY)
    await async_create_placeholder_entities(hass, display_source)

    rendered = await async_render_templates(hass, display)

    assert "Clear" in rendered
    assert any("Ready" in value for value in rendered)


async def test_wallboard_package_reports_unlocked_entries(hass: HomeAssistant) -> None:
    hass.states.async_set("lock.example_front_entry", "locked")
    hass.states.async_set("lock.example_garage_entry", "unlocked")
    hass.states.async_set("lock.example_side_entry", "locked")
    assert await async_setup_component(hass, "template", rendered_package_config())
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.wallboard_any_entry_unlocked").state == "on"
    assert hass.states.get("sensor.wallboard_unlocked_entries").state == "Garage Entry"


async def test_wallboard_package_reports_appliance_running(hass: HomeAssistant, clock: Clock) -> None:
    hass.states.async_set("sensor.example_washer_power", "350")
    hass.states.async_set("sensor.example_dryer_power", "120")
    hass.states.async_set("sensor.example_dishwasher_power", "600")
    assert await async_setup_component(hass, "template", rendered_package_config())
    await hass.async_block_till_done()
    await clock.advance(timedelta(minutes=2))

    assert hass.states.get("binary_sensor.wallboard_washer_active").state == "on"
    assert hass.states.get("binary_sensor.wallboard_dryer_active").state == "on"
    assert hass.states.get("binary_sensor.wallboard_dishwasher_active").state == "on"
