import re
from datetime import datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

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
        ("on", 8): ["Family Reminders", "Shopping List"],
        ("on", 15): ["After-School Tasks", "Shopping List"],
        ("off", 8): ["Family Reminders", "Shopping List"],
        ("off", 15): ["Family Reminders", "Shopping List"],
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


def test_wallboard_rail_orders_today_countdowns_chores_then_lists(display: dict[str, Any]) -> None:
    cards = rail_section(display)["cards"]

    assert cards[:3] == [today_card(display), countdowns_card(display), chores_card(display)]
    assert [card["type"] for card in cards[3:]] == ["todo-list"] * 3


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
