from pathlib import Path

import pytest
from insteon_fakes import D2D_TAR, FakeEisySsh, FakeIox, MODEM, PASSWORD, SSH_PASSWORD, SSH_USER, SshEndpoint, USER, UUID
from insteon_fakes import Rec, plm_record
from insteon_fakes import eisy_ssh, fake_iox, fake_ssh, iox_url  # noqa: F401 (fixtures)

from insteon.address import InsteonAddress, ButtonAddress
from insteon.iox import DeviceUnresponsive, IoxClient, IoxError, IoxFiles, soap_envelope
from insteon.program import Command, Condition, NodeStatus, SetVariable, Undecoded, VariableKind, VariableRef, Wait



def client(url: str, secret: str = PASSWORD) -> IoxClient:
    return IoxClient(url, USER, secret)


async def test_the_network_lists_each_device_with_its_buttons(iox_url: str) -> None:
    async with client(iox_url) as iox:
        network = await iox.network()

    keypad = network.device(InsteonAddress.parse("11.22.33"))
    assert keypad is not None
    assert keypad.name == "Example Keypad"
    assert keypad.model == "KeypadDimmer_ADV"
    assert keypad.folder == "Example Hall"
    assert [(str(b.address), b.name) for b in keypad.buttons] == [
        ("11.22.33 1", "Example Keypad"),
        ("11.22.33 3", "Example Keypad A"),
        ("11.22.33 4", "Example Keypad B"),
    ]
    assert [str(d.address) for d in network.devices] == ["0A.0B.0C", "11.22.33", "22.33.44", "5A.5B.5C", "6A.6B.6C", "AA.BB.CC"]


async def test_the_network_reads_addresses_iox_printed_without_leading_zeros(iox_url: str) -> None:
    async with client(iox_url) as iox:
        network = await iox.network()

    relay = network.device(InsteonAddress(0x0A, 0x0B, 0x0C))
    assert relay is not None
    assert relay.name == "Example Relay"
    assert relay.comms_error is True


async def test_the_network_knows_which_devices_are_disabled(iox_url: str) -> None:
    async with client(iox_url) as iox:
        network = await iox.network()

    sensor = network.device(InsteonAddress.parse("22.33.44"))
    assert sensor is not None and sensor.enabled is False and sensor.folder is None


async def test_scenes_list_their_controller_and_responder_buttons(iox_url: str) -> None:
    async with client(iox_url) as iox:
        network = await iox.network()

    scenes = {scene.name: scene for scene in network.scenes}
    assert set(scenes) == {"Example Scene", "Example Three-Way", "Example Empty Scene"}
    assert scenes["Example Scene"].controllers == (ButtonAddress.parse("11 22 33 3"),)
    assert scenes["Example Scene"].responders == (ButtonAddress.parse("AA BB CC 1"), ButtonAddress.parse("A B C 1"))
    assert scenes["Example Three-Way"].controllers == (ButtonAddress.parse("AA BB CC 1"), ButtonAddress.parse("11 22 33 1"))
    assert scenes["Example Empty Scene"].controllers == scenes["Example Empty Scene"].responders == ()


async def test_nodes_that_are_not_insteon_are_kept_apart(iox_url: str) -> None:
    async with client(iox_url) as iox:
        network = await iox.network()

    assert [(node.name, node.family) for node in network.foreign] == [
        ("Example Z-Wave Lock", "Z-Wave"),
        ("Example Node Server", "Node Server"),
    ]
    assert "ZY003_1" in network.node_addresses


async def test_the_network_carries_the_controllers_uuid(iox_url: str) -> None:
    async with client(iox_url) as iox:
        assert (await iox.network()).uuid == UUID


async def test_the_modem_reports_its_address_and_firmware(iox_url: str) -> None:
    async with client(iox_url) as iox:
        modem = await iox.modem()

    assert modem.address == InsteonAddress.parse(MODEM)
    assert modem.firmware == "v9B"
    assert modem.status == "Connected"


async def test_the_modem_link_table_is_read_from_the_event_stream(iox_url: str, fake_iox: FakeIox) -> None:
    fake_iox.plm_records = [
        plm_record(0, 0xE2, 20, 0x112233),
        plm_record(1, 0xA2, 1, 0xAABBCC),
        plm_record(2, 0x22, 1, 0x0A0B0C),
    ]
    async with client(iox_url) as iox:
        links = await iox.modem_links(InsteonAddress.parse(MODEM))

    assert [(link.index, link.flags, link.group, str(link.address), link.in_use) for link in links] == [
        (0, 0xE2, 20, "11.22.33", True),
        (1, 0xA2, 1, "AA.BB.CC", True),
        (2, 0x22, 1, "0A.0B.0C", False),
    ]
    request = fake_iox.soap_calls[-1]
    assert request["params"]["command"] == "G_PLM_ALL"
    assert request["params"]["node"] == "2A 2B 2C"


async def test_soap_calls_carry_the_insteon_service_action(iox_url: str, fake_iox: FakeIox) -> None:
    async with client(iox_url) as iox:
        await iox.soap("DeviceSpecific", {"command": "G_PLM_INFO", "node": "", "option": "", "flag": 1, "CDATA": ""})

    call = fake_iox.soap_calls[-1]
    assert call["soapaction"] == '"urn:udi-com:service:X_Insteon_Lighting_Service:1#DeviceSpecific"'
    assert call["namespace"] == "urn:udi-com:service:X_Insteon_Lighting_Service:1"
    assert call["params"] == {"command": "G_PLM_INFO", "node": "", "option": "", "flag": "1", "CDATA": ""}


def test_a_soap_envelope_escapes_its_parameters() -> None:
    envelope = soap_envelope("RenameNode", {"id": "AA BB CC 1", "name": "Example <Dimmer> & Co"})

    assert "<name>Example &lt;Dimmer&gt; &amp; Co</name>" in envelope
    assert "<u:RenameNode xmlns:u=" in envelope


async def test_a_wrong_password_is_reported_plainly(iox_url: str) -> None:
    async with client(iox_url, secret="wrong") as iox:
        with pytest.raises(IoxError, match="401"):
            await iox.network()


async def test_programs_list_each_program_and_folder_with_its_state(iox_url: str) -> None:
    async with client(iox_url) as iox:
        programs = {program.id: program for program in await iox.programs()}

    assert sorted(programs) == [0x01, 0x02, 0x03, 0x04, 0x0A]
    folder = programs[0x02]
    assert (folder.name, folder.parent, folder.is_folder) == ("Example Folder", 0x01, True)
    follow = programs[0x04]
    assert (follow.name, follow.parent, follow.is_folder) == ("Example Follow", 0x02, False)
    assert (follow.enabled, follow.run_at_startup, follow.status) == (False, True, True)
    assert (follow.last_run, follow.last_finish) == ("2026/01/02 03:04:05 AM", "2026/01/02 03:04:07 AM")
    assert programs[0x01].parent is None
    assert programs[0x0A].last_run is None


async def test_variables_join_each_name_to_its_value_and_init(iox_url: str) -> None:
    async with client(iox_url) as iox:
        variables = await iox.variables()

    assert [(v.kind, v.id, v.name, v.value, v.init, v.precision, v.changed) for v in variables] == [
        (VariableKind.INTEGER, 1, "i.Example_Counter", 3, 1, 0, "20260102 03:04:05"),
        (VariableKind.STATE, 1, "s.Example_Level", 42, 0, 0, "20260102 03:04:04"),
        (VariableKind.STATE, 2, "s.Example_Unused", 0, 5, 0, "20260101 00:00:00"),
    ]


def files(endpoint: SshEndpoint, secret: str = SSH_PASSWORD) -> IoxFiles:
    return IoxFiles("127.0.0.1", SSH_USER, secret, port=endpoint.port, known_hosts=endpoint.known_hosts)


async def test_program_bodies_decode_if_then_else_from_the_d2d_files(eisy_ssh: SshEndpoint) -> None:
    bodies = await files(eisy_ssh).program_bodies()

    level = VariableRef(VariableKind.STATE, 1)
    assert sorted(bodies) == [0x01, 0x02, 0x03, 0x04, 0x0A]
    remember = bodies[0x03]
    assert remember.conditions == (Condition("and", NodeStatus("AA BB CC 1", "ST"), "ISNOT", level),)
    assert remember.then == (SetVariable(level, "EQ", NodeStatus("AA BB CC 1", "ST")),)
    assert (remember.otherwise, remember.comment) == ((), "Remembers the dimmer level")
    follow = bodies[0x04]
    assert [condition.joiner for condition in follow.conditions] == ["and", "or"]
    assert follow.then == (Wait(2), Command("DON", "12345", (level,)), Command("DON", "A B C 1", (level,)))


async def test_statements_in_unknown_shapes_are_kept_as_raw_xml(eisy_ssh: SshEndpoint) -> None:
    bodies = await files(eisy_ssh).program_bodies()

    unknown = bodies[0x0A]
    assert unknown.conditions == (Undecoded("<and /><schedule><daily /></schedule>"),)
    assert unknown.then == (Undecoded('<notify content="1">2</notify>'),)


async def test_the_ssh_password_goes_only_to_sudo_on_stdin(eisy_ssh: SshEndpoint, fake_ssh: FakeEisySsh) -> None:
    await files(eisy_ssh).program_bodies()

    assert fake_ssh.commands == [D2D_TAR]
    assert fake_ssh.sudo_passwords == [SSH_PASSWORD]


async def test_a_refused_ssh_login_fails_without_the_password(eisy_ssh: SshEndpoint) -> None:
    with pytest.raises(IoxError, match="SSH") as raised:
        await files(eisy_ssh, "wrong-password").program_bodies()

    assert "wrong-password" not in str(raised.value)


async def test_an_unknown_host_key_fails_and_says_how_to_trust_it(eisy_ssh: SshEndpoint, tmp_path: Path) -> None:
    empty = tmp_path / "empty_known_hosts"
    empty.write_text("")
    endpoint = SshEndpoint(eisy_ssh.port, empty)

    with pytest.raises(IoxError, match="known_hosts"):
        await files(endpoint).program_bodies()


async def test_only_files_named_by_a_hex_program_id_are_read_as_programs(
    eisy_ssh: SshEndpoint, fake_ssh: FakeEisySsh
) -> None:
    fake_ssh.files["D2D/notes.PGM"] = b"not a program"
    fake_ssh.files["D2D/README.txt"] = b"not a program"

    bodies = await files(eisy_ssh).program_bodies()

    assert sorted(bodies) == [0x01, 0x02, 0x03, 0x04, 0x0A]


async def test_the_expected_links_of_a_device_come_from_the_controller(iox_url: str, fake_iox: FakeIox) -> None:
    fake_iox.expected["AA BB CC 1"] = [Rec(0xA2, 0, 0x2A2B2C, 0xFF1F01), Rec(0xE2, 1, 0x2A2B2C, 0x010001)]
    async with client(iox_url) as iox:
        links = await iox.expected_links(ButtonAddress.parse("AA BB CC 1"))

    assert [(link.flags, link.group, str(link.address), link.data) for link in links] == [
        (0xA2, 0, MODEM, 0xFF1F01),
        (0xE2, 1, MODEM, 0x010001),
    ]
    assert fake_iox.soap_calls[-1]["params"] == {
        "command": "G_ISY_ALL", "node": "AA BB CC 1", "option": "", "flag": "1", "CDATA": "0 -1"
    }


async def test_a_devices_own_links_are_read_from_the_device(iox_url: str, fake_iox: FakeIox) -> None:
    fake_iox.actual["AA BB CC 1"] = [Rec(0xA2, 0, 0x2A2B2C, 0xFF1F01), Rec(0xEA, 1, 0x2A2B2C, 0x010001)]
    async with client(iox_url) as iox:
        links = await iox.device_links(ButtonAddress.parse("AA BB CC 1"))

    assert [(link.flags, link.group, str(link.address)) for link in links] == [(0xA2, 0, MODEM), (0xEA, 1, MODEM)]
    assert fake_iox.device_reads() == ["AA BB CC 1"]


async def test_a_device_iox_cannot_reach_is_unresponsive(iox_url: str, fake_iox: FakeIox) -> None:
    async with client(iox_url) as iox:
        with pytest.raises(DeviceUnresponsive, match="did not answer"):
            await iox.device_links(ButtonAddress.parse("A B C 1"))


async def test_a_device_that_answers_too_slowly_is_unresponsive_and_the_read_is_stopped(
    iox_url: str, fake_iox: FakeIox
) -> None:
    fake_iox.silent.add("A B C 1")
    async with client(iox_url) as iox:
        with pytest.raises(DeviceUnresponsive, match="within 0.3 s"):
            await iox.device_links(ButtonAddress.parse("A B C 1"), timeout=0.3)

    assert fake_iox.soap_calls[-1]["params"]["command"] == "STOP"
