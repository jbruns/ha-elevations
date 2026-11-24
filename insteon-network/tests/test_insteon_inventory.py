from pathlib import Path

import pytest
from insteon_fakes import FakeIox, MODEM, PASSWORD, USER, plm_record
from insteon_fakes import fake_iox, iox_url  # noqa: F401 (fixtures)

from insteon.inventory import inventory, render
from insteon.iox import IoxClient
from insteon.address import InsteonAddress
from insteon.network import Modem, Network
from insteon.inventory import Inventory
from insteon.rails import LockHeld, Rails, lock


MODEM_INFO = Modem(InsteonAddress.parse(MODEM), "v9B", "Connected")


def rails(tmp_path: Path) -> Rails:
    return Rails(apply=False, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "backups")


async def report(iox_url: str, tmp_path: Path, **options) -> str:
    async with IoxClient(iox_url, USER, PASSWORD) as iox:
        return render(await inventory(iox, rails(tmp_path), **options))


def links(count: int, unused: int = 0) -> list[str]:
    records = [plm_record(ix, 0xE2, 1, 0x112233) for ix in range(count)]
    return records + [plm_record(count + ix, 0x22, 1, 0x112233) for ix in range(unused)]


async def test_inventory_lists_each_device_with_its_buttons(iox_url: str, tmp_path: Path) -> None:
    text = await report(iox_url, tmp_path)

    assert "11.22.33  Example Keypad  KeypadDimmer_ADV  [Example Hall]" in text
    assert "11.22.33 3  Example Keypad A" in text
    assert "0A.0B.0C  Example Relay" in text
    assert "IoX reports a communication error" in text
    assert "22.33.44  Example Leak Sensor  BinaryAlarm_ADV  (disabled)" in text


async def test_inventory_lists_scenes_with_their_controller_and_responder_buttons(
    iox_url: str, tmp_path: Path
) -> None:
    text = await report(iox_url, tmp_path)

    scene = text[text.index("Example Scene (12345)") :].split("\n\n")[0]
    assert scene.splitlines()[1:] == [
        "    Controller  11.22.33 3  Example Keypad A",
        "    Responder   AA.BB.CC 1  Example Dimmer",
        "    Responder   0A.0B.0C 1  Example Relay",
    ]
    assert "Example Empty Scene (34567)\n    (no Buttons)" in text


async def test_inventory_flags_nodes_that_are_not_insteon_as_misconfiguration(
    iox_url: str, tmp_path: Path
) -> None:
    text = await report(iox_url, tmp_path)

    flagged = text[text.index("Misconfiguration") :]
    assert "ZY003_1  Example Z-Wave Lock  Z-Wave" in flagged
    assert "n001_example  Example Node Server  Node Server" in flagged


async def test_inventory_counts_the_modem_links_in_use_against_capacity(
    iox_url: str, fake_iox: FakeIox, tmp_path: Path
) -> None:
    fake_iox.plm_records = links(9, unused=2)

    text = await report(iox_url, tmp_path, capacity=100)

    assert f"Modem {MODEM}  v9B  Connected" in text
    assert "Links: 9 in use of about 100 (9%); 11 records read" in text
    assert "WARNING" not in text


async def test_inventory_warns_at_eighty_percent_of_modem_capacity(
    iox_url: str, fake_iox: FakeIox, tmp_path: Path
) -> None:
    fake_iox.plm_records = links(8)

    text = await report(iox_url, tmp_path, capacity=10)

    assert "WARNING: the Modem holds 8 Links, 80% of its capacity of about 10" in text


async def test_the_default_modem_capacity_is_about_900(
    iox_url: str, fake_iox: FakeIox, tmp_path: Path
) -> None:
    fake_iox.plm_records = links(1)

    assert "of about 900" in await report(iox_url, tmp_path)


async def test_inventory_can_skip_the_slow_modem_link_read(
    iox_url: str, fake_iox: FakeIox, tmp_path: Path
) -> None:
    text = await report(iox_url, tmp_path, read_modem_links=False)

    assert "Links: not read" in text
    assert "G_PLM_ALL" not in [call["params"].get("command") for call in fake_iox.soap_calls]


async def test_inventory_of_named_devices_shows_only_them_and_their_scenes(
    iox_url: str, tmp_path: Path
) -> None:
    text = await report(iox_url, tmp_path, names=["AA.BB.CC"], read_modem_links=False)

    assert "Example Dimmer" in text
    assert "Example Leak Sensor" not in text
    assert "Example Scene (12345)" in text
    assert "Example Empty Scene" not in text
    assert "Example Z-Wave Lock" in text[text.index("Misconfiguration") :]


def test_an_empty_network_renders() -> None:
    text = render(Inventory(Network(uuid="00:00:00:00:00:00", devices=(), scenes=()), MODEM_INFO, None, 900))

    assert "0 Devices, 0 Scenes" in text


async def test_inventory_does_not_read_the_modem_while_another_command_holds_the_lock(
    iox_url: str, fake_iox: FakeIox, tmp_path: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "restore"), pytest.raises(LockHeld, match="insteon restore"):
        await report(iox_url, tmp_path)

    assert "G_PLM_ALL" not in [call["params"].get("command") for call in fake_iox.soap_calls]
