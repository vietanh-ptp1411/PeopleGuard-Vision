"""USB / UVC webcam adapter using OpenCV VideoCapture."""
from __future__ import annotations

import logging
import platform
import time
from typing import List, Optional

import cv2

from ..config.schemas import UsbCameraConfig
from .base_camera import BaseCamera, CameraInfo, DeviceDescriptor, Frame

log = logging.getLogger("CAMERA")

_BACKENDS = {
    "auto": cv2.CAP_ANY,
    "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
    "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
}


def _resolve_backend(name: str) -> int:
    name = (name or "auto").lower()
    if name == "auto" and platform.system() == "Windows":
        # DirectShow opens fast and honours resolution/FPS requests on most UVC cams.
        return _BACKENDS["dshow"]
    return _BACKENDS.get(name, cv2.CAP_ANY)


class UsbCamera(BaseCamera):
    camera_type = "usb"

    def __init__(self, config: UsbCameraConfig) -> None:
        super().__init__()
        self.config = config
        self._cap: Optional[cv2.VideoCapture] = None
        self._info = CameraInfo(camera_type=self.camera_type, name=f"USB #{config.device_index}")

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        with self._lock:
            self.disconnect()
            idx = int(self.config.device_index)
            backend = _resolve_backend(self.config.backend)
            try:
                cap = cv2.VideoCapture(idx, backend)
                if not cap.isOpened() and backend != cv2.CAP_ANY:
                    log.warning("USB camera %d: backend %s failed, trying default backend", idx, self.config.backend)
                    cap.release()
                    cap = cv2.VideoCapture(idx, cv2.CAP_ANY)
                if not cap.isOpened():
                    self.last_error = f"Cannot open USB camera index {idx}"
                    cap.release()
                    return False
                if self.config.width > 0 and self.config.height > 0:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
                if self.config.fps > 0:
                    cap.set(cv2.CAP_PROP_FPS, self.config.fps)
                # Warm-up read: some drivers return black/invalid first frames.
                ok = False
                for _ in range(5):
                    ok, _img = cap.read()
                    if ok:
                        break
                    time.sleep(0.05)
                if not ok:
                    self.last_error = f"USB camera index {idx} opened but returns no frames"
                    cap.release()
                    return False
                self._cap = cap
                self._info = CameraInfo(
                    camera_type=self.camera_type,
                    name=f"USB #{idx}",
                    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    fps=float(cap.get(cv2.CAP_PROP_FPS) or 0.0),
                    extra={"backend": cap.getBackendName() if hasattr(cap, "getBackendName") else str(backend)},
                )
                self._connected = True
                self.last_error = ""
                log.info("Connected %s", self._info.summary())
                return True
            except cv2.error as exc:
                self.last_error = f"OpenCV error: {exc}"
                return False

    def disconnect(self) -> None:
        with self._lock:
            self._streaming = False
            self._connected = False
            if self._cap is not None:
                try:
                    self._cap.release()
                except cv2.error:
                    pass
                self._cap = None

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        cap = self._cap
        if cap is None or not self._streaming:
            return None
        try:
            ok, img = cap.read()
        except cv2.error as exc:
            self.last_error = f"OpenCV read error: {exc}"
            return None
        if not ok or img is None:
            return None
        return self._make_frame(img)

    def get_device_info(self) -> CameraInfo:
        return self._info

    # ------------------------------------------------------------------ scan
    @classmethod
    def scan_devices(cls, max_index: int = 6) -> List[DeviceDescriptor]:
        """Probe indices 0..max_index-1. Slow-ish (opens each device) but user-triggered."""
        found: List[DeviceDescriptor] = []
        backend = _resolve_backend("auto")
        for idx in range(max_index):
            cap = cv2.VideoCapture(idx, backend)
            try:
                if cap.isOpened():
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    found.append(DeviceDescriptor(str(idx), f"Camera {idx} ({w}x{h})", {"width": w, "height": h}))
            finally:
                cap.release()
        return found
