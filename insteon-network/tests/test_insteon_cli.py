import subprocess
from pathlib import Path

import pytest
from insteon_fakes import FakeHa, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, USER
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
