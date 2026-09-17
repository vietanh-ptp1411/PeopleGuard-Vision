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
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from ..config.schemas import ClipConfig

log = logging.getLogger("CLIP")

#: frames waiting to be written; beyond this the oldest are dropped
QUEUE_LIMIT = 120
FOURCC = "mp4v"


class ClipRecorder:
    """One camera's clip writer. Thread safe: submit() and set_occupied() from anywhere."""

    def __init__(self, camera: int, label: str, config: ClipConfig) -> None:
        self.camera = int(camera)
        self.label = label or f"Camera {camera + 1}"
        self.config = config
        self._queue: "queue.Queue[Optional[Tuple[np.ndarray, float]]]" = queue.Queue(QUEUE_LIMIT)
        self._occupied = False
        self._zone = ""
        self._running = True
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"clip-{camera}", daemon=True)
        # writer state, touched only by the recorder thread
        self._writer: Optional[cv2.VideoWriter] = None
        self._path: Optional[Path] = None
        self._pre: deque = deque()
        self._started_at = 0.0
        self._post_deadline = 0.0
        self._size: Optional[Tuple[int, int]] = None
        self._thread.start()

    # ------------------------------------------------------------------ API
    def set_config(self, config: ClipConfig) -> None:
        self.config = config

    def set_label(self, label: str) -> None:
        self.label = label or self.label

    def submit(self, image: np.ndarray, timestamp: float = 0.0) -> None:
        """Hand a frame over. Never blocks; drops the oldest when the writer falls behind."""
        if not self.config.enabled or image is None or not self._running:
            return
        payload = (self._prepare(image), timestamp or time.monotonic())
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            try:
                self._queue.get_nowait()          # make room, keep the newest
                self._queue.put_nowait(payload)
            except queue.Empty:
                pass

    def set_occupied(self, occupied: bool, zone: str = "") -> None:
        with self._lock:
            self._occupied = bool(occupied)
            if occupied and zone:
                self._zone = zone

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------ internals
    def _prepare(self, image: np.ndarray) -> np.ndarray:
        scale = float(self.config.scale or 1.0)
        if 0.1 <= scale < 0.99:
            h, w = image.shape[:2]
            return cv2.resize(image, (max(2, int(w * scale)), max(2, int(h * scale))),
                              interpolation=cv2.INTER_AREA)
        return image.copy()

    def _run(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                self._tick(None, time.monotonic())
                continue
            if item is None:
                break
            image, stamp = item
            self._tick(image, stamp)
        self._close("shutdown")

    def _tick(self, image: Optional[np.ndarray], stamp: float) -> None:
        with self._lock:
            occupied, zone = self._occupied, self._zone

        if image is not None:
            if self._writer is None:
                self._remember(image, stamp)
            else:
                self._write(image)

        if occupied and self._writer is None:
            self._open(zone, stamp)
        elif occupied and self._writer is not None:
            self._post_deadline = 0.0
            if (stamp - self._started_at) > max(5.0, float(self.config.max_duration_s)):
                self._close("length limit")
                self._open(zone, stamp)          # keep going in a new part
        elif not occupied and self._writer is not None:
            if self._post_deadline == 0.0:
                self._post_deadline = stamp + max(0.0, float(self.config.post_roll_s))
            elif stamp >= self._post_deadline:
                self._close("zone clear")

    def _remember(self, image: np.ndarray, stamp: float) -> None:
        """Ring buffer of the seconds before anything happened."""
        window = max(0.0, float(self.config.pre_roll_s))
        self._pre.append((image, stamp))
        while self._pre and (stamp - self._pre[0][1]) > window:
            self._pre.popleft()

    def _estimated_fps(self) -> float:
        if self.config.fps and self.config.fps > 0:
            return float(self.config.fps)
        stamps = [s for _, s in self._pre]
        gaps = [b - a for a, b in zip(stamps, stamps[1:]) if 0.001 < (b - a) < 1.0]
        if not gaps:
            return 25.0
        gaps.sort()
        return max(1.0, min(60.0, 1.0 / gaps[len(gaps) // 2]))

    def _open(self, zone: str, stamp: float) -> None:
        sample = self._pre[0][0] if self._pre else None
        if sample is None:
            return                                  # nothing to size the file from yet
        when = datetime.now()
        folder = Path(self.config.directory) / when.strftime("%Y-%m-%d") / self._slug(self.label)
        tag = self._slug(zone) or "PERSON"
        path = folder / f"{when.strftime('%H-%M-%S')}_{tag}.mp4"
        height, width = sample.shape[:2]
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
        self._started_at, self._post_deadline = stamp, 0.0
        for image, _ in self._pre:                  # the clip starts before the trigger
            self._write(image)
        self._pre.clear()
        log.info("%s: recording %s (%.0f fps, %dx%d, %.1fs pre-roll)",
                 self.label, path.name, fps, width, height, float(self.config.pre_roll_s))

    def _write(self, image: np.ndarray) -> None:
        if self._writer is None or self._size is None:
            return
        if (image.shape[1], image.shape[0]) != self._size:
            image = cv2.resize(image, self._size, interpolation=cv2.INTER_AREA)
        try:
            self._writer.write(image)
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


def purge_old_clips(directory: str | Path, retention_days: int) -> int:
    """Delete clips older than the retention window. Returns how many went."""
    if not retention_days or retention_days <= 0:
        return 0
    root = Path(directory)
    if not root.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=int(retention_days))
    removed = 0
    for clip in root.rglob("*.mp4"):
        try:
            if datetime.fromtimestamp(clip.stat().st_mtime) < cutoff:
                clip.unlink()
                removed += 1
        except OSError as exc:
            log.warning("Cannot delete %s: %s", clip, exc)
    if removed:
        log.info("Removed %d clip(s) older than %d day(s)", removed, retention_days)
    return removed
