"""Media players whose playback and service calls tests can inspect."""

from dataclasses import dataclass
from typing import Any

import pytest
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform


def _call_data(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if k != "entity_id"}


class SettableMediaPlayer(MediaPlayerEntity):
    _attr_should_poll = False
    _attr_supported_features = (
        MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, entity_id: str, name: str) -> None:
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_unique_id = entity_id
        self._attr_state = "idle"
        self._extra: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra

    async def set(self, state: str, **attributes: Any) -> None:
        self._attr_state = state
        self._extra = attributes
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        self.calls.append(("media_pause", {}))
        self._attr_state = "paused"
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        self.calls.append(("media_stop", {}))
        self._attr_state = "idle"
        self.async_write_ha_state()


@dataclass
class MediaPlayers:
    player: SettableMediaPlayer
    other_player: SettableMediaPlayer
    non_kids_player: SettableMediaPlayer


@pytest.fixture
async def media_players(hass: HomeAssistant) -> MediaPlayers:
    player = SettableMediaPlayer("media_player.example_player", "Example Player")
    other_player = SettableMediaPlayer(
        "media_player.example_other_player", "Example Other Player"
    )
    non_kids_player = SettableMediaPlayer(
        "media_player.example_non_kids_player", "Example Non-Kids Player"
    )
    setup_test_component_platform(
        hass, "media_player", [player, other_player, non_kids_player]
    )
    assert await async_setup_component(
        hass, "media_player", {"media_player": {"platform": "test"}}
    )
    await hass.async_block_till_done()
    return MediaPlayers(player, other_player, non_kids_player)
