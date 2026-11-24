import io
import tarfile
from pathlib import Path

import pytest
from insteon_fakes import CONF_TAR, FakeEisySsh, SSH_PASSWORD, SSH_USER, SshEndpoint
from insteon_fakes import eisy_ssh, fake_ssh  # noqa: F401 (fixtures)

from insteon.iox import IoxError, IoxFiles
from insteon.rails import IoxBackup, Rails


def files(ssh: SshEndpoint, ssh_password: str = SSH_PASSWORD) -> IoxFiles:
    return IoxFiles("127.0.0.1", SSH_USER, ssh_password, port=ssh.port, known_hosts=ssh.known_hosts)


def rails(tmp_path: Path) -> Rails:
    return Rails(apply=True, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "insteon-backups")


async def test_applying_backs_up_all_of_iox_conf_as_a_tar_before_the_change(
    eisy_ssh: SshEndpoint, fake_ssh: FakeEisySsh, tmp_path: Path
) -> None:
    fake_ssh.files["0.UCF"] = b"example"
    backup = IoxBackup(files(eisy_ssh), tmp_path / "eisy" / "backups")

    async with rails(tmp_path).changing("restore", [backup]):
        (archive,) = (tmp_path / "eisy" / "backups").iterdir()

    assert backup.path == archive and archive.suffix == ".tar"
    with tarfile.open(fileobj=io.BytesIO(archive.read_bytes())) as tar:
        assert {"CONF/0.UCF", "CONF/D2D/0001.PGM"} <= set(tar.getnames())
    assert fake_ssh.commands == [CONF_TAR]
    assert fake_ssh.sudo_passwords == [SSH_PASSWORD]


async def test_a_failed_iox_backup_stops_the_command_before_it_changes_anything(
    eisy_ssh: SshEndpoint, tmp_path: Path
) -> None:
    backup = IoxBackup(files(eisy_ssh, ssh_password="wrong"), tmp_path / "eisy" / "backups")
    changed = False

    with pytest.raises(IoxError):
        async with rails(tmp_path).changing("restore", [backup]):
            changed = True

    assert not changed
    assert not (tmp_path / "eisy" / "backups").exists() or not list((tmp_path / "eisy" / "backups").iterdir())


async def test_an_empty_conf_archive_is_not_a_backup(
    eisy_ssh: SshEndpoint, fake_ssh: FakeEisySsh, tmp_path: Path
) -> None:
    fake_ssh.files = {}

    with pytest.raises(IoxError, match="holds no files"):
        await files(eisy_ssh).conf_archive()
