import re
from pathlib import Path

import pytest
from insteon_fakes import CONF_TAR, FakeEisySsh, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, USER
from insteon_fakes import eisy_ssh, fake_iox, fake_ssh, iox_url  # noqa: F401 (fixtures)

from insteon.iox import IoxClient, IoxError, IoxFiles
from insteon.lifecycle import LifecycleResult, render, set_enabled
from insteon.rails import IoxBackup, LockHeld, Rails, lock
from insteon.resolve import ResolveError


def backup(ssh: SshEndpoint, tmp_path: Path, ssh_password: str = SSH_PASSWORD) -> IoxBackup:
    files = IoxFiles("127.0.0.1", SSH_USER, ssh_password, port=ssh.port, known_hosts=ssh.known_hosts)
    return IoxBackup(files, tmp_path / "eisy" / "backups")


async def run(
    iox_url: str, ssh: SshEndpoint, tmp_path: Path, names: list[str], *, enabled: bool, apply: bool, **options
) -> LifecycleResult:
    rails = Rails(apply=apply, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")
    async with IoxClient(iox_url, USER, PASSWORD) as iox:
        return await set_enabled(
            iox, rails, options.pop("iox_backup", None) or backup(ssh, tmp_path), names, enabled=enabled
        )


async def test_a_dry_run_lists_the_programs_it_would_disable_and_changes_nothing(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result = await run(iox_url, eisy_ssh, tmp_path, ["0003"], enabled=False, apply=False)

    assert fake_iox.writes() == []
    assert not (tmp_path / "eisy" / "backups").exists()
    assert render(result).splitlines() == [
        "Would disable 1 program:",
        "  My Programs/Example Folder/Example Dimmer Level  [0003]",
        "Dry run: nothing changed. Pass --apply to disable them.",
    ]


async def test_disabling_backs_up_iox_then_disables_each_program_and_verifies_it(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ssh: FakeEisySsh, tmp_path: Path
) -> None:
    result = await run(iox_url, eisy_ssh, tmp_path, ["0003", "000A"], enabled=False, apply=True)

    assert fake_ssh.commands == [CONF_TAR]
    assert fake_iox.writes() == [("REST programs/0003/disable", {}), ("REST programs/000A/disable", {})]
    assert fake_iox.gets[-1] == "programs", "re-reads /rest/programs to verify"
    (archive,) = (tmp_path / "eisy" / "backups").iterdir()
    assert render(result).splitlines() == [
        "Disabled 2 programs; /rest/programs confirms each:",
        "  My Programs/Example Folder/Example Dimmer Level  [0003]",
        "  My Programs/Example Unknown  [000A]",
        f"IoX backup: {archive}",
    ]


async def test_a_program_iox_leaves_unchanged_is_reported_as_not_disabled(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    fake_iox.ignored_programs.add("000A")

    result = await run(iox_url, eisy_ssh, tmp_path, ["0003", "000A"], enabled=False, apply=True)

    assert [p.iox_id for p in result.unchanged] == ["000A"]
    lines = render(result).splitlines()
    assert lines[:4] == [
        "Disabled 1 program; /rest/programs confirms it:",
        "  My Programs/Example Folder/Example Dimmer Level  [0003]",
        "Not disabled: /rest/programs still shows 1 program enabled:",
        "  My Programs/Example Unknown  [000A]",
    ]


async def test_a_folder_by_name_stands_for_its_programs_and_those_already_disabled_are_left(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result = await run(iox_url, eisy_ssh, tmp_path, ["example folder"], enabled=False, apply=True)

    assert fake_iox.writes() == [("REST programs/0003/disable", {})]
    assert render(result).splitlines()[:4] == [
        "Disabled 1 program; /rest/programs confirms it:",
        "  My Programs/Example Folder/Example Dimmer Level  [0003]",
        "Already disabled (1):",
        "  My Programs/Example Folder/Example Follow  [0004]",
    ]


async def test_a_folder_by_id_takes_in_its_subfolders_and_each_program_is_named_once(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    result = await run(iox_url, eisy_ssh, tmp_path, ["1", "Example Unknown", "0004"], enabled=True, apply=False)

    assert [p.iox_id for p in result.programs] == ["0004", "0003", "000A"]
    assert render(result).splitlines() == [
        "Would enable 1 program:",
        "  My Programs/Example Folder/Example Follow  [0004]",
        "Already enabled (2):",
        "  My Programs/Example Folder/Example Dimmer Level  [0003]",
        "  My Programs/Example Unknown  [000A]",
        "Dry run: nothing changed. Pass --apply to enable them.",
    ]


async def test_with_nothing_to_change_applying_takes_no_backup_and_writes_nothing(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ssh: FakeEisySsh, tmp_path: Path
) -> None:
    result = await run(iox_url, eisy_ssh, tmp_path, ["0004"], enabled=False, apply=True)

    assert fake_iox.writes() == [] and fake_ssh.commands == []
    assert render(result).splitlines() == [
        "Nothing to disable.",
        "Already disabled (1):",
        "  My Programs/Example Folder/Example Follow  [0004]",
    ]


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("Example Missing", "no program or folder with id or name 'Example Missing'"),
        ("00FF", "no program or folder with id or name '00FF'"),
        ("Example Twin", "'Example Twin' names 2 programs or folders; give an id: 0003, 000A"),
    ],
)
async def test_a_name_that_is_unknown_or_ambiguous_fails_before_anything_changes(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path, name: str, message: str
) -> None:
    fake_iox.programs_xml = fake_iox.programs_xml.replace("Example Dimmer Level", "Example Twin").replace(
        "Example Unknown", "Example Twin"
    )

    with pytest.raises(ResolveError, match=f"^{re.escape(message)}$"):
        await run(iox_url, eisy_ssh, tmp_path, ["0003", name], enabled=False, apply=True)

    assert fake_iox.writes() == []


async def test_no_program_changes_when_the_iox_backup_fails(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    with pytest.raises(IoxError):
        await run(
            iox_url, eisy_ssh, tmp_path, ["0003"], enabled=False, apply=True, iox_backup=backup(eisy_ssh, tmp_path, "wrong")
        )

    assert fake_iox.writes() == []


async def test_no_program_changes_while_another_command_holds_the_lock(
    iox_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, tmp_path: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "remove"), pytest.raises(LockHeld):
        await run(iox_url, eisy_ssh, tmp_path, ["0003"], enabled=False, apply=True)

    assert fake_iox.writes() == []


async def test_a_program_iox_no_longer_has_fails_with_the_http_error(iox_url: str, fake_iox: FakeIox) -> None:
    async with IoxClient(iox_url, USER, PASSWORD) as iox:
        (unknown,) = [p for p in await iox.programs() if p.iox_id == "000A"]
        fake_iox.programs_xml = fake_iox.programs_xml.replace('id="000A"', 'id="00EE"')

        with pytest.raises(IoxError, match="HTTP 404"):
            await iox.set_program_enabled(unknown, False)
