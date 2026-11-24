import pytest
from insteon_fakes import D2D_TAR, FakeEisySsh, FakeHa, FakeIox, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, TOKEN, USER
from insteon_fakes import eisy_ssh, fake_ha, fake_iox, fake_ssh, ha_url, iox_url  # noqa: F401 (fixtures)

from insteon.ha import HaClient
from insteon.iox import IoxClient, IoxFiles
from insteon.programs import export, render


async def exported(iox_url: str, ha_url: str, ssh: SshEndpoint) -> str:
    files = IoxFiles("127.0.0.1", SSH_USER, SSH_PASSWORD, port=ssh.port, known_hosts=ssh.known_hosts)
    async with IoxClient(iox_url, USER, PASSWORD) as iox, HaClient(ha_url, TOKEN) as ha:
        return render(await export(iox, files, ha))


def block(text: str, header: str) -> list[str]:
    """The lines of one program's entry: its header line and the indented lines below it."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.lstrip().startswith(header))
    indent = len(lines[start]) - len(lines[start].lstrip())
    entry = [lines[start].strip()]
    for line in lines[start + 1 :]:
        if not line.strip() or len(line) - len(line.lstrip()) <= indent:
            break
        entry.append(line.strip())
    return entry


async def test_each_program_is_listed_in_its_folder_with_its_state(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint
) -> None:
    text = await exported(iox_url, ha_url, eisy_ssh)

    assert "IoX programs: 3 programs in 2 folders" in text
    assert "My Programs/Example Folder/  [0002]" in text.splitlines()
    assert block(text, "Example Follow")[0] == (
        "Example Follow  [0004]  disabled, runs at startup, If last true, last run 2026/01/02 03:04:05 AM"
    )
    assert block(text, "Example Unknown")[0] == "Example Unknown  [000A]  enabled, If last false, never run"


async def test_if_then_else_reads_as_text_with_names_for_nodes_and_variables(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint
) -> None:
    text = await exported(iox_url, ha_url, eisy_ssh)

    assert block(text, "Example Follow")[1:9] == [
        "If",
        "Status 'Example Keypad A' (11.22.33 3) is not $s.Example_Level",
        "Or Status 'Example Keypad B' (11.22.33 4) is not $s.Example_Level",
        "Then",
        "Wait 2 seconds",
        "Set Scene 'Example Scene' (12345) On $s.Example_Level",
        "Set 'Example Relay' (0A.0B.0C 1) On $s.Example_Level",
        "Else",
    ]
    assert block(text, "Example Dimmer Level")[1:8] == [
        "If",
        "Status 'Example Dimmer' (AA.BB.CC 1) is not $s.Example_Level",
        "Then",
        "$s.Example_Level = Status 'Example Dimmer' (AA.BB.CC 1)",
        "Else",
        "- No Actions -",
        "Comment: Remembers the dimmer level",
    ]


async def test_a_program_lists_the_devices_and_scenes_it_names_with_their_ha_entities(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint
) -> None:
    text = await exported(iox_url, ha_url, eisy_ssh)

    follow = block(text, "Example Follow")
    start = follow.index("Devices:")
    assert follow[start : start + 5] == [
        "Devices:",
        "'Example Keypad' (11.22.33): event.example_keypad_a",
        "Scene 'Example Scene' (12345): switch.example_scene",
        "'Example Dimmer' (AA.BB.CC), in Scene 12345: light.example_dimmer, number.example_dimmer_on_level",
        "'Example Relay' (0A.0B.0C): light.example_relay",
    ]


async def test_a_program_flags_automations_that_reference_the_same_devices(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint
) -> None:
    text = await exported(iox_url, ha_url, eisy_ssh)

    assert block(text, "Example Dimmer Level")[-2:] == [
        "Possible duplicates in HA (same Devices):",
        "automation.example_dimmer_level (Example dimmer level): light.example_dimmer",
    ]
    assert block(text, "Example Follow")[-3:] == [
        "Possible duplicates in HA (same Devices):",
        "automation.example_dimmer_level (Example dimmer level): light.example_dimmer",
        "automation.example_keypad_press (Example keypad press): event.example_keypad_a, switch.example_scene",
    ]


async def test_a_program_no_automation_shares_devices_with_is_not_flagged(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_ha: FakeHa
) -> None:
    del fake_ha.automations["automation.example_dimmer_level"]

    text = await exported(iox_url, ha_url, eisy_ssh)

    assert block(text, "Example Dimmer Level")[-1] == "No HA automation references these Devices."
    assert block(text, "Example Unknown")[-2:] == ["Devices: none", "No HA automation references these Devices."]


async def test_each_variable_shows_its_type_value_init_and_the_programs_using_it(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint
) -> None:
    text = await exported(iox_url, ha_url, eisy_ssh)

    assert block(text, "IoX variables:") == [
        "IoX variables: 1 integer, 2 state",
        "Integer",
        "1  i.Example_Counter  value 3, init 1, changed 20260102 03:04:05; used by no program",
        "State",
        "1  s.Example_Level  value 42, init 0, changed 20260102 03:04:04; used by Example Dimmer Level, Example Follow",
        "2  s.Example_Unused  value 0, init 5, changed 20260101 00:00:00; used by no program",
    ]


async def test_statements_not_decoded_are_counted_up_front_and_shown_raw(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint
) -> None:
    text = await exported(iox_url, ha_url, eisy_ssh)

    assert "Bodies: 5 D2D files read over SSH; 2 statements not decoded, shown raw as [not decoded]" in text
    assert block(text, "Example Unknown")[1:5] == [
        "If",
        "[not decoded] <and /><schedule><daily /></schedule>",
        "Then",
        '[not decoded] <notify content="1">2</notify>',
    ]


async def test_a_program_without_a_d2d_file_says_its_body_is_missing(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_ssh: FakeEisySsh
) -> None:
    del fake_ssh.files["D2D/0003.PGM"]

    text = await exported(iox_url, ha_url, eisy_ssh)

    assert "Bodies: 4 D2D files read over SSH" in text
    assert block(text, "Example Dimmer Level")[1:] == ["body: no D2D file, so its Devices are unknown"]


async def test_the_export_changes_nothing_on_iox_or_home_assistant(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox, fake_ha: FakeHa, fake_ssh: FakeEisySsh
) -> None:
    await exported(iox_url, ha_url, eisy_ssh)

    assert fake_iox.soap_calls == []
    assert fake_ssh.commands == [D2D_TAR]
    assert {command["type"] for command in fake_ha.commands} <= {
        "config/device_registry/list",
        "config/entity_registry/list",
        "config_entries/get",
        "get_states",
        "search/related",
    }


async def test_a_program_outside_any_folder_is_still_listed(
    iox_url: str, ha_url: str, eisy_ssh: SshEndpoint, fake_iox: FakeIox
) -> None:
    fake_iox.programs_xml = fake_iox.programs_xml.replace('id="000A" parentId="0001"', 'id="000A"')

    text = await exported(iox_url, ha_url, eisy_ssh)

    assert "(no folder)" in text.splitlines()
    assert block(text, "Example Unknown")[0].startswith("Example Unknown  [000A]")
