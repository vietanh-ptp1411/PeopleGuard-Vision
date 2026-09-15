"""Mitsubishi MC Protocol (3E frame, TCP) driver: FX5U/iQ-F, Q, L, iQ-R with built-in Ethernet.

Thread-safe (one request at a time per connection). Retries `retry_count` times on
socket errors with an automatic reconnect; protocol end-code errors are not retried.
"""
from __future__ import annotations

import logging
import socket
import time
from typing import List, Optional, Sequence, Tuple

from ...config.schemas import FrameFormat, PlcConnectionConfig
from ..base_plc import BasePLC, PlcError
from ..device_address import DeviceAddress, DeviceAddressError, parse_device
from .mc_protocol import MAX_BATCH_BITS, MAX_BATCH_WORDS, McFrameConfig, McProtocol3E, McProtocolError

log = logging.getLogger("PLC")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise PlcError("Connection closed by PLC")
        buf.extend(chunk)
    return bytes(buf)


def series_uses_octal_xy(series: str) -> bool:
    s = (series or "").lower()
    return any(k in s for k in ("iq-f", "fx5", "fx3", "fx"))


class MitsubishiMCDriver(BasePLC):
    name = "Mitsubishi MC Protocol 3E"

    def __init__(self, cfg: PlcConnectionConfig) -> None:
        super().__init__()
        self.cfg = cfg
        timer_units = max(1, int(round(float(cfg.timeout_s) / 0.25)))
        self.proto = McProtocol3E(
            McFrameConfig(
                network_no=int(cfg.network_no),
                pc_no=int(cfg.pc_no),
                dest_module_io=int(cfg.dest_module_io),
                dest_module_station=int(cfg.dest_module_station),
                monitoring_timer=min(timer_units, 0xFFFF),
                ascii=str(cfg.frame_format).lower() == FrameFormat.ASCII.value,
            )
        )
        self.xy_octal = series_uses_octal_xy(cfg.plc_series)
        self._sock: Optional[socket.socket] = None
        self._lock = __import__("threading").RLock()
        self.last_latency_ms: float = 0.0

    # ------------------------------------------------------------------ connection
    def connect(self) -> bool:
        with self._lock:
            self._close()
            try:
                sock = socket.create_connection((self.cfg.ip, int(self.cfg.port)), timeout=float(self.cfg.timeout_s))
                sock.settimeout(float(self.cfg.timeout_s))
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._sock = sock
                self.last_error = ""
                log.info("MC Protocol connected to %s:%s (%s, %s)", self.cfg.ip, self.cfg.port, self.cfg.frame,
                         "ASCII" if self.proto.ascii else "Binary")
                return True
            except (OSError, ValueError) as exc:
                self.last_error = f"Cannot connect to PLC {self.cfg.ip}:{self.cfg.port}: {exc}"
                log.warning(self.last_error)
                return False

    def disconnect(self) -> None:
        with self._lock:
            if self._sock is not None:
                log.info("MC Protocol disconnected from %s:%s", self.cfg.ip, self.cfg.port)
            self._close()

    def _close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def is_connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------ transaction
    def _transact(self, request: bytes) -> bytes:
        attempts = max(1, int(self.cfg.retry_count) + 1)
        with self._lock:
            last_exc: Optional[Exception] = None
            for attempt in range(attempts):
                if self._sock is None:
                    if attempt == 0 or not self.connect():
                        raise PlcError(self.last_error or "PLC not connected")
                sock = self._sock
                assert sock is not None
                try:
                    t0 = time.perf_counter()
                    sock.sendall(request)
                    header = _recv_exact(sock, self.proto.header_size)
                    remaining = self.proto.parse_header(header)
                    rest = _recv_exact(sock, remaining) if remaining > 0 else b""
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
                    return self.proto.parse_response(header + rest)
                except McProtocolError:
                    raise  # PLC answered: a retry will not help
                except (socket.timeout, OSError, PlcError) as exc:
                    last_exc = exc
                    self.last_error = f"MC communication error: {exc}"
                    log.warning("%s (attempt %d/%d)", self.last_error, attempt + 1, attempts)
                    self._close()
            raise PlcError(self.last_error or f"MC communication failed: {last_exc}")

    def _addr(self, device: str) -> DeviceAddress:
        try:
            return parse_device(device, self.xy_octal)
        except DeviceAddressError as exc:
            raise PlcError(str(exc)) from exc

    # ------------------------------------------------------------------ bits
    def read_bit(self, device: str) -> bool:
        return self.read_bits(device, 1)[0]

    def read_bits(self, start_device: str, count: int) -> List[bool]:
        addr = self._addr(start_device)
        if not addr.is_bit:
            raise PlcError(f"{start_device} is a word device; use read_word()")
        if not 1 <= count <= MAX_BATCH_BITS:
            raise PlcError(f"Bit count {count} out of range")
        data = self._transact(self.proto.build_batch_read(addr, count, bit_units=True))
        return self.proto.decode_bits(data, count)

    def write_bit(self, device: str, value: bool) -> None:
        self.write_bits(device, [bool(value)])

    def write_bits(self, start_device: str, values: Sequence[bool]) -> None:
        addr = self._addr(start_device)
        if not addr.is_bit:
            raise PlcError(f"{start_device} is a word device; use write_word()")
        self._transact(self.proto.build_batch_write_bits(addr, [bool(v) for v in values]))

    def write_bits_random(self, items: Sequence[Tuple[str, bool]]) -> None:
        """Write several non-contiguous bits in ONE request (command 0x1402)."""
        addrs = []
        for dev, val in items:
            a = self._addr(dev)
            if not a.is_bit:
                raise PlcError(f"{dev} is not a bit device")
            addrs.append((a, bool(val)))
        if not addrs:
            return
        self._transact(self.proto.build_random_write_bits(addrs))

    # ------------------------------------------------------------------ words
    def read_word(self, device: str) -> int:
        return self.read_words(device, 1)[0]

    def read_words(self, start_device: str, count: int) -> List[int]:
        addr = self._addr(start_device)
        if not 1 <= count <= MAX_BATCH_WORDS:
            raise PlcError(f"Word count {count} out of range")
        data = self._transact(self.proto.build_batch_read(addr, count, bit_units=False))
        return self.proto.decode_words(data, count)

    def write_word(self, device: str, value: int) -> None:
        self.write_words(device, [int(value)])

    def write_words(self, start_device: str, values: Sequence[int]) -> None:
        addr = self._addr(start_device)
        self._transact(self.proto.build_batch_write_words(addr, [int(v) for v in values]))

    def read_signed_word(self, device: str) -> int:
        v = self.read_word(device)
        return v - 0x10000 if v & 0x8000 else v
