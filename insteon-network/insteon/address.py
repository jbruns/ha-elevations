"""Insteon Addresses, and the addresses IoX gives a Device's Buttons.

An Insteon Address is three bytes, the same in every format: `1A.2B.3C`,
`1A 2B 3C`, `1a2b3c`. IoX writes a Button's address as the three bytes plus a
Button number, space separated, and drops leading zeros within a byte
(`AA B C 1`), so every byte is parsed as hex and compared numerically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARATORS = re.compile(r"[.\s:-]+")
_BYTE = re.compile(r"[0-9A-Fa-f]{1,2}")
_PACKED = re.compile(r"[0-9A-Fa-f]{6}")


def _bytes(parts: list[str]) -> tuple[int, int, int] | None:
    if len(parts) == 1 and _PACKED.fullmatch(parts[0]):
        packed = parts[0]
        parts = [packed[0:2], packed[2:4], packed[4:6]]
    if len(parts) != 3 or not all(_BYTE.fullmatch(part) for part in parts):
        return None
    high, middle, low = (int(part, 16) for part in parts)
    return high, middle, low


def _split(text: str) -> list[str]:
    stripped = text.strip()
    return _SEPARATORS.split(stripped) if stripped else []


@dataclass(frozen=True, order=True)
class InsteonAddress:
    """The three-byte identity printed on a Device."""

    high: int
    middle: int
    low: int

    @classmethod
    def try_parse(cls, text: str) -> InsteonAddress | None:
        found = _bytes(_split(text))
        return cls(*found) if found else None

    @classmethod
    def parse(cls, text: str) -> InsteonAddress:
        found = cls.try_parse(text)
        if found is None:
            raise ValueError(f"not an Insteon Address: {text!r}")
        return found

    @classmethod
    def from_int(cls, value: int) -> InsteonAddress:
        """An address IoX reports as one integer, as in link-table records."""
        return cls((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)

    @property
    def iox(self) -> str:
        """The bytes as IoX writes them: space separated, no leading zeros."""
        return f"{self.high:X} {self.middle:X} {self.low:X}"

    def __str__(self) -> str:
        return f"{self.high:02X}.{self.middle:02X}.{self.low:02X}"


@dataclass(frozen=True, order=True)
class ButtonAddress:
    """A Button's address: its Device's Insteon Address and the Button number,
    which IoX writes as its node address (`AA B C 1`)."""

    device: InsteonAddress
    button: int

    @classmethod
    def try_parse(cls, text: str) -> ButtonAddress | None:
        parts = _split(text)
        if len(parts) != 4 or not _BYTE.fullmatch(parts[3]):
            return None
        found = _bytes(parts[:3])
        return cls(InsteonAddress(*found), int(parts[3], 16)) if found else None

    @classmethod
    def parse(cls, text: str) -> ButtonAddress:
        found = cls.try_parse(text)
        if found is None:
            raise ValueError(f"not a Button address: {text!r}")
        return found

    @property
    def iox(self) -> str:
        return f"{self.device.iox} {self.button:X}"

    def __str__(self) -> str:
        return f"{self.device} {self.button:X}"
