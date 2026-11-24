import pytest

from insteon.address import InsteonAddress, ButtonAddress


@pytest.mark.parametrize(
    "text",
    ["1A.2B.3C", "1a.2b.3c", "1A 2B 3C", "1A:2B:3C", "1A-2B-3C", "1a2b3c", " 1A.2B.3C "],
)
def test_an_insteon_address_reads_the_same_in_every_format(text: str) -> None:
    assert InsteonAddress.parse(text) == InsteonAddress(0x1A, 0x2B, 0x3C)


def test_an_insteon_address_reads_bytes_that_iox_printed_without_leading_zeros() -> None:
    assert InsteonAddress.parse("AA B C") == InsteonAddress(0xAA, 0x0B, 0x0C)


def test_an_insteon_address_prints_dotted_with_two_digits_per_byte() -> None:
    assert str(InsteonAddress(0xAA, 0x0B, 0x0C)) == "AA.0B.0C"


@pytest.mark.parametrize("text", ["", "AA.BB", "AA.BB.CC.DD.EE", "GG.BB.CC", "AAA.BB.CC", "light.example"])
def test_anything_else_is_not_an_insteon_address(text: str) -> None:
    assert InsteonAddress.try_parse(text) is None
    with pytest.raises(ValueError):
        InsteonAddress.parse(text)


def test_a_node_address_names_the_device_and_the_button() -> None:
    node = ButtonAddress.parse("AA B C 1")

    assert node == ButtonAddress(InsteonAddress(0xAA, 0x0B, 0x0C), 1)
    assert node.iox == "AA B C 1"


def test_a_node_address_reads_a_keypad_button_number() -> None:
    assert ButtonAddress.parse("11 22 33 8").button == 8


def test_an_address_without_a_button_is_not_a_node_address() -> None:
    assert ButtonAddress.try_parse("11.22.33") is None


def test_a_node_address_that_is_not_insteon_is_rejected() -> None:
    assert ButtonAddress.try_parse("ZY003_1") is None
    assert ButtonAddress.try_parse("n001_example") is None
