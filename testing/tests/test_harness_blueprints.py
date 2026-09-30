"""Any context's blueprint can be loaded by its repo path."""

import pytest
from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations, blueprint_automation
from testing.phones import Phone


async def test_a_blueprint_loads_from_its_repo_path(
    hass: HomeAssistant, recipient: Phone
) -> None:
    hass.states.async_set("binary_sensor.example_doorbell", "off")
    await async_setup_automations(
        hass,
        [
            blueprint_automation(
                "front-door/blueprints/automation/front_door_doorbell_press_notifications.yaml",
                {
                    "doorbell": "binary_sensor.example_doorbell",
                    "camera": "camera.example",
                    "recipients": [recipient.device_id],
                },
            )
        ],
    )

    hass.states.async_set("binary_sensor.example_doorbell", "on")
    await hass.async_block_till_done()

    [notification] = recipient.notifications
    assert notification["message"] == "Someone pressed the doorbell"


def test_a_path_outside_any_contexts_blueprints_is_refused() -> None:
    with pytest.raises(ValueError, match="not a blueprint"):
        blueprint_automation("dashboards/our-home/views/front-door.yaml", {})
