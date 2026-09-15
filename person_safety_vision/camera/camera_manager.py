"""CameraManager: factory + device scan + SDK availability for every camera type."""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple, Type

from ..config.schemas import CameraConfig, CameraType
from .base_camera import BaseCamera, DeviceDescriptor
from .industrial.basler_camera import BaslerCamera
from .industrial.genicam_camera import GenICamCamera
from .industrial.hikrobot_camera import HikrobotCamera
from .rtsp_camera import RtspCamera
from .usb_camera import UsbCamera
from .video_camera import VideoCamera

log = logging.getLogger("CAMERA")


class CameraManager:
    """Creates camera adapters from configuration. Stateless apart from the registry.

    The CameraWorker owns the camera *instance* (connect/start/stop/disconnect happen
    in the worker thread); the manager only knows how to build one.
    """

    REGISTRY: Dict[CameraType, Type[BaseCamera]] = {
        CameraType.USB: UsbCamera,
        CameraType.VIDEO: VideoCamera,
        CameraType.RTSP: RtspCamera,
        CameraType.BASLER: BaslerCamera,
        CameraType.HIKROBOT: HikrobotCamera,
        CameraType.GENICAM: GenICamCamera,
    }

    def create_camera(self, config: CameraConfig) -> BaseCamera:
        ctype = config.type_enum
        cls = self.REGISTRY[ctype]
        if ctype == CameraType.USB:
            return UsbCamera(config.usb)
        if ctype == CameraType.VIDEO:
            return VideoCamera(config.video)
        if ctype == CameraType.RTSP:
            return RtspCamera(config.rtsp)
        # industrial adapters share IndustrialCameraConfig
        return cls(config.industrial)  # type: ignore[call-arg]

    def scan_devices(self, camera_type: CameraType, cti_path: str = "") -> List[DeviceDescriptor]:
        cls = self.REGISTRY[camera_type]
        try:
            if camera_type == CameraType.GENICAM:
                return GenICamCamera.scan_devices(cti_path)
            return cls.scan_devices()
        except Exception as exc:  # scanning must never take the app down
            log.error("Scan %s failed: %s", camera_type.value, exc)
            return []

    def availability(self) -> Dict[CameraType, Tuple[bool, str]]:
        """camera type -> (available, status text). Used to grey-out UI choices."""
        return {ct: (cls.sdk_available(), cls.sdk_status()) for ct, cls in self.REGISTRY.items()}

    @staticmethod
    def type_label(camera_type: CameraType) -> str:
        return camera_type.label
