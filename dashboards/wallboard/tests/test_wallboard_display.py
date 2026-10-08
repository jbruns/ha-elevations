from datetime import datetime, time, timedelta
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
    custom_card_manifest,
    async_create_placeholder_entities,
    async_load_support_packages,
    async_render_templates,
    referenced_entities,
    walk,
)

ROOT = Path(__file__).parents[2]
DISPLAY = ROOT / "wallboard" / "display.yaml"
CUSTOM_CARDS = ROOT / "custom-cards.yaml"
PACKAGE = ROOT / "wallboard" / "package.yaml"
OVERLAY = ROOT / "wallboard" / "wallboard.local.example.yaml"
THEME = ROOT / "wallboard" / "themes" / "wallboard.yaml"


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


def wallboard_home(display: dict[str, Any]) -> dict[str, Any]:
    [home] = display["views"]
    return home


# Home Assistant sections view geometry, from hui-sections-view and hui-grid-section.
SCREEN_WIDTH_PX = 1920
SCREEN_HEIGHT_PX = 1080
VIEW_COLUMN_GAP_PX = 32
VIEW_ROW_GAP_PX = 24
CARD_ROW_HEIGHT_PX = 56
CARD_ROW_GAP_PX = 8


def week_planner_card(display: dict[str, Any]) -> dict[str, Any]:
    for section in wallboard_home(display)["sections"]:
        for card in section["cards"]:
            if card.get("type") == "custom:week-planner-card":
                return card
    raise AssertionError("Wallboard Household Schedule does not include week-planner-card")


SECTION_GRID_COLUMNS = 12
SCHOOL_DAY = "input_select.example_school_day"
# A weekday morning and afternoon, on and off a School Day.
WALLBOARD_MOMENTS = [
    ({SCHOOL_DAY: school_day}, datetime(2026, 10, 7, hour, 0))
    for school_day in ("true", "false")
    for hour in (8, 15)
]


def card_visible(card: dict[str, Any], states: dict[str, str], now: datetime) -> bool:
    return all(condition_holds(condition, states, now) for condition in card.get("visibility", []))


def condition_holds(condition: dict[str, Any], states: dict[str, str], now: datetime) -> bool:
    kind = condition["condition"]
    if kind == "and":
        return all(condition_holds(c, states, now) for c in condition["conditions"])
    if kind == "or":
        return any(condition_holds(c, states, now) for c in condition["conditions"])
    if kind == "not":
        return not any(condition_holds(c, states, now) for c in condition["conditions"])
    if kind == "state":
        state = states[condition["entity"]]
        if "state" in condition:
            return state == condition["state"]
        return state != condition["state_not"]
    if kind == "time":
        after = time.fromisoformat(condition.get("after", "00:00"))
        before = time.fromisoformat(condition.get("before", "23:59:59"))
        weekdays = condition.get("weekdays", [now.strftime("%a").lower()])
        return after <= now.time() < before and now.strftime("%a").lower() in weekdays
    raise AssertionError(f"unsupported visibility condition: {kind}")


def card_columns(card: dict[str, Any]) -> int:
    columns = card["grid_options"].get("columns", "full")
    return SECTION_GRID_COLUMNS if columns == "full" else columns


def packed_rows(cards: list[dict[str, Any]]) -> int:
    # Sections place cards with CSS grid "row dense" auto-placement.
    taken: set[tuple[int, int]] = set()
    bottom = 0
    for card in cards:
        width, height = card_columns(card), card["grid_options"]["rows"]
        row = 0
        while True:
            column = next(
                (
                    c
                    for c in range(SECTION_GRID_COLUMNS - width + 1)
                    if not any((row + r, c + w) in taken for r in range(height) for w in range(width))
                ),
                None,
            )
            if column is not None:
                break
            row += 1
        taken.update((row + r, column + w) for r in range(height) for w in range(width))
        bottom = max(bottom, row + height)
    return bottom


def section_rows(section: dict[str, Any]) -> int:
    return max(
        packed_rows([card for card in section["cards"] if card_visible(card, states, now)])
        for states, now in WALLBOARD_MOMENTS
    )


def section_height_px(section: dict[str, Any]) -> int:
    rows = section_rows(section)
    return rows * CARD_ROW_HEIGHT_PX + (rows - 1) * CARD_ROW_GAP_PX


def test_wallboard_lays_out_glance_band_schedule_and_rail(display: dict[str, Any]) -> None:
    view = wallboard_home(display)

    assert view["type"] == "sections"
    assert view["max_columns"] == 3
    assert [section["column_span"] for section in view["sections"]] == [3, 2, 1]
    assert week_planner_card(display) in view["sections"][1]["cards"]
    assert all("title" not in section for section in view["sections"])
    assert view["badges"] == []


def test_wallboard_fits_one_1080p_screen_without_scrolling(display: dict[str, Any]) -> None:
    glance, schedule, rail = wallboard_home(display)["sections"]
    lower_band = max(section_height_px(schedule), section_height_px(rail))

    used = VIEW_ROW_GAP_PX + section_height_px(glance) + VIEW_ROW_GAP_PX + lower_band + VIEW_ROW_GAP_PX

    assert used <= SCREEN_HEIGHT_PX
    assert 0.15 <= section_height_px(glance) / SCREEN_HEIGHT_PX <= 0.2


def test_wallboard_household_schedule_shows_seven_days_at_every_wide_breakpoint(display: dict[str, Any]) -> None:
    # week-planner-card breakpoints by card width: small is 640px and up; extraSmall is below that.
    assert week_planner_card(display)["columns"] == {
        "extraLarge": 7,
        "large": 7,
        "medium": 7,
        "small": 7,
        "extraSmall": 1,
    }


def test_wallboard_base_theme_lets_three_columns_fill_the_screen(display: dict[str, Any]) -> None:
    themes = yaml.safe_load(THEME.read_text())
    column_max_width = themes["Wallboard"]["ha-view-sections-column-max-width"]
    columns = wallboard_home(display)["max_columns"]

    assert list(themes) == ["Wallboard"]
    assert column_max_width.endswith("px")
    # The view wrapper is padded by one column gap on each side.
    widest_view = columns * int(column_max_width.removesuffix("px")) + (columns + 1) * VIEW_COLUMN_GAP_PX
    assert widest_view >= SCREEN_WIDTH_PX


def test_wallboard_visual_ownership_stays_with_the_kiosk_browser(display: dict[str, Any]) -> None:
    view = wallboard_home(display)

    assert "theme" not in view
    assert "background" not in view


def test_wallboard_dashboard_enables_kiosk_mode(display: dict[str, Any]) -> None:
    assert display["kiosk_mode"] == {"hide_header": True, "hide_sidebar": True}


def test_wallboard_frontend_plugins_are_in_the_manifest() -> None:
    listed = set(custom_card_manifest(CUSTOM_CARDS))

    assert {"kiosk-mode", "card-mod"} <= listed


def test_wallboard_keeps_its_support_package(display_source: DisplaySource) -> None:
    assert display_source.support_packages == [PACKAGE.resolve()]


def test_wallboard_household_schedule_shows_only_shared_calendars(display: dict[str, Any]) -> None:
    calendars = week_planner_card(display)["calendars"]

    assert calendars == [
        {"entity": "calendar.example_family", "name": "Family", "color": "#2f80ed"},
        {"entity": "calendar.example_appointments", "name": "Appointments", "color": "#9b51e0"},
        {"entity": "calendar.example_trips_breaks", "name": "Trips & breaks", "color": "#27ae60"},
        {"entity": "calendar.example_birthdays", "name": "Birthdays", "color": "#f2994a"},
        {"entity": "calendar.example_us_holidays", "name": "US holidays", "color": "#eb5757"},
        {"entity": "calendar.example_school_closures", "name": "Holidays/closures", "color": "#56ccf2"},
        {"entity": "calendar.example_collection", "name": "Collection", "color": "#828282"},
    ]
    assert "calendar.example_school_lunch" not in {calendar["entity"] for calendar in calendars}


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

    assert rendered == []


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


def rail_section(display: dict[str, Any]) -> dict[str, Any]:
    return wallboard_home(display)["sections"][2]


def rail_list_cards(display: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {card["title"]: card for card in rail_section(display)["cards"] if card.get("type") == "todo-list"}


def test_wallboard_rail_shows_shopping_reminders_and_after_school_lists(display: dict[str, Any]) -> None:
    lists = rail_list_cards(display)

    assert {title: card["entity"] for title, card in lists.items()} == {
        "Shopping List": "todo.example_shopping_list",
        "Family Reminders": "todo.example_family_reminders",
        "After-School Tasks": "todo.example_after_school_tasks",
    }


def test_wallboard_rail_shows_after_school_tasks_only_on_school_day_afternoons(display: dict[str, Any]) -> None:
    lists = rail_list_cards(display)

    shown = {
        (states[SCHOOL_DAY], now.hour): sorted(title for title, card in lists.items() if card_visible(card, states, now))
        for states, now in WALLBOARD_MOMENTS
    }

    assert shown == {
        ("true", 8): ["Family Reminders", "Shopping List"],
        ("true", 15): ["After-School Tasks", "Shopping List"],
        ("false", 8): ["Family Reminders", "Shopping List"],
        ("false", 15): ["Family Reminders", "Shopping List"],
    }


def test_wallboard_rail_lists_fit_their_four_row_band(display: dict[str, Any]) -> None:
    lists = list(rail_list_cards(display).values())

    assert section_rows({"cards": lists}) == 4


def test_wallboard_rail_lists_hide_completed_items_and_keep_quick_add(display: dict[str, Any]) -> None:
    for card in rail_list_cards(display).values():
        assert card["hide_completed"] is True
        assert card.get("hide_create", False) is False


def test_wallboard_rail_lists_use_only_the_built_in_todo_list_card(display: dict[str, Any]) -> None:
    list_cards = [
        node
        for node in walk(rail_section(display))
        if isinstance(node, dict) and str(node.get("entity", "")).startswith("todo.")
    ]

    assert len(list_cards) == 3
    assert {card["type"] for card in list_cards} == {"todo-list"}
