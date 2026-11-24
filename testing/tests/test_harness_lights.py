"""Lights and switches available to blueprint tests."""

from homeassistant.core import HomeAssistant

from testing.lights import KeypadLights, Lights


async def call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)


async def test_a_light_records_turn_on_and_turn_off(
    hass: HomeAssistant, lights: Lights
) -> None:
    await call(hass, "light", "turn_on", {"entity_id": lights.follower.entity_id, "brightness": 101})
    assert hass.states.get(lights.follower.entity_id).state == "on"
    assert hass.states.get(lights.follower.entity_id).attributes["brightness"] == 101

    await call(hass, "light", "turn_off", {"entity_id": lights.follower.entity_id})

    assert lights.follower.calls == [("turn_on", {"brightness": 101}), ("turn_off", {})]
    assert hass.states.get(lights.follower.entity_id).state == "off"


async def test_a_test_sets_light_and_switch_state_without_recording_calls(
    hass: HomeAssistant, lights: Lights
) -> None:
    await lights.leader_light.set(on=True, brightness=212)
    await lights.leader_switch.set(on=True)

    assert hass.states.get(lights.leader_light.entity_id).state == "on"
    assert hass.states.get(lights.leader_light.entity_id).attributes["brightness"] == 212
    assert hass.states.get(lights.leader_switch.entity_id).state == "on"
    assert lights.leader_light.calls == []
    assert lights.leader_switch.calls == []


async def test_a_scene_switch_reports_whether_all_its_members_are_on(
    hass: HomeAssistant, keypad_lights: KeypadLights
) -> None:
    scene = keypad_lights.keypad_sync_scene

    await scene.set(on=True, all_on=False)
    assert hass.states.get(scene.entity_id).state == "on"
    assert hass.states.get(scene.entity_id).attributes["group_all_on"] == "off"

    await call(hass, "switch", "turn_on", {"entity_id": scene.entity_id})
    assert hass.states.get(scene.entity_id).attributes["group_all_on"] == "on"

    await call(hass, "switch", "turn_off", {"entity_id": scene.entity_id})
    assert hass.states.get(scene.entity_id).state == "off"
    assert hass.states.get(scene.entity_id).attributes["group_all_on"] == "off"
    assert scene.calls == [("turn_on", {}), ("turn_off", {})]


async def test_a_light_can_be_made_unavailable(
    hass: HomeAssistant, keypad_lights: KeypadLights
) -> None:
    light = keypad_lights.light
    await light.set(on=True, available=False)
    assert hass.states.get(light.entity_id).state == "unavailable"

    await light.set(on=True)
    assert hass.states.get(light.entity_id).state == "on"
