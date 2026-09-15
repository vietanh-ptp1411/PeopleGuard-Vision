"""Temporal filtering of a noisy boolean (YOLO miss for 1-2 frames must not flip the PLC).

Debouncer output turns ON only after the raw input has been continuously ON for
`on_delay_ms` AND at least `min_detection_frames` consecutive frames; it turns OFF
only after the raw input has been continuously OFF for `off_delay_ms`.
A short interruption while pending resets the pending timer; a short interruption
while ON is ignored (state is held).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class DebounceConfig:
    on_delay_ms: int = 200
    off_delay_ms: int = 1000
    min_detection_frames: int = 3

    def clamp(self) -> "DebounceConfig":
        self.on_delay_ms = max(0, int(self.on_delay_ms))
        self.off_delay_ms = max(0, int(self.off_delay_ms))
        self.min_detection_frames = max(1, int(self.min_detection_frames))
        return self


class Debouncer:
    def __init__(self, config: Optional[DebounceConfig] = None, initial: bool = False) -> None:
        self.config = (config or DebounceConfig()).clamp()
        self._output = bool(initial)
        self._pending_since: Optional[float] = None   # when raw first differed from output
        self._pending_frames = 0
        self._last_now = 0.0

    # ------------------------------------------------------------------ state
    @property
    def output(self) -> bool:
        return self._output

    @property
    def pending(self) -> bool:
        return self._pending_since is not None

    def pending_elapsed_ms(self, now: Optional[float] = None) -> float:
        if self._pending_since is None:
            return 0.0
        now = time.monotonic() if now is None else now
        return (now - self._pending_since) * 1000.0

    def pending_progress(self, now: Optional[float] = None) -> float:
        """0..1 progress of the pending timer (UI feedback)."""
        if self._pending_since is None:
            return 0.0
        target = self.config.off_delay_ms if self._output else self.config.on_delay_ms
        if target <= 0:
            return 1.0
        return max(0.0, min(1.0, self.pending_elapsed_ms(now) / target))

    @property
    def pending_frames(self) -> int:
        return self._pending_frames

    def set_config(self, config: DebounceConfig) -> None:
        self.config = config.clamp()

    def reset(self, value: bool = False) -> None:
        self._output = bool(value)
        self._pending_since = None
        self._pending_frames = 0

    # ------------------------------------------------------------------ update
    def update(self, raw: bool, now: Optional[float] = None) -> bool:
        """Feed one sample. Returns the filtered output."""
        now = time.monotonic() if now is None else now
        self._last_now = now
        raw = bool(raw)
        if raw == self._output:
            # Input agrees with output: cancel any pending change.
            self._pending_since = None
            self._pending_frames = 0
            return self._output

        # Input differs from output: start / continue pending timer.
        if self._pending_since is None:
            self._pending_since = now
            self._pending_frames = 0
        self._pending_frames += 1
        elapsed_ms = (now - self._pending_since) * 1000.0

        if raw:  # OFF -> ON candidate
            if elapsed_ms >= self.config.on_delay_ms and self._pending_frames >= self.config.min_detection_frames:
                self._commit(True)
        else:    # ON -> OFF candidate
            if elapsed_ms >= self.config.off_delay_ms:
                self._commit(False)
        return self._output

    def _commit(self, value: bool) -> None:
        self._output = value
        self._pending_since = None
        self._pending_frames = 0
