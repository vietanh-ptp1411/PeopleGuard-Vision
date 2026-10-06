"""Save a JPEG when an area becomes OCCUPIED: events/YYYY-MM-DD/HH-MM-SS_ROI01_PERSON.jpg (async)."""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..config.schemas import SnapshotConfig

log = logging.getLogger("STORAGE")


class SnapshotSaver:
    def __init__(self, config: SnapshotConfig) -> None:
        self.config = config
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot")
        self._lock = threading.Lock()
        self._pending_bytes = 0
        self._pending_jobs = 0
        self._futures = set()
        self._closed = False
        self.dropped = 0
        self.on_failure = None

    def set_config(self, config: SnapshotConfig) -> None:
        self.config = config

    def save(self, image: np.ndarray, roi_id: str, tag: str = "PERSON", when: Optional[datetime] = None) -> Optional[Path]:
        """Queue the write and return the target path immediately (None if disabled)."""
        if not self.config.enabled or image is None:
            return None
        when = when or datetime.now()
        folder = Path(self.config.directory) / when.strftime("%Y-%m-%d")
        safe_roi = "".join(c for c in roi_id if c.isalnum() or c in "-_") or "ROI"
        safe_tag = "".join(c for c in tag if c.isalnum() or c in "-_") or "PERSON"
        path = folder / f"{when.strftime('%H-%M-%S-%f')}_{safe_roi}_{safe_tag}_{uuid.uuid4().hex[:8]}.jpg"
        size = int(image.nbytes)
        with self._lock:
            if (self._closed or self._pending_jobs >= max(1, self.config.max_pending_jobs)
                    or self._pending_bytes + size > max(1, self.config.max_pending_mb) * 1024 ** 2):
                self.dropped += 1
                if self.dropped == 1 or self.dropped % 100 == 0:
                    log.warning("Snapshot buffer full/closed: %d image(s) skipped; event is still saved", self.dropped)
                return None
            self._pending_bytes += size
            self._pending_jobs += 1
        quality = int(max(30, min(100, self.config.jpeg_quality)))
        try:
            future = self._pool.submit(self._write, path, image.copy(), quality)
        except Exception:
            with self._lock:
                self._pending_bytes -= size
                self._pending_jobs -= 1
            log.exception("Cannot queue snapshot")
            return None
        with self._lock:
            self._futures.add(future)

        def completed(job):
            with self._lock:
                self._pending_bytes -= size
                self._pending_jobs -= 1
                self._futures.discard(job)
            ok = not job.cancelled() and job.exception() is None and job.result() is True
            if not ok and self.on_failure is not None:
                self.on_failure(str(path))

        future.add_done_callback(completed)
        return path

    @staticmethod
    def _write(path: Path, image: np.ndarray, quality: int) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                log.error("JPEG encode failed for %s", path)
                return False
            path.write_bytes(buf.tobytes())   # works with unicode paths on Windows
            log.info("Snapshot saved %s", path)
            return True
        except Exception as exc:
            log.error("Snapshot save failed %s: %s", path, exc)
            return False

    def shutdown(self, timeout: float = 3.0) -> bool:
        with self._lock:
            self._closed = True
            jobs = list(self._futures)
        _, pending = wait(jobs, timeout=timeout)
        self._pool.shutdown(wait=False, cancel_futures=True)
        return not pending
