"""Tests set how many persons are home, which zone.home counts."""

from homeassistant.core import HomeAssistant

from testing.automations import async_setup_automations
from testing.household import Household
from testing.phones import Phone


async def test_zone_home_counts_the_persons_home(
    hass: HomeAssistant, household: Household
) -> None:
    await household.set_home(2)
    assert hass.states.get("zone.home").state == "2"

    await household.set_home(0)
    assert hass.states.get("zone.home").state == "0"


async def test_the_last_person_leaving_triggers_on_zone_home(
    hass: HomeAssistant, household: Household, recipient: Phone
) -> None:
    await household.set_home(1)
    await async_setup_automations(
        hass,
        [
            {
                "alias": "Everyone left",
                "triggers": [{"trigger": "state", "entity_id": "zone.home", "to": "0"}],
                "actions": [{"action": recipient.notify, "data": {"message": "away"}}],
            }
        ],
    )

    await household.set_home(0)

    assert len(recipient.notifications) == 1
