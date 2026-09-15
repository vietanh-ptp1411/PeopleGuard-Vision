"""MC Protocol (SLMP) 3E frame encoder/decoder - Binary and ASCII. No sockets here.

Supported commands (enough for monitoring I/O and easy to extend):
    0x0401 Batch read   (bit units / word units)
    0x1401 Batch write  (bit units / word units)
    0x1402 Random write (bit units)  -> several non-contiguous bits in one request
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Sequence, Tuple

from ..base_plc import PlcError
from ..device_address import DeviceAddress

# Batch limits (3E frame, Q/L/iQ-R/iQ-F). Conservative values.
MAX_BATCH_BITS = 3584
MAX_BATCH_WORDS = 960
MAX_RANDOM_BITS = 188


class McCommand(IntEnum):
    BATCH_READ = 0x0401
    BATCH_WRITE = 0x1401
    RANDOM_WRITE = 0x1402


END_CODE_MESSAGES: Dict[int, str] = {
    0x0000: "OK",
    0xC050: "Communication data code mismatch (ASCII/Binary) - check GX Works 'Communication Data Code'",
    0xC051: "Too many bit device points",
    0xC052: "Too many word device points",
    0xC053: "Too many random bit points",
    0xC054: "Too many random word points",
    0xC056: "Device number out of range",
    0xC058: "Request data length does not match device points",
    0xC059: "Command/subcommand not supported by this CPU",
    0xC05B: "CPU cannot read/write the specified device",
    0xC05C: "Request content error (e.g. bit-unit access to a word device)",
    0xC05D: "Monitor registration not done",
    0xC05F: "Request cannot be executed on the target CPU",
    0xC060: "Request content error (bit value must be 0/1)",
    0xC061: "Request data length error",
    0xC06F: "Frame format mismatch (3E/4E) or ASCII/Binary mismatch",
    0xC070: "Device memory extension not available for target station",
    0xC0B5: "Data not usable by the CPU",
    0xC200: "Remote password error",
    0xC204: "Remote password not unlocked for this connection",
    0x4031: "Device number out of range for this CPU",
}


class McProtocolError(PlcError):
    def __init__(self, end_code: int) -> None:
        self.end_code = end_code
        msg = END_CODE_MESSAGES.get(end_code, "Unknown end code")
        super().__init__(f"PLC returned end code 0x{end_code:04X}: {msg}")


@dataclass
class McFrameConfig:
    network_no: int = 0x00
    pc_no: int = 0xFF
    dest_module_io: int = 0x03FF
    dest_module_station: int = 0x00
    monitoring_timer: int = 8        # x250 ms  -> 2 s
    ascii: bool = False


class McProtocol3E:
    """Builds requests and parses responses for one connection configuration."""

    BINARY_HEADER = 9   # 2 sub + 1 net + 1 pc + 2 io + 1 station + 2 length
    ASCII_HEADER = 18   # 4 sub + 2 net + 2 pc + 4 io + 2 station + 4 length

    def __init__(self, cfg: McFrameConfig) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ framing
    @property
    def ascii(self) -> bool:
        return bool(self.cfg.ascii)

    @property
    def header_size(self) -> int:
        return self.ASCII_HEADER if self.ascii else self.BINARY_HEADER

    def parse_header(self, header: bytes) -> int:
        """Return the number of bytes that follow the header (end code + data)."""
        if len(header) < self.header_size:
            raise PlcError(f"Short header ({len(header)} bytes)")
        if self.ascii:
            text = header.decode("ascii", errors="replace")
            if text[:4] != "D000":
                raise PlcError(f"Unexpected response subheader '{text[:4]}' (expected D000)")
            return int(text[14:18], 16)
        if header[:2] != b"\xD0\x00":
            raise PlcError(f"Unexpected response subheader {header[:2].hex()} (expected D000)")
        return struct.unpack_from("<H", header, 7)[0]

    def _wrap(self, body: bytes | str) -> bytes:
        c = self.cfg
        if self.ascii:
            assert isinstance(body, str)
            payload = f"{c.monitoring_timer:04X}" + body
            head = f"5000{c.network_no:02X}{c.pc_no:02X}{c.dest_module_io:04X}{c.dest_module_station:02X}{len(payload):04X}"
            return (head + payload).encode("ascii")
        assert isinstance(body, bytes)
        payload = struct.pack("<H", c.monitoring_timer) + body
        head = b"\x50\x00" + bytes([c.network_no & 0xFF, c.pc_no & 0xFF]) + struct.pack("<H", c.dest_module_io) + bytes(
            [c.dest_module_station & 0xFF]
        ) + struct.pack("<H", len(payload))
        return head + payload

    def _device(self, addr: DeviceAddress) -> bytes | str:
        if self.ascii:
            return addr.ascii_code + addr.number_text(6)
        return struct.pack("<I", addr.number)[:3] + bytes([addr.code])

    # ------------------------------------------------------------------ requests
    def build_batch_read(self, addr: DeviceAddress, count: int, bit_units: bool) -> bytes:
        limit = MAX_BATCH_BITS if bit_units else MAX_BATCH_WORDS
        if not 1 <= count <= limit:
            raise PlcError(f"Batch read count {count} out of range 1..{limit}")
        sub = 0x0001 if bit_units else 0x0000
        if self.ascii:
            return self._wrap(f"{McCommand.BATCH_READ:04X}{sub:04X}" + self._device(addr) + f"{count:04X}")  # type: ignore[operator]
        return self._wrap(struct.pack("<HH", McCommand.BATCH_READ, sub) + self._device(addr) + struct.pack("<H", count))  # type: ignore[operator]

    def build_batch_write_bits(self, addr: DeviceAddress, values: Sequence[bool]) -> bytes:
        n = len(values)
        if not 1 <= n <= MAX_BATCH_BITS:
            raise PlcError(f"Batch write bit count {n} out of range")
        if self.ascii:
            data = "".join("1" if v else "0" for v in values)
            return self._wrap(f"{McCommand.BATCH_WRITE:04X}0001" + self._device(addr) + f"{n:04X}" + data)  # type: ignore[operator]
        packed = bytearray((n + 1) // 2)
        for i, v in enumerate(values):
            if v:
                packed[i // 2] |= 0x10 if i % 2 == 0 else 0x01
        return self._wrap(struct.pack("<HH", McCommand.BATCH_WRITE, 0x0001) + self._device(addr) + struct.pack("<H", n) + bytes(packed))  # type: ignore[operator]

    def build_batch_write_words(self, addr: DeviceAddress, values: Sequence[int]) -> bytes:
        n = len(values)
        if not 1 <= n <= MAX_BATCH_WORDS:
            raise PlcError(f"Batch write word count {n} out of range")
        if self.ascii:
            data = "".join(f"{int(v) & 0xFFFF:04X}" for v in values)
            return self._wrap(f"{McCommand.BATCH_WRITE:04X}0000" + self._device(addr) + f"{n:04X}" + data)  # type: ignore[operator]
        data_b = b"".join(struct.pack("<H", int(v) & 0xFFFF) for v in values)
        return self._wrap(struct.pack("<HH", McCommand.BATCH_WRITE, 0x0000) + self._device(addr) + struct.pack("<H", n) + data_b)  # type: ignore[operator]

    def build_random_write_bits(self, items: Sequence[Tuple[DeviceAddress, bool]]) -> bytes:
        n = len(items)
        if not 1 <= n <= MAX_RANDOM_BITS:
            raise PlcError(f"Random write bit count {n} out of range")
        if self.ascii:
            body = f"{McCommand.RANDOM_WRITE:04X}0001{n:02X}" + "".join(
                self._device(a) + ("01" if v else "00") for a, v in items  # type: ignore[operator]
            )
            return self._wrap(body)
        body = struct.pack("<HH", McCommand.RANDOM_WRITE, 0x0001) + bytes([n]) + b"".join(
            self._device(a) + bytes([1 if v else 0]) for a, v in items  # type: ignore[operator]
        )
        return self._wrap(body)

    # ------------------------------------------------------------------ responses
    def parse_response(self, frame: bytes) -> bytes:
        """Validate a full response frame and return the data part. Raises McProtocolError."""
        if self.ascii:
            text = frame.decode("ascii", errors="replace")
            if len(text) < self.ASCII_HEADER + 4:
                raise PlcError("Short ASCII response")
            length = int(text[14:18], 16)
            end_code = int(text[18:22], 16)
            if end_code != 0:
                raise McProtocolError(end_code)
            return text[22 : 18 + length].encode("ascii")
        if len(frame) < self.BINARY_HEADER + 2:
            raise PlcError("Short binary response")
        length = struct.unpack_from("<H", frame, 7)[0]
        end_code = struct.unpack_from("<H", frame, 9)[0]
        if end_code != 0:
            raise McProtocolError(end_code)
        return frame[11 : 9 + length]

    def decode_bits(self, data: bytes, count: int) -> List[bool]:
        out: List[bool] = []
        if self.ascii:
            text = data.decode("ascii", errors="replace")
            if len(text) < count:
                raise PlcError(f"Bit data too short ({len(text)} < {count})")
            return [text[i] == "1" for i in range(count)]
        if len(data) < (count + 1) // 2:
            raise PlcError(f"Bit data too short ({len(data)} bytes for {count} points)")
        for i in range(count):
            byte = data[i // 2]
            out.append(bool((byte >> 4) & 1) if i % 2 == 0 else bool(byte & 1))
        return out

    def decode_words(self, data: bytes, count: int) -> List[int]:
        if self.ascii:
            text = data.decode("ascii", errors="replace")
            if len(text) < count * 4:
                raise PlcError(f"Word data too short ({len(text)} < {count * 4})")
            return [int(text[i * 4 : i * 4 + 4], 16) for i in range(count)]
        if len(data) < count * 2:
            raise PlcError(f"Word data too short ({len(data)} bytes for {count} words)")
        return list(struct.unpack_from(f"<{count}H", data, 0))


def describe_end_code(end_code: int) -> str:
    return END_CODE_MESSAGES.get(end_code, "Unknown end code")
