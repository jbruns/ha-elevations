import pytest
from insteon_fakes import FakeIox, MODEM, PASSWORD, USER, UUID, plm_record
from insteon_fakes import fake_iox, iox_url  # noqa: F401 (fixtures)

from insteon.address import InsteonAddress, ButtonAddress
from insteon.iox import IoxClient, IoxError, soap_envelope


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
    assert [str(d.address) for d in network.devices] == ["0A.0B.0C", "11.22.33", "22.33.44", "AA.BB.CC"]


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
