"""Save a JPEG when an area becomes OCCUPIED: events/YYYY-MM-DD/HH-MM-SS_ROI01_PERSON.jpg (async)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
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

    def set_config(self, config: SnapshotConfig) -> None:
        self.config = config

    def save(self, image: np.ndarray, roi_id: str, tag: str = "PERSON", when: Optional[datetime] = None) -> Optional[Path]:
        """Queue the write and return the target path immediately (None if disabled)."""
        if not self.config.enabled or image is None:
            return None
        when = when or datetime.now()
        folder = Path(self.config.directory) / when.strftime("%Y-%m-%d")
        safe_roi = "".join(c for c in roi_id if c.isalnum() or c in "-_") or "ROI"
        path = folder / f"{when.strftime('%H-%M-%S')}_{safe_roi}_{tag}.jpg"
        img_copy = image.copy()
        quality = int(max(30, min(100, self.config.jpeg_quality)))
        self._pool.submit(self._write, path, img_copy, quality)
        return path

    @staticmethod
    def _write(path: Path, image: np.ndarray, quality: int) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                log.error("JPEG encode failed for %s", path)
                return
            path.write_bytes(buf.tobytes())   # works with unicode paths on Windows
            log.info("Snapshot saved %s", path)
        except Exception as exc:
            log.error("Snapshot save failed %s: %s", path, exc)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
