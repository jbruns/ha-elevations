"""Behaviour of the Screen Time blueprint, run in a real Home Assistant core."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.clock import Clock
from testing.helpers import Helpers
from testing.media import MediaPlayers, SettableMediaPlayer

BLUEPRINT = "family/blueprints/automation/family_screen_time.yaml"
MONDAY = datetime(2026, 9, 7, 9)
MINUTE = timedelta(minutes=1)


@dataclass
class ScreenTime:
    hass: HomeAssistant
    clock: Clock
    players: MediaPlayers
    today_school_day: str
    tomorrow_school_day: str
    daily_limit: str
    timer: str
    limit_reached: str

    async def start(self, **inputs: Any) -> None:
        await async_setup_automations(
            self.hass,
            [
                blueprint_automation(
                    BLUEPRINT,
                    {
                        "players": [
                            self.players.player.entity_id,
                            self.players.other_player.entity_id,
                            self.players.non_kids_player.entity_id,
                        ],
                        "session_user": "Kids",
                        "today_school_day": self.today_school_day,
                        "tomorrow_school_day": self.tomorrow_school_day,
                        "daily_limit": self.daily_limit,
                        "screen_time_timer": self.timer,
                        "limit_reached": self.limit_reached,
                        **inputs,
                    },
                    alias="Screen Time",
                )
            ],
        )

    async def school_days(self, *, today: bool, tomorrow: bool) -> None:
        for entity_id, on in (
            (self.today_school_day, today),
            (self.tomorrow_school_day, tomorrow),
        ):
            await self.hass.services.async_call(
                "input_boolean",
                "turn_on" if on else "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )

    async def limit_is_reached(self) -> None:
        await self.hass.services.async_call(
            "input_boolean",
            "turn_on",
            {"entity_id": self.limit_reached},
            blocking=True,
        )

    async def play(
        self, player: SettableMediaPlayer, *, account: str = "Kids"
    ) -> None:
        await player.set("playing", app_name=account)
        await self.hass.async_block_till_done()

    async def stop(self, player: SettableMediaPlayer) -> None:
        await player.set("idle", app_name=player.extra_state_attributes.get("app_name"))
        await self.hass.async_block_till_done()

    async def at(self, day: datetime) -> None:
        await self.clock.move_to(day)

    async def wait(self, minutes: float) -> None:
        await self.clock.advance(timedelta(minutes=minutes), step=MINUTE / 2)

    @property
    def limit(self) -> bool:
        return self.hass.states.get(self.limit_reached).state == "on"

    @property
    def timer_state(self) -> str:
        return self.hass.states.get(self.timer).state


@pytest.fixture
async def screen_time(
    hass: HomeAssistant,
    clock: Clock,
    helpers: Helpers,
    media_players: MediaPlayers,
) -> ScreenTime:
    today = await helpers.input_boolean("Example School Day Today")
    tomorrow = await helpers.input_boolean("Example School Day Tomorrow")
    daily_limit = await helpers.input_number(
        "Example Daily Limit", initial=2, minimum=1, maximum=480
    )
    timer = await helpers.timer("Example Screen Time")
    limit_reached = await helpers.input_boolean("Example Daily Limit Reached")
    await clock.move_to(MONDAY)
    screen_time = ScreenTime(
        hass, clock, media_players, today, tomorrow, daily_limit, timer, limit_reached
    )
    await screen_time.school_days(today=False, tomorrow=True)
    return screen_time


async def test_screen_time_counts_across_two_players(
    screen_time: ScreenTime,
) -> None:
    await screen_time.start()

    await screen_time.play(screen_time.players.player)
    await screen_time.wait(1)
    await screen_time.play(screen_time.players.other_player)
    await screen_time.stop(screen_time.players.player)
    await screen_time.wait(1.1)

    assert screen_time.limit is True
    assert screen_time.players.other_player.calls == [("media_stop", {})]


async def test_screen_time_pauses_when_nothing_plays(screen_time: ScreenTime) -> None:
    await screen_time.start()

    await screen_time.play(screen_time.players.player)
    await screen_time.wait(1)
    await screen_time.stop(screen_time.players.player)
    await screen_time.wait(2)

    assert screen_time.limit is False
    assert screen_time.timer_state == "paused"


async def test_playback_is_blocked_at_the_daily_limit(
    screen_time: ScreenTime,
) -> None:
    await screen_time.limit_is_reached()
    await screen_time.start()

    await screen_time.play(screen_time.players.player)

    assert screen_time.players.player.calls == [("media_stop", {})]


async def test_daily_reset_clears_the_daily_limit(screen_time: ScreenTime) -> None:
    await screen_time.at(MONDAY.replace(hour=23, minute=59))
    await screen_time.start()
    await screen_time.hass.services.async_call(
        "timer",
        "start",
        {"entity_id": screen_time.timer, "duration": "00:10:00"},
        blocking=True,
    )
    await screen_time.limit_is_reached()

    await screen_time.wait(1.1)

    assert screen_time.limit is False
    assert screen_time.timer_state == "idle"


async def test_no_play_outside_the_viewing_window(screen_time: ScreenTime) -> None:
    await screen_time.at(MONDAY.replace(hour=7, minute=59))
    await screen_time.start()

    await screen_time.play(screen_time.players.player)

    assert screen_time.players.player.calls == [("media_stop", {})]


async def test_a_holiday_monday_gets_the_day_viewing_window(
    screen_time: ScreenTime,
) -> None:
    await screen_time.school_days(today=False, tomorrow=True)
    await screen_time.at(MONDAY.replace(hour=9))
    await screen_time.start()

    await screen_time.play(screen_time.players.player)

    assert screen_time.players.player.calls == []
    assert screen_time.timer_state == "active"


async def test_the_evening_before_a_holiday_gets_the_evening_viewing_window(
    screen_time: ScreenTime,
) -> None:
    await screen_time.school_days(today=True, tomorrow=False)
    await screen_time.at(MONDAY.replace(hour=17, minute=30))
    await screen_time.start()

    await screen_time.play(screen_time.players.player)

    assert screen_time.players.player.calls == []
    assert screen_time.timer_state == "active"


async def test_monday_through_thursday_school_nights_have_no_viewing_window(
    screen_time: ScreenTime,
) -> None:
    await screen_time.school_days(today=True, tomorrow=True)
    await screen_time.at(MONDAY.replace(hour=17, minute=30))
    await screen_time.start()

    await screen_time.play(screen_time.players.player)

    assert screen_time.players.player.calls == [("media_stop", {})]


async def test_playing_through_the_evening_window_end_is_stopped(
    screen_time: ScreenTime,
) -> None:
    await screen_time.school_days(today=True, tomorrow=False)
    await screen_time.at(MONDAY.replace(hour=19, minute=59))
    await screen_time.start()
    await screen_time.play(screen_time.players.player)

    await screen_time.wait(1.1)

    assert screen_time.players.player.calls == [("media_stop", {})]
    assert screen_time.timer_state == "paused"


async def test_non_school_day_playing_through_eight_pm_is_stopped(
    screen_time: ScreenTime,
) -> None:
    await screen_time.school_days(today=False, tomorrow=True)
    await screen_time.at(MONDAY.replace(hour=19, minute=59))
    await screen_time.start()
    await screen_time.play(screen_time.players.player)

    await screen_time.wait(1.1)

    assert screen_time.players.player.calls == [("media_stop", {})]
    assert screen_time.timer_state == "paused"


async def test_non_school_day_uses_the_day_window_end_not_the_evening_end(
    screen_time: ScreenTime,
) -> None:
    await screen_time.school_days(today=False, tomorrow=True)
    await screen_time.at(MONDAY.replace(hour=9, minute=59))
    await screen_time.start(day_window_end="10:00:00", evening_window_end="20:00:00")
    await screen_time.play(screen_time.players.player)

    await screen_time.wait(1.1)

    assert screen_time.players.player.calls == [("media_stop", {})]
    assert screen_time.timer_state == "paused"


async def test_another_users_playback_is_never_stopped_or_counted(
    screen_time: ScreenTime,
) -> None:
    await screen_time.at(MONDAY.replace(hour=19, minute=59))
    await screen_time.start()
    await screen_time.play(screen_time.players.non_kids_player, account="Guest")

    await screen_time.wait(1.1)

    assert screen_time.players.non_kids_player.calls == []
    assert screen_time.timer_state == "idle"
