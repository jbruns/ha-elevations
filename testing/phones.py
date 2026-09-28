"""Household phones: mobile_app devices whose Notifications are captured.

A blueprint takes phones as device inputs and notifies each one through
notify.mobile_app_<phone>. Every name here is a placeholder (ADR 0004).
"""

from dataclasses import dataclass
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)


@dataclass
class Phone:
    device_id: str
    notify: str
    calls: list[ServiceCall]

    @property
    def notifications(self) -> list[dict[str, Any]]:
        """Each notify call's data, such as message, title and data.push or data.tag."""
        return [dict(call.data) for call in self.calls]


def add_phone(hass: HomeAssistant, name: str) -> Phone:
    slug = slugify(name)
    entry = MockConfigEntry(domain="mobile_app", data={"device_name": name})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mobile_app", slug)},
        name=name,
    )
    calls = async_mock_service(hass, "notify", f"mobile_app_{slug}")
    return Phone(device.id, f"notify.mobile_app_{slug}", calls)


@pytest.fixture
async def recipient(hass: HomeAssistant) -> Phone:
    return add_phone(hass, "Test Phone")


@pytest.fixture
async def other_recipient(hass: HomeAssistant) -> Phone:
    """A second household phone; add it to an automation's recipients to use it."""
    return add_phone(hass, "Other Phone")


@pytest.fixture
async def administrator(hass: HomeAssistant) -> Phone:
    """The Administrator's phone, the only one Infrastructure notifies (ADR 0006)."""
    return add_phone(hass, "Admin Phone")
