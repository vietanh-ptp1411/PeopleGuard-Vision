"""PLC driver interface. Add Modbus TCP / S7 / OPC UA / EtherNet/IP by subclassing this."""
from __future__ import annotations

import abc
from typing import List, Sequence, Tuple


class PlcError(Exception):
    """Communication or protocol error. The PLC worker turns this into PLC DISCONNECTED/FAULT."""


class BasePLC(abc.ABC):
    name: str = "base"

    def __init__(self) -> None:
        self.last_error: str = ""

    # -- connection ---------------------------------------------------------
    @abc.abstractmethod
    def connect(self) -> bool: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    # -- bits ----------------------------------------------------------------
    @abc.abstractmethod
    def read_bit(self, device: str) -> bool: ...

    @abc.abstractmethod
    def write_bit(self, device: str, value: bool) -> None: ...

    @abc.abstractmethod
    def read_bits(self, start_device: str, count: int) -> List[bool]: ...

    @abc.abstractmethod
    def write_bits(self, start_device: str, values: Sequence[bool]) -> None: ...

    # -- words ---------------------------------------------------------------
    @abc.abstractmethod
    def read_word(self, device: str) -> int: ...

    @abc.abstractmethod
    def write_word(self, device: str, value: int) -> None: ...

    @abc.abstractmethod
    def read_words(self, start_device: str, count: int) -> List[int]: ...

    @abc.abstractmethod
    def write_words(self, start_device: str, values: Sequence[int]) -> None: ...

    # -- diagnostics ---------------------------------------------------------
    def test_connection(self, probe_device: str = "SM400") -> Tuple[bool, str]:
        """Connect (if needed) and read one device. Returns (ok, message)."""
        try:
            if not self.is_connected() and not self.connect():
                return False, self.last_error or "connect failed"
            value = self.read_bit(probe_device) if _looks_like_bit(probe_device) else self.read_word(probe_device)
            return True, f"{probe_device} = {int(value)}"
        except PlcError as exc:
            return False, str(exc)

    @property
    def is_simulated(self) -> bool:
        return False


def _looks_like_bit(device: str) -> bool:
    from .device_address import parse_device

    try:
        return parse_device(device).is_bit
    except ValueError:
        return True
