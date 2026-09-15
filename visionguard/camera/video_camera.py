"""Video file adapter (.mp4/.avi/.mov ...) with play/pause/loop - the demo workhorse."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import cv2

from ..config.schemas import VideoCameraConfig
from .base_camera import BaseCamera, CameraInfo, Frame

log = logging.getLogger("CAMERA")

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v", ".mpg", ".mpeg")


class VideoCamera(BaseCamera):
    camera_type = "video"

    def __init__(self, config: VideoCameraConfig) -> None:
        super().__init__()
        self.config = config
        self._cap: Optional[cv2.VideoCapture] = None
        self._info = CameraInfo(camera_type=self.camera_type, name=Path(config.path).name)
        self._paused = bool(config.start_paused)
        self._finished = False
        self._loop = bool(config.loop)
        self._fps = 25.0
        self._frame_count = 0
        self._next_deadline = 0.0
        self._pause_lock = threading.Lock()
        self._seek_request: Optional[int] = None

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        with self._lock:
            self.disconnect()
            path = Path(self.config.path)
            if not path.is_file():
                self.last_error = f"Video file not found: {path}"
                return False
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                self.last_error = f"Cannot open video file: {path.name}"
                cap.release()
                return False
            self._cap = cap
            self._fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if self._fps <= 1.0 or self._fps > 240:
                self._fps = 25.0
            self._frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self._info = CameraInfo(
                camera_type=self.camera_type,
                name=path.name,
                width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=self._fps,
                extra={"frames": self._frame_count, "path": str(path)},
            )
            self._finished = False
            self._paused = bool(self.config.start_paused)
            self._connected = True
            self.last_error = ""
            log.info("Opened video %s", self._info.summary())
            return True

    def disconnect(self) -> None:
        with self._lock:
            self._streaming = False
            self._connected = False
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    def start(self) -> bool:
        ok = super().start()
        if ok:
            self._next_deadline = time.perf_counter()
        return ok

    # ------------------------------------------------------------------ frames
    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        cap = self._cap
        if cap is None or not self._streaming:
            return None
        if self._paused:
            time.sleep(min(timeout, 0.05))
            return None
        if self._finished:
            time.sleep(min(timeout, 0.1))
            return None

        with self._lock:
            if self._seek_request is not None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, self._seek_request)
                self._seek_request = None
            ok, img = cap.read()
            if not ok or img is None:
                if self._loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, img = cap.read()
                if not ok or img is None:
                    self._finished = True
                    log.info("Video finished: %s", self._info.name)
                    return None

        if self.config.realtime:
            self._pace()
        return self._make_frame(img)

    def _pace(self) -> None:
        period = 1.0 / self._fps
        now = time.perf_counter()
        if self._next_deadline <= 0 or now - self._next_deadline > 1.0:
            self._next_deadline = now  # re-sync after pause/seek/lag
        self._next_deadline += period
        delay = self._next_deadline - now
        if delay > 0:
            time.sleep(delay)

    # ------------------------------------------------------------------ playback control
    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._next_deadline = 0.0

    def toggle_pause(self) -> bool:
        if self._paused:
            self.resume()
        else:
            self.pause()
        return self._paused

    def restart(self) -> None:
        self._seek_request = 0
        self._finished = False
        self._next_deadline = 0.0

    def seek_fraction(self, fraction: float) -> None:
        if self._frame_count > 0:
            self._seek_request = int(max(0.0, min(1.0, fraction)) * (self._frame_count - 1))
            self._finished = False

    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)
        self.config.loop = self._loop
        if loop and self._finished:
            self.restart()

    def position(self) -> tuple[int, int]:
        cap = self._cap
        if cap is None:
            return 0, 0
        return int(cap.get(cv2.CAP_PROP_POS_FRAMES)), self._frame_count

    def is_paused(self) -> bool:
        return self._paused

    def is_finished(self) -> bool:
        return self._finished

    def get_device_info(self) -> CameraInfo:
        return self._info
