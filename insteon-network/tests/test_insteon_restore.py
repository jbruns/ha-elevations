from pathlib import Path

import pytest
from insteon_fakes import FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, USER
from insteon_fakes import eisy_ssh, fake_iox, fake_ssh, iox_url  # noqa: F401 (fixtures)

from insteon.iox import IoxClient, IoxError, IoxFiles
from insteon.rails import IoxBackup, LockHeld, Rails, lock
from insteon.restore import RestoreResult, render, restore


@pytest.fixture(autouse=True)
def short_battery_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Battery Device stays in linking mode for about four minutes; the tests wait half a second."""
    monkeypatch.setattr("insteon.iox.BATTERY_WINDOW", 0.5)


def backup(ssh: SshEndpoint, tmp_path: Path, ssh_password: str = SSH_PASSWORD) -> IoxBackup:
    files = IoxFiles("127.0.0.1", SSH_USER, ssh_password, port=ssh.port, known_hosts=ssh.known_hosts)
    return IoxBackup(files, tmp_path / "eisy" / "backups")


async def run(
    iox_url: str, ssh: SshEndpoint, tmp_path: Path, name: str, *, apply: bool, answers: list[str] | None = None, **options
) -> tuple[RestoreResult, list[str]]:
    asked: list[str] = []

    def prompt(message: str) -> str:
        asked.append(message)
        return (answers or [""]).pop(0)

    rails = Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")
    async with IoxClient(iox_url, USER, PASSWORD) as iox:
        result = await restore(iox, rails, options.pop("iox_backup", None) or backup(ssh, tmp_path), name, prompt=prompt)
    return result, asked


async def test_a_dry_run_says_what_it_would_restore_and_writes_nothing(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result, asked = await run(iox_url, eisy_ssh, tmp_path, "Example Dimmer", apply=False)

    assert fake_iox.writes() == []
    assert not (tmp_path / "eisy" / "backups").exists()
    text = render(result)
    assert "Would Restore the Links of AA.BB.CC Example Dimmer from the Controller's database" in text
    assert "Dry run: nothing changed. Pass --apply to Restore." in text


async def test_restoring_a_device_backs_up_iox_then_restores_it_from_its_primary_button(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result, asked = await run(iox_url, eisy_ssh, tmp_path, "11.22.33", apply=True)

    assert fake_iox.writes() == [("RestoreDeviceFromNode", {"node": "11 22 33 1", "flag": "0"})]
    assert fake_iox.soap_calls[-1]["soapaction"].endswith('#RestoreDevice"')
    (archive,) = (tmp_path / "eisy" / "backups").iterdir()
    assert f"IoX backup: {archive}" in render(result)
    assert "Restored the Links of 11.22.33 Example Keypad." in render(result)


async def test_restoring_the_modem_rewrites_its_links(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result, asked = await run(iox_url, eisy_ssh, tmp_path, "modem", apply=True)

    assert fake_iox.writes() == [("ReplaceModem", {})]
    assert "Restored the Modem's Links." in render(result)


async def test_no_write_happens_when_the_iox_backup_fails(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    with pytest.raises(IoxError):
        await run(iox_url, eisy_ssh, tmp_path, "modem", apply=True, iox_backup=backup(eisy_ssh, tmp_path, "wrong"))

    assert fake_iox.writes() == []


async def test_no_write_happens_while_another_command_holds_the_lock(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "remove"), pytest.raises(LockHeld):
        await run(iox_url, eisy_ssh, tmp_path, "modem", apply=True)

    assert fake_iox.writes() == []


async def test_a_battery_device_is_restored_only_once_the_administrator_holds_set(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result, asked = await run(iox_url, eisy_ssh, tmp_path, "Example Door Sensor", apply=True)

    assert "Hold its Set button" in asked[0]
    assert fake_iox.writes() == [("RestoreDeviceFromNode", {"node": "5A 5B 5C 1", "flag": "0"})]
    assert "Restored the Links of 5A.5B.5C Example Door Sensor." in render(result)


async def test_a_battery_device_skipped_at_the_prompt_is_not_written(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result, asked = await run(iox_url, eisy_ssh, tmp_path, "Example Door Sensor", apply=True, answers=["s"])

    assert fake_iox.writes() == []
    assert "Skipped at the prompt: nothing written." in render(result)


async def test_pending_writes_left_on_a_battery_device_are_reported(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    fake_iox.pending.add("5A 5B 5C 1")

    result, asked = await run(iox_url, eisy_ssh, tmp_path, "Example Door Sensor", apply=True)

    assert "Pending Writes remain on 5A.5B.5C Example Door Sensor" in render(result)


async def test_the_restore_of_a_battery_device_waits_until_its_pending_writes_clear(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    fake_iox.written_later.add("5A 5B 5C 1")

    result, asked = await run(iox_url, eisy_ssh, tmp_path, "Example Door Sensor", apply=True)

    assert result.write is not None and result.write.written and not result.write.pending
    assert "Pending Writes remain" not in render(result)
