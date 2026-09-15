"""CameraEventProvider: the interface every AI camera event source implements.

Pull based on purpose: the event worker thread owns the loop and calls poll(), so there is
no hidden thread per provider and no callback running on an unknown thread.

    provider.connect()            # reach the camera, verify credentials
    provider.start_listening()    # open the long lived alarm stream
    while running:
        for event in provider.poll(0.5):
            ...
    provider.stop_listening(); provider.disconnect()

Implementations must never raise out of these methods: return False / [] and put the reason
in `last_error`, so a wrong password or an unplugged camera can never take the app down.
"""
from __future__ import annotations

import abc
import logging
from typing import Callable, List, Optional

from .camera_event import CameraEvent

log = logging.getLogger("EVENT")

RawListener = Callable[[str], None]


class CameraEventProvider(abc.ABC):
    name: str = "base"
    provider_type: str = "base"

    def __init__(self) -> None:
        self._connected = False
        self._listening = False
        self.last_error: str = ""
        self._raw_listener: Optional[RawListener] = None

    # ------------------------------------------------------------------ lifecycle
    @abc.abstractmethod
    def connect(self) -> bool:
        """Check that the camera answers and the credentials work."""

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def start_listening(self) -> bool:
        """Open the event stream. Returns False and sets last_error on failure."""

    @abc.abstractmethod
    def stop_listening(self) -> None: ...

    @abc.abstractmethod
    def poll(self, timeout: float = 0.5) -> List[CameraEvent]:
        """Read the stream for at most `timeout` seconds. [] means 'nothing yet', not an error.

        Set `self._connected = False` when the channel is really dead so the worker reconnects.
        """

    # ------------------------------------------------------------------ state
    def is_connected(self) -> bool:
        return self._connected

    def is_listening(self) -> bool:
        return self._listening

    def health_check(self) -> bool:
        """Optional periodic probe (a stream that sends nothing is not proof of life)."""
        return self._connected

    def describe(self) -> str:
        return self.name

    # ------------------------------------------------------------------ raw debug feed
    def set_raw_listener(self, listener: Optional[RawListener]) -> None:
        """Receive every payload exactly as the camera sent it (RAW EVENT tab)."""
        self._raw_listener = listener

    def _emit_raw(self, payload: str) -> None:
        if self._raw_listener is None or not payload:
            return
        try:
            self._raw_listener(payload)
        except Exception as exc:
            log.debug("raw listener failed: %s", exc)

    # ------------------------------------------------------------------ simulation hooks
    @property
    def supports_simulation(self) -> bool:
        return False

    def simulate(self, event: CameraEvent) -> None:
        """Only the mock provider injects events; ignored elsewhere."""
