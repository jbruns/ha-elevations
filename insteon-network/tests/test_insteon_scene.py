from pathlib import Path

import pytest
from insteon_fakes import FakeHa, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, USER
from insteon_fakes import eisy_ssh, fake_ha, fake_iox, fake_ssh, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon import scene as scene_command
from insteon.ha import HaClient
from insteon.iox import IoxClient, IoxFiles
from insteon.rails import IoxBackup, Rails
from insteon.resolve import ResolveError


async def run(
    iox_url: str, ha_url: str, ssh: SshEndpoint, tmp_path: Path, planner, *, apply: bool, answers: list[str] | None = None
) -> scene_command.SceneResult:
    files = IoxFiles("127.0.0.1", SSH_USER, SSH_PASSWORD, port=ssh.port, known_hosts=ssh.known_hosts)
    rails = Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")
    replies = list(answers or [])
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return await scene_command.scene(
            iox, ha, rails, IoxBackup(files, tmp_path / "eisy" / "backups"), planner,
            prompt=lambda message: replies.pop(0) if replies else "",
        )


async def test_a_dry_run_says_what_would_change_and_writes_nothing(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, tmp_path: Path
) -> None:
    result = await run(
        iox_url, ha_url, eisy_ssh, tmp_path, scene_command.add_button("Example Scene", "11 22 33 4", controller=False), apply=False
    )

    assert fake_iox.writes() == [] and fake_ha.writes() == []
    text = scene_command.render(result)
    assert "Would: Add 11.22.33 4 Example Keypad B to Example Scene (12345) as a Responder Button" in text
    assert "Dry run: nothing changed." in text


async def test_adding_a_responder_button_moves_it_into_the_scene_then_writes_every_member(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    await run(
        iox_url, ha_url, eisy_ssh, tmp_path, scene_command.add_button("12345", "Example Keypad B", controller=False), apply=True
    )

    assert fake_iox.writes() == [
        ("MoveNode", {"node": "11 22 33 4", "group": "12345", "flag": "32"}),
        ("WriteDeviceUpdates", {"node": "A B C 1"}),
        ("WriteDeviceUpdates", {"node": "11 22 33 1"}),
        ("WriteDeviceUpdates", {"node": "AA BB CC 1"}),
    ]
    assert (tmp_path / "eisy" / "backups").exists()


async def test_a_button_that_controls_one_scene_cannot_control_another(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    with pytest.raises(ResolveError, match="already controls Example Scene"):
        await run(
            iox_url, ha_url, eisy_ssh, tmp_path, scene_command.add_button("Example Empty Scene", "11 22 33 3", controller=True),
            apply=True,
        )

    assert fake_iox.writes() == []


async def test_removing_a_button_takes_it_out_and_writes_the_members(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    await run(iox_url, ha_url, eisy_ssh, tmp_path, scene_command.remove_button("Example Three-Way", "Example Dimmer"), apply=True)

    assert fake_iox.writes() == [
        ("RemoveFromGroup", {"node": "AA BB CC 1", "group": "23456"}),
        ("WriteDeviceUpdates", {"node": "11 22 33 1"}),
        ("WriteDeviceUpdates", {"node": "AA BB CC 1"}),
    ]


async def test_a_responder_level_is_set_for_the_scene_as_a_byte_with_its_ramp_rate(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    await run(iox_url, ha_url, eisy_ssh, tmp_path, scene_command.level("Example Scene", "AA.BB.CC", 50, ramp=28), apply=True)

    assert fake_iox.writes() == [
        ("SetSceneProfile", {"node": "AA BB CC 1", "controller": "12345", "control": "OL", "action": "128"}),
        ("SetSceneProfile", {"node": "AA BB CC 1", "controller": "12345", "control": "RR", "action": "28"}),
        ("WriteDeviceUpdates", {"node": "AA BB CC 1"}),
    ]


async def test_a_responder_level_can_be_set_for_one_controller_button(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    await run(
        iox_url, ha_url, eisy_ssh, tmp_path,
        scene_command.level("Example Scene", "A B C 1", 100, controller_name="Example Keypad A"), apply=True,
    )

    assert fake_iox.writes()[0] == (
        "SetSceneProfile", {"node": "A B C 1", "controller": "11 22 33 3", "control": "OL", "action": "255"}
    )


async def test_creating_a_scene_adds_it_then_reconciles_ha_and_names_its_switch(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, tmp_path: Path
) -> None:
    fake_ha.entities.append(
        {"entity_id": "switch.example_new", "unique_id": "00:00:00:00:00:00_34568", "platform": "isy994", "device_id": None}
    )

    result = await run(iox_url, ha_url, eisy_ssh, tmp_path, scene_command.create("Example New"), apply=True)

    assert fake_iox.writes() == [("AddGroup", {"id": "34568", "name": "Example New", "flag": "4"})]
    assert fake_ha.writes()[0]["service"] == "reload_config_entry"
    assert "Its HA switch: switch.example_new" in scene_command.render(result)


async def test_deleting_a_scene_removes_it_and_writes_its_members(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, tmp_path: Path
) -> None:
    await run(iox_url, ha_url, eisy_ssh, tmp_path, scene_command.delete("Example Three-Way"), apply=True)

    assert fake_iox.writes() == [
        ("RemoveGroup", {"id": "23456"}),
        ("WriteDeviceUpdates", {"node": "11 22 33 1"}),
        ("WriteDeviceUpdates", {"node": "AA BB CC 1"}),
    ]
    assert fake_ha.writes()[0]["service"] == "reload_config_entry"
