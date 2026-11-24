from pathlib import Path

import pytest
from insteon_fakes import FakeIox, PASSWORD, USER, Rec, plm_record
from insteon_fakes import fake_iox, iox_url  # noqa: F401 (fixtures)

from insteon.audit import AuditReport, audit, render
from insteon.iox import IoxClient
from insteon.rails import LockHeld, Rails, lock

MODEM = 0x2A2B2C
DIMMER, KEYPAD, RELAY = 0xAABBCC, 0x112233, 0x0A0B0C
DOOR, LEAK, SMOKE = 0x5A5B5C, 0x223344, 0x6A6B6C
NOWHERE = 0x334455
"""An address the Controller has no Device at."""


def in_agreement() -> tuple[dict[str, list[Rec]], list[tuple[int, int, int]]]:
    """Device tables and the Modem's table for the fixture network, all in agreement.

    Example Scene (Modem group 20): the Keypad's Button 3 controls the Dimmer and the Relay.
    """
    devices = {
        "AA BB CC 1": [Rec(0xA2, 0, MODEM), Rec(0xE2, 1, MODEM), Rec(0xA2, 20, MODEM, 0xFF1F01), Rec(0xA2, 3, KEYPAD, 0xFF1F01)],
        "11 22 33 1": [Rec(0xA2, 0, MODEM), Rec(0xE2, 1, MODEM), Rec(0xE2, 3, MODEM), Rec(0xE2, 3, DIMMER), Rec(0xE2, 3, RELAY)],
        "A B C 1": [Rec(0xA2, 0, MODEM), Rec(0xE2, 1, MODEM), Rec(0xA2, 20, MODEM), Rec(0xA2, 3, KEYPAD)],
        "5A 5B 5C 1": [Rec(0xE2, 1, MODEM)],
        "22 33 44 1": [Rec(0xE2, 1, MODEM)],
        "6A 6B 6C 1": [Rec(0xA2, 0, MODEM), Rec(0xE2, 1, MODEM)],
    }
    modem = [
        (0xE2, 0, DIMMER), (0xA2, 1, DIMMER), (0xE2, 20, DIMMER),
        (0xE2, 0, KEYPAD), (0xA2, 1, KEYPAD), (0xA2, 3, KEYPAD),
        (0xE2, 0, RELAY), (0xA2, 1, RELAY), (0xE2, 20, RELAY),
        (0xA2, 1, DOOR), (0xA2, 1, LEAK),
        (0xE2, 0, SMOKE), (0xA2, 1, SMOKE),
    ]
    return devices, modem


@pytest.fixture
def network(fake_iox: FakeIox) -> FakeIox:
    devices, modem = in_agreement()
    fake_iox.expected = {node: list(records) for node, records in devices.items()}
    fake_iox.actual = {node: list(records) for node, records in devices.items()}
    set_modem(fake_iox, modem)
    return fake_iox


def set_modem(fake: FakeIox, records: list[tuple[int, int, int]]) -> None:
    fake.plm_records = [plm_record(ix, flags, group, address) for ix, (flags, group, address) in enumerate(records)]


def rails(tmp_path: Path) -> Rails:
    return Rails(apply=False, lock_path=tmp_path / "insteon.lock", backup_root=tmp_path / "backups")


async def run_audit(iox_url: str, tmp_path: Path, **options) -> AuditReport:
    async with IoxClient(iox_url, USER, PASSWORD) as iox:
        return await audit(iox, rails(tmp_path), **options)


async def report(iox_url: str, tmp_path: Path, **options) -> str:
    return render(await run_audit(iox_url, tmp_path, **options))


async def test_a_network_in_agreement_with_the_controller_has_no_differences_stale_links_or_unresponsive_devices(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    text = await report(iox_url, tmp_path)

    assert "No Unresponsive Devices." in text
    assert "No Stale Links." in text
    assert "No differences." in text


async def test_battery_and_disabled_devices_are_skipped_unless_named(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    text = await report(iox_url, tmp_path)

    assert network.device_reads() == ["A B C 1", "11 22 33 1", "6A 6B 6C 1", "AA BB CC 1"]
    skipped = text[text.index("Skipped (2)") :]
    assert "5A.5B.5C  Example Door Sensor: a Battery Device; name it to audit it" in skipped
    assert "22.33.44  Example Leak Sensor: disabled; name it to audit it" in skipped


async def test_flag_bits_other_than_in_use_and_controller_are_not_differences(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    network.actual["AA BB CC 1"] = [
        Rec(0xA2, 0, MODEM), Rec(0xEA, 1, MODEM), Rec(0xAA, 20, MODEM, 0xFF1F01), Rec(0xA2, 3, KEYPAD, 0xFF1F01)
    ]

    assert "No differences." in await report(iox_url, tmp_path)


async def test_deleted_records_on_a_device_are_ignored(iox_url: str, network: FakeIox, tmp_path: Path) -> None:
    network.actual["AA BB CC 1"].append(Rec(0x22, 7, NOWHERE))

    text = await report(iox_url, tmp_path)

    assert "No differences." in text and "No Stale Links." in text


async def test_a_link_missing_from_a_device_is_a_difference_with_its_scene(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    network.actual["AA BB CC 1"] = network.actual["AA BB CC 1"][:3]

    text = await report(iox_url, tmp_path)

    differences = text[text.index("Differences (1, in 1 tables)") :]
    assert differences.splitlines()[2:5] == [
        "  AA.BB.CC Example Dimmer",
        "    missing  responds to 11.22.33 Example Keypad group 3",
        "      Scenes: Example Scene (12345)",
    ]


async def test_a_link_the_controller_does_not_expect_and_different_data_are_differences(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    network.actual["11 22 33 1"].append(Rec(0xE2, 1, DIMMER))
    network.actual["AA BB CC 1"][3] = Rec(0xA2, 3, KEYPAD, 0x801F01)

    text = await report(iox_url, tmp_path)

    assert "    extra    controls AA.BB.CC Example Dimmer with group 1\n" in text
    assert (
        "    data     responds to 11.22.33 Example Keypad group 3: holds 80 1F 01, the Controller expects FF 1F 01"
        in text
    )


async def test_a_link_to_an_address_the_controller_lacks_is_a_stale_link_not_a_difference(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    network.actual["11 22 33 1"].append(Rec(0xE2, 3, NOWHERE))

    text = await report(iox_url, tmp_path)

    stale = text[text.index("Stale Links (1)") :].split("\n\n")[0]
    assert stale.splitlines()[1:] == [
        "  in 11.22.33 Example Keypad: controls 33.44.55 (not on the Controller) with group 3",
        "    the Controller has no Device at 33.44.55; the Controller does not expect it",
        "    Scenes: Example Scene (12345)",
    ]
    assert "No differences." in text


async def test_a_device_that_does_not_answer_is_unresponsive_with_its_scenes_and_links_to_it_are_stale(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    del network.actual["A B C 1"]

    text = await report(iox_url, tmp_path)

    unresponsive = text[text.index("Unresponsive Devices (1)") :].split("\n\n")[0]
    assert unresponsive.splitlines()[1:4] == [
        "  0A.0B.0C  Example Relay  [Example Room]",
        "    0A.0B.0C did not answer; IoX gave up (HTTP 501)",
        "    Scenes: Example Scene (12345)",
    ]
    stale = text[text.index("Stale Links (4)") :]
    assert "in 11.22.33 Example Keypad: controls 0A.0B.0C Example Relay with group 3" in stale
    assert "0A.0B.0C is an Unresponsive Device; the Controller expects it" in stale
    assert f"in the Modem (2A.2B.2C): controls 0A.0B.0C Example Relay with group 20" in stale


async def test_the_modems_links_are_compared_with_the_mirror_of_the_devices_expected_links(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    _, modem = in_agreement()
    set_modem(network, [record for record in modem if record != (0xE2, 20, DIMMER)] + [(0xA2, 9, SMOKE)])

    text = await report(iox_url, tmp_path)

    differences = text[text.index("Differences") :]
    assert "  the Modem (2A.2B.2C)\n    missing  controls AA.BB.CC Example Dimmer with group 20" in differences
    assert "    extra    responds to 6A.6B.6C Example Smoke Bridge group 9" in differences


async def test_a_modem_link_to_an_address_the_controller_lacks_is_stale(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    _, modem = in_agreement()
    set_modem(network, modem + [(0xA2, 1, NOWHERE), (0x22, 1, NOWHERE)])

    text = await report(iox_url, tmp_path)

    assert "Stale Links (1)\n  in the Modem (2A.2B.2C): responds to 33.44.55 (not on the Controller) group 1" in text


async def test_naming_devices_audits_only_them_and_their_links_in_the_modem(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    _, modem = in_agreement()
    set_modem(network, [record for record in modem if record != (0xE2, 0, KEYPAD)] + [(0xA2, 1, NOWHERE)])

    text = await report(iox_url, tmp_path, names=["AA.BB.CC", "Example Dimmer"])

    assert network.device_reads() == ["AA BB CC 1"]
    assert text.startswith("Link Audit of the named Devices and the Modem: 1 Devices read, 0 Unresponsive, 0 skipped")
    assert "No differences." in text and "No Stale Links." in text


async def test_a_named_battery_device_is_read_after_the_administrator_holds_set(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    prompts: list[str] = []

    def press_enter(message: str) -> str:
        prompts.append(message)
        assert network.device_reads() == []  # asked before the read
        return ""

    await run_audit(iox_url, tmp_path, names=["5A.5B.5C"], prompt=press_enter, read_modem_links=False)

    assert len(prompts) == 1 and "Example Door Sensor" in prompts[0] and "Set button" in prompts[0]
    assert network.device_reads() == ["5A 5B 5C 1"]


async def test_a_named_battery_device_can_be_skipped_at_the_prompt(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    text = render(await run_audit(iox_url, tmp_path, names=["5A.5B.5C"], prompt=lambda _: "s", read_modem_links=False))

    assert network.device_reads() == []
    assert "5A.5B.5C  Example Door Sensor: a Battery Device; skipped at the prompt" in text


async def test_a_device_that_answers_too_slowly_is_unresponsive_not_a_crash(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    network.silent.add("6A 6B 6C 1")

    text = await report(iox_url, tmp_path, timeout=0.3, read_modem_links=False)

    assert "6A.6B.6C did not answer within 0.3 s" in text
    assert "Unresponsive Devices (1)" in text


async def test_the_audit_reports_progress_for_each_device(iox_url: str, network: FakeIox, tmp_path: Path) -> None:
    messages: list[str] = []
    del network.actual["A B C 1"]

    await run_audit(iox_url, tmp_path, progress=messages.append)

    assert messages[0] == "Reading the Links the Controller expects for 6 Devices"
    assert "[1/4] 0A.0B.0C Example Relay: reading its Links" in messages
    assert "[1/4] 0A.0B.0C Example Relay: Unresponsive (0A.0B.0C did not answer; IoX gave up (HTTP 501))" in messages
    assert "[4/4] AA.BB.CC Example Dimmer: 4 Links" in messages
    assert messages[-1] == "Reading the Modem's Links (about 2 a second)"


async def test_the_audit_reads_nothing_while_another_command_holds_the_lock(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    with lock(tmp_path / "insteon.lock", "restore"), pytest.raises(LockHeld, match="insteon restore"):
        await run_audit(iox_url, tmp_path)

    assert [call["params"]["command"] for call in network.soap_calls] == ["G_PLM_INFO"]


async def test_a_missing_link_to_an_unresponsive_device_says_so(
    iox_url: str, network: FakeIox, tmp_path: Path
) -> None:
    del network.actual["A B C 1"]
    network.actual["11 22 33 1"] = network.actual["11 22 33 1"][:4]

    text = await report(iox_url, tmp_path)

    assert "    missing  controls 0A.0B.0C Example Relay with group 3 (an Unresponsive Device)" in text
