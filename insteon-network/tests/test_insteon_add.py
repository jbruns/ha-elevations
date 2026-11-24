from pathlib import Path

import pytest
from insteon_fakes import FakeHa, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, USER
from insteon_fakes import eisy_ssh, fake_ha, fake_iox, fake_ssh, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon.add import AddResult, add, render
from insteon.ha import HaClient
from insteon.iox import IoxClient, IoxError, IoxFiles
from insteon.rails import IoxBackup, Rails
from insteon.resolve import ResolveError

NEW = (
    '<node flag="128" nodeDefId="DimmerLampSwitch_ADV"><address>7A 7B 7C 1</address><name>Example New Dimmer</name>'
    "<type>1.32.65.0</type><enabled>true</enabled><pnode>7A 7B 7C 1</pnode></node>"
)


@pytest.fixture
def addable(fake_iox: FakeIox, fake_ha: FakeHa) -> FakeIox:
    fake_iox.addable["7A 7B 7C"] = NEW
    fake_ha.entities.append(
        {"entity_id": "light.example_new_dimmer", "unique_id": "00:00:00:00:00:00_7A 7B 7C 1", "platform": "isy994", "device_id": None}
    )
    return fake_iox


async def run(
    iox_url: str, ha_url: str, ssh: SshEndpoint, tmp_path: Path, address: str = "7a.7b.7c", *, apply: bool, answers=None, **options
) -> AddResult:
    files = IoxFiles("127.0.0.1", SSH_USER, SSH_PASSWORD, port=ssh.port, known_hosts=ssh.known_hosts)
    rails = Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")
    replies = list(answers or [])
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return await add(
            iox, ha, rails, IoxBackup(files, tmp_path / "eisy" / "backups"), address, "Example New Dimmer",
            prompt=lambda message: replies.pop(0) if replies else "", **options,
        )


async def test_a_dry_run_says_it_would_erase_the_links_and_writes_nothing(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, addable: FakeIox, fake_ha: FakeHa, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, apply=False)

    assert addable.writes() == [] and fake_ha.writes() == []
    assert "Would add 7A.7B.7C Example New Dimmer, erasing its Links." in render(result)


async def test_adding_erases_its_links_by_default_then_reloads_ha_and_prints_its_entities(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, addable: FakeIox, fake_ha: FakeHa, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, apply=True)

    assert addable.writes() == [("AddNode", {"id": "7A 7B 7C", "name": "Example New Dimmer", "type": "", "flag": "1"})]
    assert fake_ha.writes()[0]["service"] == "reload_config_entry"
    assert "Its HA entities: light.example_new_dimmer" in render(result)


async def test_building_from_its_links_is_only_on_request(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, addable: FakeIox, tmp_path: Path
) -> None:
    await run(iox_url, ha_url, eisy_ssh, tmp_path, apply=True, from_links=True)

    assert addable.writes()[0][1]["flag"] == "3"


async def test_an_address_the_controller_already_has_is_refused(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, addable: FakeIox, tmp_path: Path
) -> None:
    with pytest.raises(ResolveError, match="already has AA.BB.CC: Example Dimmer"):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, "AA.BB.CC", apply=True)

    assert addable.writes() == []


async def test_a_device_iox_could_not_add_fails_before_ha_is_touched(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, tmp_path: Path
) -> None:
    with pytest.raises(IoxError, match="IoX did not add 7A.7B.7C"):
        await run(iox_url, ha_url, eisy_ssh, tmp_path, apply=True)

    assert fake_ha.writes() == []


async def test_a_battery_device_is_added_only_once_the_administrator_holds_set(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, addable: FakeIox, tmp_path: Path
) -> None:
    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, apply=True, battery=True, answers=["s"])

    assert addable.writes() == []
    assert "was not added" in render(result)
