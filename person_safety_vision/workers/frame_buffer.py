"""Single-slot frame buffer: the inference worker always gets the LATEST frame, older ones are dropped."""
from __future__ import annotations

import threading
from typing import Optional

from ..camera.base_camera import Frame


class LatestFrameBuffer:
    def __init__(self) -> None:
        self._frame: Optional[Frame] = None
        self._cond = threading.Condition()
        self.dropped = 0
        self.received = 0

    def put(self, frame: Frame) -> None:
        with self._cond:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame
            self.received += 1
            self._cond.notify()

    def get(self, timeout: float = 0.5) -> Optional[Frame]:
        with self._cond:
            if self._frame is None:
                self._cond.wait(timeout)
            frame, self._frame = self._frame, None
            return frame

    def peek(self) -> Optional[Frame]:
        with self._cond:
            return self._frame

    def clear(self) -> None:
        with self._cond:
            self._frame = None

    def reset_stats(self) -> None:
        with self._cond:
            self.dropped = 0
            self.received = 0
