"""RtspVideoReceiver: the realtime video half of AI Camera mode.

In AI Camera mode the PC does not analyse the picture, it only shows it. This receiver
therefore does one thing well: keep the newest frame available and never let a backlog build
up. It is a BaseCamera, so the existing CameraWorker drives it exactly like any other source
(same thread, same auto-reconnect, same single slot frame buffer) - no second video pipeline.

    receiver.connect() -> start() -> get_latest_frame() ... -> stop() -> disconnect()
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from ...config.schemas import RtspCameraConfig
from ..base_camera import BaseCamera, CameraInfo, Frame
from ..rtsp_camera import RtspCamera, build_rtsp_url, mask_url

log = logging.getLogger("CAMERA")


class RtspVideoReceiver(BaseCamera):
    camera_type = "rtsp_video"

    def __init__(self, config: RtspCameraConfig) -> None:
        super().__init__()
        self.config = config
        self._camera = RtspCamera(config)
        self._latest: Optional[Frame] = None
        self._last_frame_time = 0.0
        self._frames = 0

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        ok = self._camera.connect()
        self._connected = ok
        self.last_error = self._camera.last_error
        return ok

    def disconnect(self) -> None:
        self._camera.disconnect()
        self._connected = False
        self._streaming = False
        self._latest = None

    def start(self) -> bool:
        ok = self._camera.start()
        self._streaming = ok
        self._connected = self._camera.is_connected()
        self.last_error = self._camera.last_error
        if ok:
            self._last_frame_time = time.monotonic()
        return ok

    def stop(self) -> None:
        self._camera.stop()
        self._streaming = False

    # ------------------------------------------------------------------ frames
    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        frame = self._camera.get_frame(timeout)
        self._connected = self._camera.is_connected()
        self.last_error = self._camera.last_error
        if frame is not None:
            self._latest = frame
            self._frames += 1
            self._last_frame_time = time.monotonic()
        return frame

    def get_latest_frame(self) -> Optional[Frame]:
        """Newest frame received so far, without waiting. Older frames are simply dropped."""
        return self._latest

    def seconds_since_last_frame(self) -> float:
        if self._last_frame_time <= 0:
            return 0.0
        return time.monotonic() - self._last_frame_time

    @property
    def frame_count(self) -> int:
        return self._frames

    # ------------------------------------------------------------------ info
    def is_connected(self) -> bool:
        return self._camera.is_connected()

    def get_device_info(self) -> CameraInfo:
        info = self._camera.get_device_info()
        info.camera_type = self.camera_type
        return info

    @property
    def url(self) -> str:
        """Stream URL with the password masked, safe for the UI and the log."""
        return mask_url(build_rtsp_url(self.config))

    # ------------------------------------------------------------------ diagnostics
    @staticmethod
    def test_connection(config: RtspCameraConfig) -> Tuple[bool, str]:
        return RtspCamera.test_connection(config)
