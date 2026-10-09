import re
from datetime import datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

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


def wallboard_view(display: dict[str, Any], path: str) -> dict[str, Any]:
    [view] = [view for view in display["views"] if view["path"] == path]
    return view


def wallboard_home(display: dict[str, Any]) -> dict[str, Any]:
    return wallboard_view(display, "home")


def wallboard_month(display: dict[str, Any]) -> dict[str, Any]:
    return wallboard_view(display, "month")


def navigated_view_path(from_view: str, navigation_path: str) -> str:
    # A relative path resolves against the open view's URL, as history.pushState does,
    # so it works under whatever url_path the dashboard is installed at.
    dashboard = "https://ha.example.com/wallboard/"
    target = urljoin(dashboard + from_view, navigation_path)
    assert target.startswith(dashboard), f"{navigation_path!r} leaves the Wallboard"
    return target.removeprefix(dashboard)


HOUSEHOLD_SCHEDULE_CALENDARS = [
    "calendar.example_family",
    "calendar.example_appointments",
    "calendar.example_trips_breaks",
    "calendar.example_birthdays",
    "calendar.example_us_holidays",
    "calendar.example_school_closures",
    "calendar.example_collection",
]


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


# A section's grid has 12 columns for each column it spans.
SECTION_GRID_COLUMNS = 12
SCHOOL_DAY = "input_boolean.example_school_day_today"
SCHOOL_DAY_TOMORROW = "input_boolean.example_school_day_tomorrow"
COUNTDOWNS = "sensor.wallboard_countdowns"
# A weekday morning and afternoon, on and off a School Day, before a School Day or not.
WALLBOARD_MOMENTS = [
    ({SCHOOL_DAY: school_day, SCHOOL_DAY_TOMORROW: tomorrow, COUNTDOWNS: "4"}, datetime(2026, 10, 7, hour, 0))
    for school_day in ("on", "off")
    for tomorrow in ("on", "off")
    for hour in (8, 15)
]


def card_visible(card: dict[str, Any], states: dict[str, str], now: datetime) -> bool:
    return all(condition_holds(condition, states, now) for condition in card.get("visibility", []))


def condition_holds(condition: dict[str, Any], states: dict[str, str], now: datetime) -> bool:
    kind = condition["condition"]
    if kind == "or":
        return any(condition_holds(c, states, now) for c in condition["conditions"])
    if kind == "state":
        state = states[condition["entity"]]
        if "state" in condition:
            return state == condition["state"]
        return state != condition["state_not"]
    if kind == "numeric_state":
        value = float(states[condition["entity"]])
        return value > condition.get("above", float("-inf")) and value < condition.get("below", float("inf"))
    if kind == "time":
        after = time.fromisoformat(condition.get("after", "00:00"))
        before = time.fromisoformat(condition.get("before", "23:59:59"))
        return after <= now.time() < before
    raise AssertionError(f"unsupported visibility condition: {kind}")


def card_columns(card: dict[str, Any], grid_columns: int) -> int:
    columns = card["grid_options"].get("columns", "full")
    return grid_columns if columns == "full" else min(columns, grid_columns)


def packed_cells(cards: list[dict[str, Any]], grid_columns: int) -> set[tuple[int, int]]:
    # Sections place cards with CSS grid "row dense" auto-placement.
    taken: set[tuple[int, int]] = set()
    for card in cards:
        width, height = card_columns(card, grid_columns), card["grid_options"]["rows"]
        row = 0
        while True:
            column = next(
                (
                    c
                    for c in range(grid_columns - width + 1)
                    if not any((row + r, c + w) in taken for r in range(height) for w in range(width))
                ),
                None,
            )
            if column is not None:
                break
            row += 1
        taken.update((row + r, column + w) for r in range(height) for w in range(width))
    return taken


def packed_rows(cards: list[dict[str, Any]], grid_columns: int = SECTION_GRID_COLUMNS) -> int:
    return max((row + 1 for row, _ in packed_cells(cards, grid_columns)), default=0)


def grid_columns(section: dict[str, Any]) -> int:
    return SECTION_GRID_COLUMNS * section.get("column_span", 1)


def heading_card(section: dict[str, Any]) -> dict[str, Any]:
    [heading] = [card for card in section["cards"] if card["type"] == "heading"]
    return heading


def month_calendar_card(display: dict[str, Any]) -> dict[str, Any]:
    [calendar] = [card for card in walk(wallboard_month(display)) if isinstance(card, dict) and card.get("type") == "calendar"]
    return calendar


def chores_card(display: dict[str, Any]) -> dict[str, Any]:
    _, _, rail = wallboard_home(display)["sections"]
    for card in rail["cards"]:
        if "sensor.example_child_a_choreops_ui_dashboard_helper" in card.get("filter", {}).get("template", ""):
            return card
    raise AssertionError("Wallboard rail does not include the Chores card")


def set_child_chores(hass: HomeAssistant, child: str, chores: list[tuple[str, str, str]]) -> None:
    # ChoreOps publishes each child's Chores on a dashboard helper, and each Chore on a status sensor.
    listed = []
    for slug, name, state in chores:
        status = f"sensor.example_{child}_chore_status_{slug}"
        hass.states.async_set(
            status,
            state,
            {
                "chore_name": name,
                "claim_button_eid": f"button.example_{child}_claim_chore_{slug}",
                "approve_button_eid": f"button.example_{child}_approve_chore_{slug}",
                "disapprove_button_eid": f"button.example_{child}_disapprove_chore_{slug}",
            },
        )
        group = "this_week" if slug.endswith("_later") else "today"
        listed.append({"eid": status, "name": name, "state": state, "labels": [], "primary_group": group})
    hass.states.async_set(f"sensor.example_{child}_choreops_ui_dashboard_helper", "available", {"chores": listed})


def children_chores(hass: HomeAssistant, display: dict[str, Any]) -> list[dict[str, Any]]:
    return list(Template(chores_card(display)["filter"]["template"], hass).async_render())


def child_chips_card(child: dict[str, Any]) -> dict[str, Any]:
    [chips] = [card for card in child["cards"] if card["type"] == "custom:mushroom-chips-card"]
    return chips


def child_chips(child: dict[str, Any]) -> list[dict[str, Any]]:
    return child_chips_card(child)["chips"]


def faded_chip_positions(chips_card: dict[str, Any]) -> set[int]:
    faded = set()
    for first, last in re.findall(r"nth-child\(n\+(\d+)\):nth-child\(-n\+(\d+)\) \{ opacity: 0\.45; \}", chips_card["card_mod"]["style"]):
        faded.update(range(int(first), int(last) + 1))
    return faded


def section_rows(section: dict[str, Any]) -> int:
    return max(
        packed_rows([card for card in section["cards"] if card_visible(card, states, now)], grid_columns(section))
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
    for view in display["views"]:
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


def test_wallboard_household_schedule_header_opens_month(display: dict[str, Any]) -> None:
    header = heading_card(wallboard_home(display)["sections"][1])
    tap_action = header["tap_action"]

    assert header["type"] == "heading"
    assert header["heading"] == "Household Schedule"
    assert tap_action["action"] == "navigate"
    assert navigated_view_path("home", tap_action["navigation_path"]) == "month"


def test_wallboard_month_is_the_only_secondary_view(display: dict[str, Any]) -> None:
    month = wallboard_month(display)

    assert [view["path"] for view in display["views"]] == ["home", "month"]
    assert month["subview"] is True
    assert navigated_view_path("month", month["back_path"]) == "home"


def test_wallboard_month_shows_the_household_schedule_calendars_as_a_month_grid(display: dict[str, Any]) -> None:
    calendar = month_calendar_card(display)

    assert calendar["initial_view"] == "dayGridMonth"
    assert calendar["entities"] == HOUSEHOLD_SCHEDULE_CALENDARS
    assert calendar["entities"] == [entry["entity"] for entry in week_planner_card(display)["calendars"]]


def test_wallboard_month_offers_no_event_creation(display: dict[str, Any]) -> None:
    calendar = month_calendar_card(display)

    assert calendar["show_add_event"] is False


def test_wallboard_month_header_returns_to_the_main_view(display: dict[str, Any]) -> None:
    [section] = wallboard_month(display)["sections"]
    header = heading_card(section)

    assert header["type"] == "heading"
    assert header["tap_action"]["action"] == "navigate"
    assert navigated_view_path("month", header["tap_action"]["navigation_path"]) == "home"


def test_wallboard_month_fits_one_1080p_screen_without_scrolling(display: dict[str, Any]) -> None:
    month = wallboard_month(display)
    [section] = month["sections"]

    used = VIEW_ROW_GAP_PX + section_height_px(section) + VIEW_ROW_GAP_PX

    assert month["type"] == "sections"
    assert section["column_span"] == month["max_columns"]
    assert used <= SCREEN_HEIGHT_PX


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

    assert rendered
    assert all(isinstance(value, str) for value in rendered)


async def test_wallboard_shows_chores_for_each_child(hass: HomeAssistant, display: dict[str, Any]) -> None:
    set_child_chores(hass, "child_a", [("make_bed", "Make bed", "overdue")])
    set_child_chores(hass, "child_b", [])

    child_a, child_b = children_chores(hass, display)

    assert [card["heading"] for card in (child_a["cards"][0], child_b["cards"][0])] == ["Child A", "Child B"]
    assert [chip["content"] for chip in child_chips(child_a)] == ["Make bed"]
    assert [chip["content"] for chip in child_chips(child_b)] == ["Nothing due"]


def rail_section(display: dict[str, Any]) -> dict[str, Any]:
    return wallboard_home(display)["sections"][2]


def schedule_section(display: dict[str, Any]) -> dict[str, Any]:
    return wallboard_home(display)["sections"][1]


def list_cards(display: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {card["title"]: card for card in schedule_section(display)["cards"] if card.get("type") == "todo-list"}


def test_wallboard_shows_shopping_reminders_and_after_school_lists_under_the_week_grid(display: dict[str, Any]) -> None:
    lists = list_cards(display)

    assert {title: card["entity"] for title, card in lists.items()} == {
        "Shopping List": "todo.example_shopping_list",
        "Family Reminders": "todo.example_family_reminders",
        "After-School Tasks": "todo.example_after_school_tasks",
    }


def test_wallboard_shows_after_school_tasks_only_on_school_day_afternoons(display: dict[str, Any]) -> None:
    lists = list_cards(display)

    shown = {
        (states[SCHOOL_DAY], now.hour): sorted(title for title, card in lists.items() if card_visible(card, states, now))
        for states, now in WALLBOARD_MOMENTS
    }

    assert shown == {
        ("on", 8): ["Family Reminders", "Shopping List"],
        ("on", 15): ["After-School Tasks", "Shopping List"],
        ("off", 8): ["Family Reminders", "Shopping List"],
        ("off", 15): ["Family Reminders", "Shopping List"],
    }


def test_wallboard_lists_fit_their_four_row_band(display: dict[str, Any]) -> None:
    lists = list(list_cards(display).values())

    assert section_rows({"cards": lists, "column_span": schedule_section(display)["column_span"]}) == 4


def test_wallboard_lists_hide_completed_items_and_keep_quick_add(display: dict[str, Any]) -> None:
    for card in list_cards(display).values():
        assert card["hide_completed"] is True
        assert card.get("hide_create", False) is False


def test_wallboard_lists_use_only_the_built_in_todo_list_card(display: dict[str, Any]) -> None:
    todo_cards = [
        node
        for node in walk(wallboard_home(display))
        if isinstance(node, dict) and str(node.get("entity", "")).startswith("todo.")
    ]

    assert len(todo_cards) == 3
    assert {card["type"] for card in todo_cards} == {"todo-list"}


async def test_wallboard_lists_overdue_chores_first_then_due_today_up_to_six(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    set_child_chores(
        hass,
        "child_a",
        [
            ("feed_fish", "Feed fish", "pending"),
            ("make_bed", "Make bed", "overdue"),
            ("tidy_room", "Tidy room", "due"),
            ("set_table", "Set table", "approved"),
            ("shower", "Shower", "overdue"),
            ("mow_lawn_later", "Mow lawn", "pending"),
            ("water_plants", "Water plants", "due"),
            ("brush_teeth", "Brush teeth", "claimed"),
            ("pack_bag", "Pack bag", "overdue"),
        ],
    )
    set_child_chores(hass, "child_b", [])

    child_a, _ = children_chores(hass, display)

    assert [chip["content"] for chip in child_chips(child_a)] == [
        "Make bed",
        "Shower",
        "Pack bag",
        "Feed fish",
        "Tidy room",
        "Water plants",
        "+1 more",
    ]


async def test_wallboard_claims_a_chore_on_tap_without_confirmation(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    set_child_chores(hass, "child_a", [("make_bed", "Make bed", "overdue")])
    set_child_chores(hass, "child_b", [("tidy_room", "Tidy room", "due")])

    child_a, child_b = children_chores(hass, display)

    assert [chip["tap_action"] for chip in child_chips(child_a) + child_chips(child_b)] == [
        {
            "action": "perform-action",
            "perform_action": "button.press",
            "target": {"entity_id": "button.example_child_a_claim_chore_make_bed"},
        },
        {
            "action": "perform-action",
            "perform_action": "button.press",
            "target": {"entity_id": "button.example_child_b_claim_chore_tidy_room"},
        },
    ]


async def test_wallboard_greys_a_claimed_chore_until_it_is_approved(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    set_child_chores(
        hass,
        "child_a",
        [("brush_teeth", "Brush teeth", "claimed"), ("make_bed", "Make bed", "overdue")],
    )
    set_child_chores(hass, "child_b", [("tidy_room", "Tidy room", "approved")])

    child_a, child_b = children_chores(hass, display)
    make_bed, brush_teeth = child_chips(child_a)

    assert brush_teeth["content"] == "Brush teeth"
    assert brush_teeth["icon_color"] == "disabled"
    assert brush_teeth["tap_action"] == {"action": "none"}
    assert faded_chip_positions(child_chips_card(child_a)) == {2}
    assert make_bed["icon_color"] == "red"
    assert [chip["content"] for chip in child_chips(child_b)] == ["Nothing due"]


async def test_wallboard_leaves_chore_approvals_off_the_board(hass: HomeAssistant, display: dict[str, Any]) -> None:
    set_child_chores(
        hass,
        "child_a",
        [("brush_teeth", "Brush teeth", "claimed"), ("make_bed", "Make bed", "overdue")],
    )
    set_child_chores(hass, "child_b", [("tidy_room", "Tidy room", "due")])

    shown = repr(children_chores(hass, display)) + repr(chores_card(display))

    assert "approve" not in shown


async def test_wallboard_chore_cards_are_in_the_manifest(hass: HomeAssistant, display: dict[str, Any]) -> None:
    set_child_chores(hass, "child_a", [("make_bed", "Make bed", "overdue")])
    set_child_chores(hass, "child_b", [])

    assert_custom_cards_listed({"cards": children_chores(hass, display)}, CUSTOM_CARDS)


# Today and Countdowns

SCHOOL_LUNCH = "sensor.wallboard_school_lunch"


def rail_card(display: dict[str, Any], entity: str) -> dict[str, Any]:
    [card] = [card for card in rail_section(display)["cards"] if entity in repr(card.get("content", ""))]
    return card


def today_card(display: dict[str, Any]) -> dict[str, Any]:
    return rail_card(display, SCHOOL_LUNCH)


def countdowns_card(display: dict[str, Any]) -> dict[str, Any]:
    return rail_card(display, COUNTDOWNS)


async def show_today(
    hass: HomeAssistant,
    clock: Clock,
    display: dict[str, Any],
    when: datetime,
    *,
    today: str,
    tomorrow: str,
    classes: tuple[str, str] = ("Library", "PE"),
    lunch: tuple[str, str] = ("Cheese Pizza Slice", "Roasted Chicken"),
) -> str | None:
    """The Today block as shown at a moment, or None while it is hidden."""
    states = {SCHOOL_DAY: today, SCHOOL_DAY_TOMORROW: tomorrow, COUNTDOWNS: "0"}
    for entity, state in states.items():
        hass.states.async_set(entity, state)
    hass.states.async_set("input_select.example_child_a_special_class", classes[0])
    hass.states.async_set("input_select.example_child_b_special_class", classes[1])
    hass.states.async_set(SCHOOL_LUNCH, lunch[0], {"today": lunch[0], "tomorrow": lunch[1]})
    await clock.move_to(when)
    card = today_card(display)
    if not card_visible(card, states, when):
        return None
    return Template(card["content"], hass).async_render(parse_result=False)


def test_wallboard_rail_orders_today_countdowns_then_chores(display: dict[str, Any]) -> None:
    assert rail_section(display)["cards"] == [today_card(display), countdowns_card(display), chores_card(display)]


def test_wallboard_household_schedule_puts_the_lists_beneath_the_week_grid(display: dict[str, Any]) -> None:
    cards = schedule_section(display)["cards"]

    assert cards[:2] == [heading_card(schedule_section(display)), week_planner_card(display)]
    assert [card["type"] for card in cards[2:]] == ["todo-list"] * 3


async def test_wallboard_today_shows_the_school_day_special_classes_and_lunch(
    hass: HomeAssistant, clock: Clock, display: dict[str, Any]
) -> None:
    shown = await show_today(hass, clock, display, datetime(2026, 10, 7, 8, 0), today="on", tomorrow="on")

    assert "School Day" in shown
    assert "Child A: Library" in shown
    assert "Child B: PE" in shown
    assert "Cheese Pizza Slice" in shown
    assert "Roasted Chicken" not in shown


async def test_wallboard_today_adds_tomorrows_lunch_from_3_pm(
    hass: HomeAssistant, clock: Clock, display: dict[str, Any]
) -> None:
    before = await show_today(hass, clock, display, datetime(2026, 10, 7, 14, 59), today="on", tomorrow="on")
    after = await show_today(hass, clock, display, datetime(2026, 10, 7, 15, 0), today="on", tomorrow="on")
    friday = await show_today(hass, clock, display, datetime(2026, 10, 9, 15, 0), today="on", tomorrow="off")

    assert "Roasted Chicken" not in before
    assert "Cheese Pizza Slice" in after
    assert "Roasted Chicken" in after
    assert "Roasted Chicken" not in friday


async def test_wallboard_today_leaves_out_a_child_without_a_special_class(
    hass: HomeAssistant, clock: Clock, display: dict[str, Any]
) -> None:
    one = await show_today(
        hass, clock, display, datetime(2026, 10, 7, 8, 0), today="on", tomorrow="on", classes=("Art", "No class")
    )
    none = await show_today(
        hass, clock, display, datetime(2026, 10, 7, 8, 0), today="on", tomorrow="on", classes=("No class", "Not set")
    )

    assert "Child A: Art" in one
    assert "Child B" not in one
    assert "Special Class" not in none


async def test_wallboard_today_shows_tomorrows_school_info_before_a_school_day(
    hass: HomeAssistant, clock: Clock, display: dict[str, Any]
) -> None:
    sunday = await show_today(
        hass, clock, display, datetime(2026, 10, 11, 9, 0), today="off", tomorrow="on", lunch=("", "Roasted Chicken")
    )

    assert "School Day tomorrow" in sunday
    assert "Roasted Chicken" in sunday
    assert "Special Class" not in sunday


async def test_wallboard_today_is_hidden_when_neither_today_nor_tomorrow_is_a_school_day(
    hass: HomeAssistant, clock: Clock, display: dict[str, Any]
) -> None:
    saturday = await show_today(hass, clock, display, datetime(2026, 10, 10, 9, 0), today="off", tomorrow="off")

    assert saturday is None


async def test_wallboard_today_waits_overnight_for_family_to_update_the_school_day(
    hass: HomeAssistant, clock: Clock, display: dict[str, Any]
) -> None:
    # Until Family's helpers update in the morning, they still describe yesterday.
    overnight = await show_today(hass, clock, display, datetime(2026, 10, 7, 6, 0), today="on", tomorrow="on")
    morning = await show_today(hass, clock, display, datetime(2026, 10, 7, 6, 15), today="on", tomorrow="on")

    assert overnight is None
    assert "School Day" in morning


async def test_wallboard_countdowns_show_days_until_each_anticipated_event(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    countdowns = [
        {"title": "Flight out", "date": "2026-10-08", "days": 0},
        {"title": "Sam's birthday", "date": "2026-10-09", "days": 1},
        {"title": "Halloween", "date": "2026-10-31", "days": 23},
        {"title": "Beach trip", "date": "2026-11-20", "days": 43},
    ]
    hass.states.async_set(COUNTDOWNS, "4", {"countdowns": countdowns})

    shown = Template(countdowns_card(display)["content"], hass).async_render(parse_result=False)
    lines = [line for line in re.split(r"<br>|\n", shown) if line.strip()]

    assert len(lines) == 4
    assert ["Flight out", "Sam's birthday", "Halloween", "Beach trip"] == [
        next(title for title in ("Flight out", "Sam's birthday", "Halloween", "Beach trip") if title in line)
        for line in lines
    ]
    assert "Today" in lines[0]
    assert "Tomorrow" in lines[1]
    assert "23 days" in lines[2]
    assert "43 days" in lines[3]


def test_wallboard_countdowns_are_hidden_when_there_are_none(display: dict[str, Any]) -> None:
    card = countdowns_card(display)
    now = datetime(2026, 10, 7, 8, 0)

    assert not card_visible(card, {COUNTDOWNS: "0"}, now)
    assert card_visible(card, {COUNTDOWNS: "1"}, now)


# Glance band


def glance_section(display: dict[str, Any]) -> dict[str, Any]:
    return wallboard_home(display)["sections"][0]


def glance_card(display: dict[str, Any], card_type: str, marker: str = "") -> dict[str, Any]:
    [card] = [
        card
        for card in glance_section(display)["cards"]
        if card["type"] == card_type and marker in repr(card)
    ]
    return card


def render(hass: HomeAssistant, template: str) -> Any:
    return Template(template, hass).async_render()


def lines(rendered: str) -> list[str]:
    return [line.strip() for line in str(rendered).splitlines() if line.strip()]


def test_wallboard_glance_band_shows_the_time_and_date_with_madrid_and_london_clocks(display: dict[str, Any]) -> None:
    clock = glance_card(display, "custom:better-moment-card")

    assert [moment.get("timezone") for moment in clock["moment"]] == [None, None, "Europe/Madrid", "Europe/London"]
    assert [moment["format"] for moment in clock["moment"]][2:] == ["'Madrid' H:mm", "'London' H:mm"]


def test_wallboard_glance_band_shows_an_hourly_forecast_strip(display: dict[str, Any]) -> None:
    hourly = glance_card(display, "custom:hourly-weather")

    assert hourly["entity"] == "weather.example"
    assert hourly["forecast_type"] == "hourly"


NOW_NEXT = "sensor.wallboard_now_next"


def timed_event(title: str, start: datetime, end: datetime) -> dict[str, str]:
    zone = dt_util.get_default_time_zone()
    return {
        "title": title,
        "start": start.replace(tzinfo=zone).isoformat(),
        "end": end.replace(tzinfo=zone).isoformat(),
    }


async def now_next(hass: HomeAssistant, display: dict[str, Any], clock: Clock, at: datetime, events: list[dict[str, str]]) -> list[str]:
    await clock.move_to(at)
    hass.states.async_set(NOW_NEXT, str(len(events)), {"events": events})
    return lines(render(hass, glance_card(display, "markdown", NOW_NEXT)["content"]))


async def test_wallboard_now_next_shows_the_event_under_way_and_the_next_one(
    hass: HomeAssistant, display: dict[str, Any], clock: Clock
) -> None:
    shown = await now_next(
        hass,
        display,
        clock,
        datetime(2026, 10, 8, 14, 15),
        [
            timed_event("Lunch", datetime(2026, 10, 8, 12, 0), datetime(2026, 10, 8, 13, 0)),
            timed_event("Dentist", datetime(2026, 10, 8, 14, 0), datetime(2026, 10, 8, 15, 0)),
            timed_event("Soccer practice", datetime(2026, 10, 8, 15, 0), datetime(2026, 10, 8, 16, 0)),
            timed_event("Bake sale", datetime(2026, 10, 9, 8, 0), datetime(2026, 10, 9, 9, 0)),
        ],
    )

    assert shown == ["Now: Dentist · until 3:00", "Next: Soccer practice · in 45 min"]


async def test_wallboard_now_next_gives_the_time_of_a_next_event_an_hour_or_more_away(
    hass: HomeAssistant, display: dict[str, Any], clock: Clock
) -> None:
    shown = await now_next(
        hass,
        display,
        clock,
        datetime(2026, 10, 8, 14, 15),
        [timed_event("Curriculum night", datetime(2026, 10, 8, 18, 30), datetime(2026, 10, 8, 20, 0))],
    )

    assert shown == ["Next: Curriculum night · at 6:30"]


async def test_wallboard_now_next_shows_tomorrows_first_event_once_today_is_done(
    hass: HomeAssistant, display: dict[str, Any], clock: Clock
) -> None:
    shown = await now_next(
        hass,
        display,
        clock,
        datetime(2026, 10, 8, 21, 0),
        [
            timed_event("Soccer practice", datetime(2026, 10, 8, 17, 0), datetime(2026, 10, 8, 19, 0)),
            timed_event("Bake sale", datetime(2026, 10, 9, 8, 0), datetime(2026, 10, 9, 9, 0)),
            timed_event("Piano", datetime(2026, 10, 9, 16, 0), datetime(2026, 10, 9, 17, 0)),
        ],
    )

    assert shown == ["Nothing else today · Tomorrow: 8:00 Bake sale"]


async def test_wallboard_now_next_says_nothing_else_today_when_tomorrow_is_clear(
    hass: HomeAssistant, display: dict[str, Any], clock: Clock
) -> None:
    shown = await now_next(
        hass,
        display,
        clock,
        datetime(2026, 10, 8, 14, 15),
        [timed_event("Dentist", datetime(2026, 10, 8, 14, 0), datetime(2026, 10, 8, 15, 0))],
    )

    assert shown == ["Now: Dentist · until 3:00", "Nothing else today"]


async def test_wallboard_now_next_names_the_day_an_event_under_way_ends(
    hass: HomeAssistant, display: dict[str, Any], clock: Clock
) -> None:
    shown = await now_next(
        hass,
        display,
        clock,
        datetime(2026, 10, 8, 21, 0),
        [timed_event("Overnight camp", datetime(2026, 10, 8, 18, 0), datetime(2026, 10, 9, 10, 0))],
    )

    assert shown == ["Now: Overnight camp · until Fri 10:00", "Nothing else today"]


AQI = "sensor.example_outdoor_aqi"


async def current_weather(hass: HomeAssistant, display: dict[str, Any], aqi: str) -> str:
    hass.states.async_set("weather.example", "partlycloudy", {"temperature": 69, "temperature_unit": "°F"})
    hass.states.async_set(AQI, aqi)
    return str(render(hass, glance_card(display, "markdown", AQI)["content"]))


async def test_wallboard_glance_band_shows_current_weather_and_a_quiet_aqi(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    shown = await current_weather(hass, display, "41")

    assert "mdi:weather-partly-cloudy" in shown
    assert "69°" in shown
    assert "Partly cloudy" in shown
    assert "AQI 41" in shown
    assert "<font" not in shown


async def test_wallboard_glance_band_shows_aqi_in_red_above_100(hass: HomeAssistant, display: dict[str, Any]) -> None:
    assert '<font color="#db4437">AQI 101</font>' in await current_weather(hass, display, "101")
    assert "<font" not in await current_weather(hass, display, "100")


async def test_wallboard_glance_band_never_makes_aqi_an_attention_item(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    hass.states.async_set(AQI, "250")

    assert attention_items(hass, display) == []


HAZARDS = "binary_sensor.example_immediate_hazards"
EXTERIOR_DOORS = "binary_sensor.example_exterior_doors"
WASHER_FINISHED = "binary_sensor.wallboard_washer_finished_cycle"
DRYER_FINISHED = "binary_sensor.wallboard_dryer_finished_cycle"
BINS_OUT = "binary_sensor.wallboard_bins_out"


def attention_strip(display: dict[str, Any]) -> dict[str, Any]:
    return glance_card(display, "custom:auto-entities", HAZARDS)


def attention_items(hass: HomeAssistant, display: dict[str, Any]) -> list[dict[str, Any]]:
    return list(render(hass, attention_strip(display)["filter"]["template"]) or [])


def set_every_attention_item(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.example_smoke", "on", {"friendly_name": "Smoke"})
    hass.states.async_set("binary_sensor.example_leak", "off", {"friendly_name": "Leak"})
    hass.states.async_set(HAZARDS, "on", {"entity_id": ["binary_sensor.example_smoke", "binary_sensor.example_leak"]})
    hass.states.async_set("binary_sensor.example_front_door_open", "on", {"friendly_name": "Front Door"})
    hass.states.async_set("binary_sensor.example_deck_door_open", "on", {"friendly_name": "Deck Door"})
    hass.states.async_set(
        EXTERIOR_DOORS,
        "on",
        {"entity_id": ["binary_sensor.example_front_door_open", "binary_sensor.example_deck_door_open"]},
    )
    hass.states.async_set("cover.example_garage_door", "open")
    hass.states.async_set("binary_sensor.wallboard_unlocked_door", "on")
    hass.states.async_set("sensor.wallboard_unlocked_doors", "Front Door, Side Door")
    hass.states.async_set(WASHER_FINISHED, "on")
    hass.states.async_set(DRYER_FINISHED, "on")
    hass.states.async_set(BINS_OUT, "on", {"bins": "Recycle + Solid Waste"})


def test_wallboard_attention_strip_shows_nothing_but_keeps_its_row_when_empty(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    strip = attention_strip(display)

    assert strip["show_empty"] is True
    assert attention_items(hass, display) == []


def test_wallboard_attention_strip_lists_attention_items_most_severe_first(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    set_every_attention_item(hass)

    assert [item["content"] for item in attention_items(hass, display)] == [
        "Smoke",
        "Front Door, Deck Door open",
        "Garage Door open",
        "Front Door, Side Door unlocked",
        "Unload washer",
        "Unload dryer",
        "Bins out: Recycle + Solid Waste",
    ]


@pytest.mark.parametrize("state", ["open", "opening", "closing"])
def test_wallboard_garage_door_is_an_attention_item_until_it_is_closed(
    hass: HomeAssistant, display: dict[str, Any], state: str
) -> None:
    hass.states.async_set("cover.example_garage_door", state)

    assert [item["content"] for item in attention_items(hass, display)] == ["Garage Door open"]


def test_wallboard_a_tap_clears_a_finished_cycle_or_bins_out(hass: HomeAssistant, display: dict[str, Any]) -> None:
    set_every_attention_item(hass)

    taps = {item["content"]: item["tap_action"] for item in attention_items(hass, display)}

    assert {content: tap for content, tap in taps.items() if tap["action"] != "none"} == {
        content: {
            "action": "perform-action",
            "perform_action": "script.wallboard_acknowledge",
            "data": {"attention_item": attention_item},
        }
        for content, attention_item in [
            ("Unload washer", WASHER_FINISHED),
            ("Unload dryer", DRYER_FINISHED),
            ("Bins out: Recycle + Solid Waste", BINS_OUT),
        ]
    }


RUNNING = {
    "binary_sensor.wallboard_washer_active": "mdi:washing-machine",
    "binary_sensor.wallboard_dryer_active": "mdi:tumble-dryer",
    "binary_sensor.wallboard_dishwasher_active": "mdi:dishwasher",
}


def running_indicators(hass: HomeAssistant, display: dict[str, Any]) -> list[dict[str, Any]]:
    card = glance_card(display, "custom:auto-entities", "binary_sensor.wallboard_dishwasher_active")
    assert card["show_empty"] is False
    return list(render(hass, card["filter"]["template"]) or [])


def test_wallboard_shows_running_appliances_quietly_not_as_attention_items(
    hass: HomeAssistant, display: dict[str, Any]
) -> None:
    assert running_indicators(hass, display) == []

    for running in RUNNING:
        hass.states.async_set(running, "on")

    indicators = running_indicators(hass, display)
    assert [indicator["icon"] for indicator in indicators] == list(RUNNING.values())
    assert all(indicator["tap_action"] == {"action": "none"} for indicator in indicators)
    assert attention_items(hass, display) == []


def test_wallboard_glance_band_is_three_rows_whether_or_not_attention_items_show(display: dict[str, Any]) -> None:
    glance = glance_section(display)
    strips = [card for card in glance["cards"] if card["type"] == "custom:auto-entities"]
    others = [card for card in glance["cards"] if card not in strips]

    assert section_rows(glance) == 3
    assert packed_rows(others, grid_columns(glance)) == 2
    assert attention_strip(display)["show_empty"] is True


def test_wallboard_glance_band_cards_above_the_chips_share_one_height(display: dict[str, Any]) -> None:
    glance = glance_section(display)
    top = [card for card in glance["cards"] if card["type"] != "custom:auto-entities"]

    assert {card["grid_options"]["rows"] for card in top} == {2}
    assert sum(card["grid_options"]["columns"] for card in top) == grid_columns(glance)


def test_wallboard_glance_band_fills_the_screen_width(display: dict[str, Any]) -> None:
    glance = glance_section(display)
    columns = grid_columns(glance)

    assert packed_cells(glance["cards"], columns) == {(row, column) for row in range(3) for column in range(columns)}


async def test_wallboard_glance_band_cards_are_in_the_manifest(hass: HomeAssistant, display: dict[str, Any]) -> None:
    set_every_attention_item(hass)

    assert_custom_cards_listed({"cards": [attention_strip(display)] + attention_items(hass, display)}, CUSTOM_CARDS)
