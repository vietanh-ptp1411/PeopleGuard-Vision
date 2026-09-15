"""Small performance helpers: FPS counter and moving-average timers."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class FpsCounter:
    """Sliding-window FPS estimate. Call tick() once per frame."""

    window: float = 2.0
    _stamps: Deque[float] = field(default_factory=deque, repr=False)

    def tick(self, now: float | None = None) -> float:
        now = time.perf_counter() if now is None else now
        self._stamps.append(now)
        cutoff = now - self.window
        while self._stamps and self._stamps[0] < cutoff:
            self._stamps.popleft()
        return self.fps

    @property
    def fps(self) -> float:
        if len(self._stamps) < 2:
            return 0.0
        span = self._stamps[-1] - self._stamps[0]
        return (len(self._stamps) - 1) / span if span > 0 else 0.0

    def reset(self) -> None:
        self._stamps.clear()


@dataclass
class MovingAverage:
    """Moving average of the last N samples (e.g. inference ms, PLC latency ms)."""

    size: int = 30
    _samples: Deque[float] = field(default_factory=deque, repr=False)

    def add(self, value: float) -> float:
        self._samples.append(value)
        while len(self._samples) > self.size:
            self._samples.popleft()
        return self.value

    @property
    def value(self) -> float:
        return sum(self._samples) / len(self._samples) if self._samples else 0.0

    @property
    def last(self) -> float:
        return self._samples[-1] if self._samples else 0.0

    def reset(self) -> None:
        self._samples.clear()


class Stopwatch:
    """Context manager measuring elapsed milliseconds."""

    def __init__(self) -> None:
        self.elapsed_ms: float = 0.0
        self._t0 = 0.0

    def __enter__(self) -> "Stopwatch":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
