"""The sun fixture lets blueprint tests drive sun elevation triggers."""

from homeassistant.core import HomeAssistant

from testing.sun import Sun


async def test_a_test_sets_sun_elevation(hass: HomeAssistant, sun: Sun) -> None:
    await sun.set_elevation(-3.5)

    state = hass.states.get(sun.entity_id)
    assert state.state == "below_horizon"
    assert state.attributes["elevation"] == -3.5
