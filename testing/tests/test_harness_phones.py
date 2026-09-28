"""Recipient and Administrator phones capture the Notifications sent to them."""

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations
from testing.phones import Phone


def notify_by_device(action_data: dict) -> dict:
    """An automation that notifies the phone a blueprint would resolve from a device."""
    return {
        "alias": "Notify",
        "triggers": [{"trigger": "event", "event_type": "example_notify"}],
        "variables": {"device": "{{ trigger.event.data.device_id }}"},
        "actions": [
            {
                "action": "notify.mobile_app_{{ device_attr(device, 'name') | slugify }}",
                "data": action_data,
            }
        ],
    }


async def test_a_recipient_captures_a_critical_notification(
    hass: HomeAssistant, recipient: Phone
) -> None:
    await async_setup_automations(
        hass,
        [
            notify_by_device(
                {
                    "title": "Leak",
                    "message": "Water under the sink",
                    "data": {
                        "tag": "leak",
                        "push": {"sound": {"name": "default", "critical": 1, "volume": 1.0}},
                    },
                }
            )
        ],
    )

    hass.bus.async_fire("example_notify", {"device_id": recipient.device_id})
    await hass.async_block_till_done()

    [notification] = recipient.notifications
    assert notification["message"] == "Water under the sink"
    assert notification["data"]["tag"] == "leak"
    assert notification["data"]["push"]["sound"]["critical"] == 1


async def test_only_the_administrator_captures_a_silent_update(
    hass: HomeAssistant, recipient: Phone, administrator: Phone
) -> None:
    await async_setup_automations(
        hass, [notify_by_device({"message": "clear_notification", "data": {"tag": "ups"}})]
    )

    hass.bus.async_fire("example_notify", {"device_id": administrator.device_id})
    await hass.async_block_till_done()

    assert administrator.notifications == [
        {"message": "clear_notification", "data": {"tag": "ups"}}
    ]
    assert recipient.notifications == []


async def test_each_phone_has_its_own_notify_action(
    recipient: Phone, other_recipient: Phone, administrator: Phone
) -> None:
    assert [recipient.notify, other_recipient.notify, administrator.notify] == [
        "notify.mobile_app_test_phone",
        "notify.mobile_app_other_phone",
        "notify.mobile_app_admin_phone",
    ]
