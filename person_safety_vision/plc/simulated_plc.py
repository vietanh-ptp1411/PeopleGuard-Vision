"""In-memory PLC used by Simulation Mode: demo the whole pipeline without hardware."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Sequence, Tuple

from .base_plc import BasePLC, PlcError
from .device_address import DeviceAddress, DeviceAddressError, parse_device

log = logging.getLogger("PLC")

ChangeListener = Callable[[str, int], None]


class SimulatedPLC(BasePLC):
    name = "Simulated PLC (virtual memory)"

    def __init__(self, latency_ms: float = 0.5, xy_octal: bool = False) -> None:
        super().__init__()
        self.latency_ms = float(latency_ms)
        self.xy_octal = xy_octal
        self._bits: Dict[Tuple[str, int], bool] = {}
        self._words: Dict[Tuple[str, int], int] = {}
        self._connected = False
        self._lock = threading.RLock()
        self._listeners: List[ChangeListener] = []
        self.write_count = 0

    @property
    def is_simulated(self) -> bool:
        return True

    def add_listener(self, cb: ChangeListener) -> None:
        self._listeners.append(cb)

    def _notify(self, device: str, value: int) -> None:
        for cb in list(self._listeners):
            try:
                cb(device, value)
            except Exception as exc:
                log.debug("sim listener error: %s", exc)

    # ------------------------------------------------------------------ connection
    def connect(self) -> bool:
        self._connected = True
        log.info("Simulated PLC connected (virtual memory)")
        return True

    def disconnect(self) -> None:
        if self._connected:
            log.info("Simulated PLC disconnected")
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _check(self) -> None:
        if not self._connected:
            raise PlcError("Simulated PLC not connected")
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)

    def _addr(self, device: str) -> DeviceAddress:
        try:
            return parse_device(device, self.xy_octal)
        except DeviceAddressError as exc:
            raise PlcError(str(exc)) from exc

    # ------------------------------------------------------------------ bits
    def read_bit(self, device: str) -> bool:
        return self.read_bits(device, 1)[0]

    def read_bits(self, start_device: str, count: int) -> List[bool]:
        self._check()
        a = self._addr(start_device)
        if not a.is_bit:
            raise PlcError(f"{start_device} is a word device")
        with self._lock:
            return [self._bits.get((a.device, a.number + i), False) for i in range(count)]

    def write_bit(self, device: str, value: bool) -> None:
        self.write_bits(device, [bool(value)])

    def write_bits(self, start_device: str, values: Sequence[bool]) -> None:
        self._check()
        a = self._addr(start_device)
        if not a.is_bit:
            raise PlcError(f"{start_device} is a word device")
        changed = []
        with self._lock:
            for i, v in enumerate(values):
                key = (a.device, a.number + i)
                if self._bits.get(key, False) != bool(v):
                    self._bits[key] = bool(v)
                    changed.append((str(DeviceAddress(*key)), int(bool(v))))
            self.write_count += len(values)
        for dev, val in changed:
            self._notify(dev, val)

    def write_bits_random(self, items: Sequence[Tuple[str, bool]]) -> None:
        for dev, val in items:
            self.write_bit(dev, val)

    # ------------------------------------------------------------------ words
    def read_word(self, device: str) -> int:
        return self.read_words(device, 1)[0]

    def read_words(self, start_device: str, count: int) -> List[int]:
        self._check()
        a = self._addr(start_device)
        with self._lock:
            if a.is_word:
                return [self._words.get((a.device, a.number + i), 0) & 0xFFFF for i in range(count)]
            out = []
            for w in range(count):
                v = 0
                for b in range(16):
                    if self._bits.get((a.device, a.number + w * 16 + b), False):
                        v |= 1 << b
                out.append(v)
            return out

    def write_word(self, device: str, value: int) -> None:
        self.write_words(device, [int(value)])

    def write_words(self, start_device: str, values: Sequence[int]) -> None:
        self._check()
        a = self._addr(start_device)
        changed = []
        with self._lock:
            for i, v in enumerate(values):
                v &= 0xFFFF
                if a.is_word:
                    key = (a.device, a.number + i)
                    if self._words.get(key, 0) != v:
                        self._words[key] = v
                        changed.append((str(DeviceAddress(*key)), v))
                else:
                    for b in range(16):
                        key = (a.device, a.number + i * 16 + b)
                        bit = bool((v >> b) & 1)
                        if self._bits.get(key, False) != bit:
                            self._bits[key] = bit
                            changed.append((str(DeviceAddress(*key)), int(bit)))
            self.write_count += len(values)
        for dev, val in changed:
            self._notify(dev, val)

    # ------------------------------------------------------------------ inspection
    def memory_snapshot(self) -> Dict[str, int]:
        """Sorted view of every device touched so far (for the Virtual PLC panel)."""
        with self._lock:
            items = [(DeviceAddress(*k), int(v)) for k, v in self._bits.items()]
            items += [(DeviceAddress(*k), int(v)) for k, v in self._words.items()]
        items.sort(key=lambda kv: (kv[0].device, kv[0].number))
        return {str(a): v for a, v in items}

    def reset_memory(self) -> None:
        with self._lock:
            self._bits.clear()
            self._words.clear()
