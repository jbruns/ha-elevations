import json
from pathlib import Path

import pytest
from insteon_fakes import FakeHa, PASSWORD, TOKEN, USER
from insteon_fakes import fake_ha, fake_iox, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon.ha import HaClient
from insteon.iox import IoxClient
from insteon.prune import prune
from insteon.rails import LockHeld, Rails, lock

STALE = ["gone", "old-hub", "old-dimmer", "old-network"]


def rails(tmp_path: Path, *, apply: bool) -> Rails:
    return Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "backups")


async def run(iox_url: str, ha_url: str, tmp_path: Path, *, apply: bool):
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return await prune(iox, ha, rails(tmp_path, apply=apply))


async def test_a_dry_run_finds_the_stale_ha_devices_and_removes_nothing(
    iox_url: str, ha_url: str, fake_ha: FakeHa, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, tmp_path, apply=False)

    assert [device.device_id for device in result.removable] == STALE
    assert "gone-with-entities" in [device.device_id for device in result.stale]
    assert fake_ha.removed() == []
    assert not (tmp_path / "backups").exists()


async def test_applying_removes_exactly_the_stale_ha_devices(
    iox_url: str, ha_url: str, fake_ha: FakeHa, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, tmp_path, apply=True)

    assert fake_ha.removed() == STALE
    assert [device.device_id for device in result.removed] == STALE
    assert {device["id"] for device in fake_ha.devices} == {
        "hub",
        "variables",
        "dimmer",
        "relay",
        "keypad",
        "zwave-lock",
        "gone-with-entities",
        "hue-bulb",
    }


async def test_devices_from_another_controller_are_stale_even_at_a_live_address(
    iox_url: str, ha_url: str, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, tmp_path, apply=False)

    old = next(device for device in result.stale if device.device_id == "old-dimmer")
    assert "another Controller" in old.reason


async def test_a_stale_ha_device_that_still_has_entities_is_held_back(
    iox_url: str, ha_url: str, fake_ha: FakeHa, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, tmp_path, apply=True)

    assert [(device.device_id, device.entity_ids) for device in result.held] == [
        ("gone-with-entities", ("sensor.example_gone",))
    ]
    assert "gone-with-entities" not in fake_ha.removed()


async def test_applying_backs_up_the_registries_before_removing(
    iox_url: str, ha_url: str, tmp_path: Path
) -> None:
    await run(iox_url, ha_url, tmp_path, apply=True)

    (folder,) = (tmp_path / "backups").iterdir()
    devices = json.loads((folder / "ha-devices.json").read_text())
    assert {"gone", "old-hub"} <= {device["id"] for device in devices}


async def test_applying_refuses_while_another_command_holds_the_lock(
    iox_url: str, ha_url: str, fake_ha: FakeHa, tmp_path: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "remove"), pytest.raises(LockHeld, match="insteon remove"):
        await run(iox_url, ha_url, tmp_path, apply=True)

    assert fake_ha.removed() == []


async def test_nothing_to_prune_takes_no_lock_or_backup(
    iox_url: str, ha_url: str, fake_ha: FakeHa, tmp_path: Path
) -> None:
    fake_ha.devices = [device for device in fake_ha.devices if device["id"] not in STALE]

    result = await run(iox_url, ha_url, tmp_path, apply=True)

    assert result.removed == []
    assert not (tmp_path / "backups").exists()
