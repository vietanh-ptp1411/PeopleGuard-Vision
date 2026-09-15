"""Base class for machine-vision cameras (GigE Vision / USB3 Vision / GenICam).

Concrete adapters (Basler, Hikrobot, GenICam) live in this package and must:
  * import their SDK lazily and expose sdk_available()/sdk_status()
  * never raise out of connect()/get_frame(); set last_error instead
  * return BGR uint8 frames like every other camera
"""
from __future__ import annotations

import abc
import logging
from typing import Optional

import cv2
import numpy as np

from ...config.schemas import IndustrialCameraConfig
from ..base_camera import BaseCamera, CameraInfo

log = logging.getLogger("CAMERA")


def to_bgr(image: np.ndarray, pixel_format: str) -> np.ndarray:
    """Convert common GenICam pixel formats to BGR8."""
    fmt = (pixel_format or "").lower()
    if image.ndim == 2:
        if "bayerrg" in fmt:
            return cv2.cvtColor(image, cv2.COLOR_BayerRG2BGR)
        if "bayergb" in fmt:
            return cv2.cvtColor(image, cv2.COLOR_BayerGB2BGR)
        if "bayergr" in fmt:
            return cv2.cvtColor(image, cv2.COLOR_BayerGR2BGR)
        if "bayerbg" in fmt:
            return cv2.cvtColor(image, cv2.COLOR_BayerBG2BGR)
        if image.dtype != np.uint8:  # Mono10/12/16 -> 8 bit
            shift = 8 if image.dtype == np.uint16 and image.max() > 4095 else (4 if image.dtype == np.uint16 else 0)
            image = (image >> shift).astype(np.uint8) if shift else image.astype(np.uint8)
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 3:
        if "rgb" in fmt and "bgr" not in fmt:
            return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        return image
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


class IndustrialCameraBase(BaseCamera):
    camera_type = "industrial"
    vendor: str = "generic"

    def __init__(self, config: IndustrialCameraConfig) -> None:
        super().__init__()
        self.config = config
        self._info = CameraInfo(camera_type=self.camera_type, name=self.vendor)

    # -- feature access (best effort; adapters override) -----------------------
    @abc.abstractmethod
    def set_exposure(self, exposure_us: float) -> bool: ...

    @abc.abstractmethod
    def set_gain(self, gain: float) -> bool: ...

    @abc.abstractmethod
    def set_frame_rate(self, fps: float) -> bool: ...

    @abc.abstractmethod
    def set_trigger_mode(self, enabled: bool) -> bool: ...

    def apply_config(self) -> None:
        """Push exposure/gain/fps/trigger from config to the device; log failures, never raise."""
        cfg = self.config
        for label, fn, value in (
            ("ExposureTime", self.set_exposure, cfg.exposure_us),
            ("Gain", self.set_gain, cfg.gain),
            ("FrameRate", self.set_frame_rate, cfg.frame_rate),
            ("TriggerMode", self.set_trigger_mode, str(cfg.trigger_mode).lower() in ("on", "true", "1")),
        ):
            try:
                if not fn(value):  # type: ignore[arg-type]
                    log.warning("%s: %s not applied (%s)", self.vendor, label, self.last_error or "unsupported")
            except Exception as exc:  # SDK-specific exceptions
                log.warning("%s: %s failed: %s", self.vendor, label, exc)

    def get_device_info(self) -> CameraInfo:
        return self._info

    @classmethod
    def sdk_available(cls) -> bool:
        return False

    @classmethod
    def sdk_status(cls) -> str:
        return "SDK not installed"

    def _sdk_missing(self) -> bool:
        if not self.sdk_available():
            self.last_error = f"{self.vendor}: {self.sdk_status()}"
            log.error(self.last_error)
            return True
        return False

    def _grab_timeout_ms(self) -> int:
        return int(max(100, self.config.grab_timeout_ms))

    def _noop_frame(self) -> Optional[np.ndarray]:
        return None
