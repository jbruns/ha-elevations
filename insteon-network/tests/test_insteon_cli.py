import subprocess
from pathlib import Path

import pytest
from insteon_fakes import FakeHa, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, USER
from insteon_fakes import eisy_ssh, fake_ha, fake_iox, fake_ssh, ha_url, iox_url  # noqa: F401 (fixtures)
from yarl import URL

from insteon.cli import run


@pytest.fixture
def private(tmp_path: Path, iox_url: str, ha_url: str) -> Path:
    iox = URL(iox_url)
    (tmp_path / "eisy").mkdir()
    (tmp_path / "eisy" / "iox.local.env").write_text(
        f"IOX_HOST={iox.host}\nIOX_PORT={iox.port}\nIOX_PROTO=http\nIOX_USER_NAME={USER}\nIOX_PASSWORD={PASSWORD}\n"
    )
    (tmp_path / "ha-url").write_text(ha_url + "\n")
    (tmp_path / "ha-token").write_text(TOKEN + "\n")
    return tmp_path


async def test_inventory_prints_the_network(private: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run(["--private-dir", str(private), "inventory", "--no-modem-links"]) == 0

    out = capsys.readouterr().out
    assert "Example Keypad" in out and "Example Scene (12345)" in out


async def test_inventory_resolves_an_ha_entity_through_home_assistant(
    private: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "inventory", "--no-modem-links", "light.example_relay"]) == 0

    out = capsys.readouterr().out
    assert "Example Relay" in out and "Example Keypad  " not in out


async def test_prune_is_a_dry_run_without_apply(
    private: Path, fake_ha: FakeHa, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "prune"]) == 0

    out = capsys.readouterr().out
    assert "Dry run" in out and "--apply" in out
    assert "from another Controller" in out
    assert "held: 1 entity still uses it (sensor.example_gone)" in out
    assert fake_ha.removed() == []


async def test_prune_with_apply_removes_the_stale_ha_devices(
    private: Path, fake_ha: FakeHa, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "prune", "--apply"]) == 0

    out = capsys.readouterr().out
    assert "Removed 4 Stale HA Devices" in out
    assert "Backup:" in out
    assert len(fake_ha.removed()) == 4


async def test_missing_credentials_fail_with_the_path_and_no_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(tmp_path), "inventory"]) == 1

    err = capsys.readouterr().err
    assert "iox.local.env not found" in err


async def test_a_device_the_controller_lacks_fails_plainly(
    private: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "inventory", "--no-modem-links", "33.44.55"]) == 1

    assert "no Device at 33.44.55" in capsys.readouterr().err


async def test_the_private_folder_can_come_from_the_environment(
    private: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INSTEON_PRIVATE_DIR", str(private))

    assert await run(["inventory", "--no-modem-links"]) == 0
    assert "Example Dimmer" in capsys.readouterr().out


# insteon programs ------------------------------------------------------------


@pytest.fixture
def programs_private(private: Path, eisy_ssh: SshEndpoint) -> Path:
    """The private folder with the Controller's SSH login, and its host key pinned there."""
    with (private / "eisy" / "iox.local.env").open("a") as env:
        env.write(f"IOX_SSH_USER={SSH_USER}\nIOX_SSH_PASSWORD={SSH_PASSWORD}\nIOX_SSH_PORT={eisy_ssh.port}\n")
    (private / "eisy" / "known_hosts").write_text(eisy_ssh.known_hosts.read_text())
    return private


async def test_programs_prints_every_program_and_variable(
    programs_private: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(programs_private), "programs"]) == 0

    out = capsys.readouterr().out
    assert "IoX programs: 3 programs in 2 folders" in out
    assert "IoX variables: 1 integer, 2 state" in out


async def test_programs_without_the_ssh_login_fails_naming_the_missing_settings(
    private: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "programs"]) == 1

    assert "does not set IOX_SSH_USER, IOX_SSH_PASSWORD" in capsys.readouterr().err


# insteon programs --disable/--enable ---------------------------------------------


async def test_programs_disable_is_a_dry_run_without_apply(
    programs_private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(programs_private), "programs", "--disable", "Example Folder"]) == 0

    out = capsys.readouterr().out
    assert "Would disable 1 program:" in out and "Dry run: nothing changed" in out
    assert "IoX programs:" not in out, "a lifecycle action does not print the export"
    assert fake_iox.writes() == []


async def test_programs_disable_with_apply_backs_up_iox_into_the_private_folder_first(
    programs_private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(programs_private), "programs", "--disable", "0003", "000A", "--apply"]) == 0

    (archive,) = (programs_private / "eisy" / "backups").iterdir()
    assert f"IoX backup: {archive}" in capsys.readouterr().out
    assert fake_iox.writes() == [("REST programs/0003/disable", {}), ("REST programs/000A/disable", {})]


async def test_programs_enable_fails_when_iox_leaves_a_program_unchanged(
    programs_private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_iox.ignored_programs.add("0004")

    assert await run(["--private-dir", str(programs_private), "programs", "--enable", "0004", "--apply"]) == 1

    captured = capsys.readouterr()
    assert "Not enabled: /rest/programs still shows 1 program disabled:" in captured.out
    assert "insteon: error: 1 program did not change; run the same command again to retry" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [["programs", "--apply"], ["programs", "--disable", "0003", "--enable", "000A"]],
)
async def test_programs_refuses_apply_alone_and_both_actions_at_once(
    programs_private: Path, arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit:
        await run(["--private-dir", str(programs_private), *arguments])

    assert exit.value.code == 2
async def test_audit_prints_the_report_and_its_progress_apart(
    private: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "audit", "--no-modem-links", "AA.BB.CC"]) == 0

    captured = capsys.readouterr()
    assert "Unresponsive Devices (1)" in captured.out
    assert "AA.BB.CC Example Dimmer: reading its Links" in captured.err
    assert "reading its Links" not in captured.out


async def test_refs_prints_the_references_of_a_device(
    private: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    assert await run(["--private-dir", str(private), "refs", "--repo", str(repo), "light.example_dimmer"]) == 0

    out = capsys.readouterr().out
    assert "References to AA.BB.CC Example Dimmer" in out
    assert "number.example_dimmer_on_level" in out


async def test_refs_outside_a_git_repo_fails_plainly(
    private: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "refs", "--repo", str(tmp_path / "nowhere"), "AA.BB.CC"]) == 1

    assert "cannot list files" in capsys.readouterr().err


# Commands that change IoX ------------------------------------------------------


async def test_restore_is_a_dry_run_without_apply(
    programs_private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(programs_private), "restore", "modem"]) == 0

    assert "Dry run: nothing changed" in capsys.readouterr().out
    assert fake_iox.writes() == []


async def test_restore_with_apply_backs_up_iox_into_the_private_folder_first(
    programs_private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(programs_private), "restore", "AA.BB.CC", "--apply"]) == 0

    (archive,) = (programs_private / "eisy" / "backups").iterdir()
    assert f"IoX backup: {archive}" in capsys.readouterr().out
    assert fake_iox.writes() == [("RestoreDeviceFromNode", {"node": "AA BB CC 1", "flag": "0"})]


async def test_a_changing_command_without_the_ssh_login_fails_before_any_write(
    private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["--private-dir", str(private), "restore", "modem", "--apply"]) == 1

    assert "does not set IOX_SSH_USER" in capsys.readouterr().err
    assert fake_iox.writes() == []


async def test_remove_prints_its_plan_and_progress_apart(
    programs_private: Path, tmp_path: Path, fake_iox: FakeIox, fake_ha: FakeHa, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    assert await run(["--private-dir", str(programs_private), "remove", "--repo", str(repo), "Example Relay"]) == 0

    captured = capsys.readouterr()
    assert "Would remove 0A.0B.0C Example Relay" in captured.out
    assert "to see whether it answers" in captured.err
    assert fake_iox.writes() == [] and fake_ha.writes() == []


async def test_scene_and_add_are_dry_runs_without_apply(
    programs_private: Path, fake_iox: FakeIox, fake_ha: FakeHa, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--private-dir", str(programs_private)]
    assert await run([*base, "scene", "add", "Example Scene", "11 22 33 4", "--responder"]) == 0
    assert await run([*base, "scene", "level", "12345", "AA.BB.CC", "40"]) == 0
    assert await run([*base, "add", "7A.7B.7C", "Example New"]) == 0

    out = capsys.readouterr().out
    assert out.count("Dry run: nothing changed") == 3
    assert fake_iox.writes() == [] and fake_ha.writes() == []


async def test_a_scene_change_that_breaks_a_rule_fails_plainly(
    programs_private: Path, fake_iox: FakeIox, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--private-dir", str(programs_private)]
    assert await run([*base, "scene", "add", "Example Empty Scene", "11 22 33 3", "--controller", "--apply"]) == 1

    assert "a Button controls at most one Scene" in capsys.readouterr().err
    assert fake_iox.writes() == []


async def test_replace_is_a_dry_run_without_apply_and_refuses_a_new_device_iox_does_not_have(
    programs_private: Path, tmp_path: Path, fake_iox: FakeIox, fake_ha: FakeHa, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    base = ["--private-dir", str(programs_private), "replace", "--repo", str(repo)]

    assert await run([*base, "AA.BB.CC", "7A.7B.7C"]) == 1
    assert "add it first with `insteon add 7A.7B.7C NAME --apply`" in capsys.readouterr().err

    fake_iox.nodes_xml = fake_iox.nodes_xml.replace(
        "</nodes>",
        '<node flag="128" nodeDefId="DimmerLampSwitch_ADV"><address>7A 7B 7C 1</address><name>Example New</name>'
        "<type>1.32.65.0</type><enabled>true</enabled><pnode>7A 7B 7C 1</pnode></node></nodes>",
    )
    fake_ha.entities += [
        {"entity_id": "light.example_new", "unique_id": "00:00:00:00:00:00_7A 7B 7C 1", "platform": "isy994", "device_id": None},
        {"entity_id": "number.example_new_on_level", "unique_id": "00:00:00:00:00:00_7A 7B 7C 1_OL", "platform": "isy994", "device_id": None},
    ]
    assert await run([*base, "AA.BB.CC", "7A.7B.7C"]) == 0

    out = capsys.readouterr().out
    assert "light.example_new -> light.example_dimmer" in out and "Dry run: nothing changed" in out
    assert fake_iox.writes() == [] and fake_ha.writes() == []
    fake_iox.replace_keeps_old = True
    assert await run([*base, "AA.BB.CC", "7A.7B.7C", "--apply"]) == 1
    assert "Did not finish replacing" in capsys.readouterr().out
    assert fake_ha.writes() == []
