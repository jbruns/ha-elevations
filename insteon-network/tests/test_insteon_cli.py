from pathlib import Path

import pytest
from insteon_fakes import FakeHa, PASSWORD, TOKEN, USER
from insteon_fakes import fake_ha, fake_iox, ha_url, iox_url  # noqa: F401 (fixtures)
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
