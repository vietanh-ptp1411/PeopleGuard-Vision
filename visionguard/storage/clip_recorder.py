"""Record the stretch of video in which a person was inside a zone - one file per camera.

Two details make the difference between a clip you can use and one you cannot:

    pre-roll    A person is only "in the zone" after the debounce has agreed, which is
                hundreds of milliseconds after they walked in. Recording from that instant
                always loses the entry. So every frame is kept in a short ring buffer, and
                when the zone trips the buffer is flushed into the file first: the clip
                starts before the event that caused it.

    post-roll   The zone clearing is not the end of the story either - you want to see the
                person walk out. Recording continues for a few seconds, and if they step
                back in during that window it is the same clip, not a new one.

Writing happens on the recorder's own thread. The camera thread only hands over a frame,
and if the disk falls behind, frames are dropped rather than queued without limit - a
monitoring system must never stall its camera to finish a recording.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from ..config.schemas import ClipConfig

log = logging.getLogger("CLIP")

FOURCC = "mp4v"


class ClipRecorder:
    """One camera's clip writer. Thread safe: submit() and set_occupied() from anywhere."""

    def __init__(self, camera: int, label: str, config: ClipConfig) -> None:
        self.camera = int(camera)
        self.label = label or f"Camera {camera + 1}"
        self.config = replace(config)
        self._occupied = False
        self._zone = ""
        self._running = True
        self._lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        self._pending = None
        self._pending_bytes = 0
        self._active_bytes = 0
        self._active_size = None
        self._config_revision = 0
        self._active_revision = 0
        self._configuration_changed = False
        self._pre_bytes = 0
        self._next_sample = 0.0
        self.dropped = 0
        self.high_water_bytes = 0
        self._thread = threading.Thread(target=self._run, name=f"clip-{camera}", daemon=True)
        # writer state, touched only by the recorder thread
        self._writer: Optional[cv2.VideoWriter] = None
        self._path: Optional[Path] = None
        self._pre: deque = deque()
        self._started_at = 0.0
        self._post_deadline = 0.0
        self._size: Optional[Tuple[int, int]] = None
        self._writer_fps = 15.0
        self._last_write_stamp = None
        self._thread.start()

    # ------------------------------------------------------------------ API
    def set_config(self, config: ClipConfig) -> None:
        with self._ready:
            if config == self.config:
                return
            self.config = replace(config)
            self._config_revision += 1
            self._configuration_changed = True
            self._next_sample = 0.0
            # Release queued frames immediately, including when recording is disabled.
            # An in-flight resize keeps its original size reservation until it returns.
            self._pending = None
            self._pending_bytes = 0
            self._pre.clear()
            self._pre_bytes = 0
            self._ready.notify_all()

    def set_label(self, label: str) -> None:
        self.label = label or self.label

    def submit(self, image: np.ndarray, timestamp: float = 0.0) -> None:
        """Keep one immutable camera image; resize/encode run on the recorder thread.

        timestamp is monotonic (not the frame's wall-clock timestamp).
        """
        if image is None:
            return
        stamp = timestamp or time.monotonic()
        with self._ready:
            if not self.config.enabled or not self._running or stamp < self._next_sample:
                return
            fps = self._capture_fps()
            width, height = self._prepared_size(image)
            charge = int(image.nbytes) + width * height * 3
            self._next_sample = stamp + 1.0 / fps
            budget = self._budget()
            if charge + self._active_bytes > budget:
                self.dropped += 1
                return
            while self._pre and self._pre_bytes + charge + self._active_bytes > budget:
                old, _ = self._pre.popleft()
                self._pre_bytes -= old.nbytes
            if self._pending is not None:
                self.dropped += 1
            self._pending = (image, stamp, (width, height), self._config_revision)
            self._pending_bytes = charge
            self.high_water_bytes = max(self.high_water_bytes, self.buffer_bytes_locked())
            self._ready.notify()

    def _budget(self) -> int:
        return max(1, int(self.config.max_buffer_mb)) * 1024 ** 2

    def buffer_bytes_locked(self) -> int:
        return self._pending_bytes + self._active_bytes + self._pre_bytes

    @property
    def buffer_bytes(self) -> int:
        with self._lock:
            return self.buffer_bytes_locked()

    def _capture_fps(self) -> float:
        return max(1.0, min(60.0, float(self.config.fps or self.config.capture_fps or 15.0)))

    def set_occupied(self, occupied: bool, zone: str = "") -> None:
        with self._lock:
            self._occupied = bool(occupied)
            if occupied and zone:
                self._zone = zone

    def stop(self, timeout: float = 3.0) -> bool:
        with self._ready:
            self._running = False
            self._pending = None
            self._pending_bytes = 0
            self._ready.notify_all()
        if timeout:
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    # ------------------------------------------------------------------ internals
    def _prepared_size(self, image: np.ndarray) -> Tuple[int, int]:
        h, w = image.shape[:2]
        scale = max(0.1, min(1.0, float(self.config.scale or 1.0)))
        scale = min(scale, max(2, self.config.max_width) / max(1, w))
        return max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        size = self._active_size or self._prepared_size(image)
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)

    def _run(self) -> None:
        try:
            while self._running:
                with self._ready:
                    if self._pending is None and self._running and not self._configuration_changed:
                        self._ready.wait(0.2)
                    if not self._running:
                        break
                    item, self._pending = self._pending, None
                    self._active_bytes, self._pending_bytes = self._pending_bytes, 0
                    changed, self._configuration_changed = self._configuration_changed, False
                    self._active_revision = self._config_revision
                if changed:
                    self._close("configuration change")
                if item is None:
                    self._tick(None, time.monotonic())
                    continue
                raw, stamp, self._active_size, self._active_revision = item
                image = self._prepare(raw)
                self._active_size = None
                del raw, item
                with self._lock:
                    self._active_bytes = image.nbytes
                self._tick(image, stamp)
                del image
                with self._lock:
                    self._active_bytes = 0
        except Exception:
            log.exception("%s: recorder stopped after an error", self.label)
        finally:
            self._running = False
            self._close("shutdown")
            with self._lock:
                self._pending = None
                self._pre.clear()
                self._pre_bytes = self._pending_bytes = self._active_bytes = 0

    def _tick(self, image: Optional[np.ndarray], stamp: float) -> None:
        with self._lock:
            if not self.config.enabled or self._active_revision != self._config_revision:
                return
            occupied, zone = self._occupied, self._zone

        if image is not None:
            if self._writer is None:
                self._remember(image, stamp)
            else:
                self._write(image, stamp)

        if occupied and self._writer is None:
            self._open(zone, stamp)
        elif occupied and self._writer is not None:
            self._post_deadline = 0.0
            if (stamp - self._started_at) > max(5.0, float(self.config.max_duration_s)):
                self._close("length limit")
                if image is not None:
                    self._remember(image, stamp)
                self._open(zone, stamp)          # keep going in a new part
        elif not occupied and self._writer is not None:
            if self._post_deadline == 0.0:
                self._post_deadline = stamp + max(0.0, float(self.config.post_roll_s))
            elif stamp >= self._post_deadline:
                self._close("zone clear")

    def _remember(self, image: np.ndarray, stamp: float) -> None:
        """Ring buffer of the seconds before anything happened."""
        window = max(0.0, float(self.config.pre_roll_s))
        with self._lock:
            if not self.config.enabled or self._active_revision != self._config_revision:
                return
            if self._pre and self._pre[-1][0].shape != image.shape:
                self._pre.clear()
                self._pre_bytes = 0
            self._pre.append((image, stamp))
            self._pre_bytes += image.nbytes
            # The prepared image is transferred from active to pre-roll ownership.
            self._active_bytes = 0
            while self._pre and ((stamp - self._pre[0][1]) > window or
                                 self.buffer_bytes_locked() > self._budget()):
                old, _ = self._pre.popleft()
                self._pre_bytes -= old.nbytes
            self.high_water_bytes = max(self.high_water_bytes, self.buffer_bytes_locked())

    def _estimated_fps(self) -> float:
        if self.config.fps and self.config.fps > 0:
            return float(self.config.fps)
        with self._lock:
            stamps = [s for _, s in self._pre]
        gaps = [b - a for a, b in zip(stamps, stamps[1:]) if 0.001 < (b - a) < 1.0]
        if not gaps:
            return self._capture_fps()
        gaps.sort()
        return max(1.0, min(60.0, 1.0 / gaps[len(gaps) // 2]))

    def _open(self, zone: str, stamp: float) -> None:
        with self._lock:
            sample = self._pre[0][0] if self._pre else None
        if sample is None:
            return                                  # nothing to size the file from yet
        when = datetime.now()
        # A label of "!!!" or an emoji slugs away to nothing, and an empty folder name
        # quietly collapses the per-camera split - every camera then writes into the date
        # folder, where two of them tripping in the same second overwrite each other.
        folder = (Path(self.config.directory) / when.strftime("%Y-%m-%d")
                  / (self._slug(self.label) or f"camera-{self.camera + 1}"))
        tag = self._slug(zone) or "PERSON"
        path = folder / f"{when.strftime('%H-%M-%S-%f')}_cam{self.camera + 1}_{tag}.mp4"
        height, width = sample.shape[:2]
        del sample  # Do not retain an uncharged frame while draining the ring buffer.
        fps = self._estimated_fps()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*FOURCC), fps, (width, height))
            if not writer.isOpened():
                log.error("Cannot open %s for writing", path)
                return
        except Exception as exc:
            log.error("Clip writer failed for %s: %s", path, exc)
            return
        self._writer, self._path, self._size = writer, path, (width, height)
        self._writer_fps = fps
        self._last_write_stamp = None
        self._started_at, self._post_deadline = stamp, 0.0
        # Drain one at a time: pending frames remain inside the shared byte budget.
        while True:
            with self._lock:
                if not self._pre:
                    break
                image, timestamp = self._pre.popleft()
                self._pre_bytes -= image.nbytes
                self._active_bytes = image.nbytes
            self._write(image, timestamp)
        log.info("%s: recording %s (%.0f fps, %dx%d, %.1fs pre-roll)",
                 self.label, path.name, fps, width, height, float(self.config.pre_roll_s))

    def _write(self, image: np.ndarray, stamp: float) -> None:
        if self._writer is None or self._size is None:
            return
        if (image.shape[1], image.shape[0]) != self._size:
            # A stream resolution change starts a new clip, avoiding an additional
            # full-size resize allocation outside the shared memory reservation.
            self._close("resolution change")
            self._remember(image, stamp)
            return
        try:
            # Use source time so sampled/dropped frames do not speed the clip up.
            if self._last_write_stamp is None:
                self._last_write_stamp = stamp - 1.0 / self._writer_fps
            count = int(round((stamp - self._last_write_stamp) * self._writer_fps))
            if count > self._writer_fps * 2:
                # A long camera outage starts a new segment rather than encoding a huge freeze.
                self._close("camera gap")
                self._remember(image, stamp)
                return
            for _ in range(max(0, count)):
                self._writer.write(image)
                self._last_write_stamp += 1.0 / self._writer_fps
        except Exception as exc:
            log.error("Clip write failed: %s", exc)
            self._close("write error")

    def _close(self, reason: str) -> Optional[Path]:
        if self._writer is None:
            return None
        path = self._path
        try:
            self._writer.release()
        except Exception:
            pass
        self._writer, self._path, self._size = None, None, None
        self._post_deadline = 0.0
        if path is not None:
            size_mb = path.stat().st_size / 1e6 if path.exists() else 0.0
            log.info("%s: clip saved %s (%.1f MB, %s)", self.label, path, size_mb, reason)
        return path

    @staticmethod
    def _slug(text: str) -> str:
        return "".join(c for c in str(text) if c.isalnum() or c in "-_") [:40]
