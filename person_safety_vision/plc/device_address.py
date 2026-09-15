"""Mitsubishi device address parsing ("M100", "D100", "X1F", "SM400" ...) + MC protocol codes."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Tuple

# name -> (binary code, is_bit, radix of the device number, 2-char ASCII code)
DEVICE_TABLE: Dict[str, Tuple[int, bool, int, str]] = {
    "SM": (0x91, True, 10, "SM"),
    "SD": (0xA9, False, 10, "SD"),
    "X": (0x9C, True, 16, "X*"),
    "Y": (0x9D, True, 16, "Y*"),
    "M": (0x90, True, 10, "M*"),
    "L": (0x92, True, 10, "L*"),
    "F": (0x93, True, 10, "F*"),
    "V": (0x94, True, 10, "V*"),
    "B": (0xA0, True, 16, "B*"),
    "D": (0xA8, False, 10, "D*"),
    "W": (0xB4, False, 16, "W*"),
    "TS": (0xC1, True, 10, "TS"),
    "TC": (0xC0, True, 10, "TC"),
    "TN": (0xC2, False, 10, "TN"),
    "SS": (0xC7, True, 10, "SS"),
    "SC": (0xC6, True, 10, "SC"),
    "SN": (0xC8, False, 10, "SN"),
    "CS": (0xC4, True, 10, "CS"),
    "CC": (0xC3, True, 10, "CC"),
    "CN": (0xC5, False, 10, "CN"),
    "SB": (0xA1, True, 16, "SB"),
    "SW": (0xB5, False, 16, "SW"),
    "DX": (0xA2, True, 16, "DX"),
    "DY": (0xA3, True, 16, "DY"),
    "Z": (0xCC, False, 10, "Z*"),
    "R": (0xAF, False, 10, "R*"),
    "ZR": (0xB0, False, 16, "ZR"),
}

_ADDR_RE = re.compile(r"^\s*([A-Z]{1,2})\s*([0-9A-F]+)\s*$", re.IGNORECASE)


class DeviceAddressError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceAddress:
    device: str      # "M", "D", "X" ...
    number: int      # numeric device number

    @property
    def code(self) -> int:
        return DEVICE_TABLE[self.device][0]

    @property
    def is_bit(self) -> bool:
        return DEVICE_TABLE[self.device][1]

    @property
    def is_word(self) -> bool:
        return not self.is_bit

    @property
    def radix(self) -> int:
        return DEVICE_TABLE[self.device][2]

    @property
    def ascii_code(self) -> str:
        return DEVICE_TABLE[self.device][3]

    def offset(self, n: int) -> "DeviceAddress":
        return DeviceAddress(self.device, self.number + n)

    def number_text(self, width: int = 0) -> str:
        r = self.radix
        if r == 16:
            s = format(self.number, "X")
        elif r == 8:
            s = format(self.number, "o")
        else:
            s = str(self.number)
        return s.rjust(width, "0") if width else s

    def __str__(self) -> str:
        return f"{self.device}{self.number_text()}"


def parse_device(text: str, xy_octal: bool = False) -> DeviceAddress:
    """Parse "M100" / "d 200" / "X1F" / "SM400".

    xy_octal=True treats X/Y numbers as octal (MELSEC iQ-F / FX5 convention).
    """
    if not text or not text.strip():
        raise DeviceAddressError("Empty device address")
    m = _ADDR_RE.match(text)
    if not m:
        raise DeviceAddressError(f"Invalid device address: '{text}'")
    dev = m.group(1).upper()
    num_text = m.group(2)
    if dev not in DEVICE_TABLE:
        # allow 1-letter prefix with hex digits parsed into the letters group (e.g. "DA" is invalid anyway)
        raise DeviceAddressError(f"Unknown device type '{dev}' in '{text}'")
    radix = DEVICE_TABLE[dev][2]
    if dev in ("X", "Y") and xy_octal:
        radix = 8
    try:
        number = int(num_text, radix)
    except ValueError as exc:
        raise DeviceAddressError(f"Invalid device number '{num_text}' for {dev} (radix {radix})") from exc
    if number < 0 or number > 0xFFFFFF:
        raise DeviceAddressError(f"Device number out of range: {text}")
    return DeviceAddress(dev, number)


def is_valid_device(text: str, xy_octal: bool = False) -> bool:
    try:
        parse_device(text, xy_octal)
        return True
    except DeviceAddressError:
        return False
