"""Media players available to blueprint tests."""

from homeassistant.core import HomeAssistant

from testing.media import MediaPlayers


async def test_a_media_player_records_stop_and_pause(
    hass: HomeAssistant, media_players: MediaPlayers
) -> None:
    await media_players.player.set("playing", app_name="Kids")

    await hass.services.async_call(
        "media_player",
        "media_pause",
        {"entity_id": media_players.player.entity_id},
        blocking=True,
    )
    await hass.services.async_call(
        "media_player",
        "media_stop",
        {"entity_id": media_players.player.entity_id},
        blocking=True,
    )

    assert media_players.player.calls == [("media_pause", {}), ("media_stop", {})]
    assert hass.states.get(media_players.player.entity_id).state == "idle"
    assert (
        hass.states.get(media_players.player.entity_id).attributes["app_name"] == "Kids"
    )
