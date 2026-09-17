"""Camera abstraction. Every camera returns frames as numpy BGR uint8 arrays."""
from __future__ import annotations

import abc
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class Frame:
    image: np.ndarray            # HxWx3 BGR uint8
    frame_id: int
    timestamp: float             # time.time() at capture
    source: str = ""
    camera: int = 0              # which camera in the group produced it

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass
class CameraInfo:
    camera_type: str
    name: str = ""
    model: str = ""
    serial: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [self.name or self.camera_type]
        if self.model:
            parts.append(self.model)
        if self.serial:
            parts.append(f"SN {self.serial}")
        if self.width and self.height:
            parts.append(f"{self.width}x{self.height}")
        if self.fps:
            parts.append(f"{self.fps:.0f}fps")
        return " | ".join(parts)


@dataclass
class DeviceDescriptor:
    """Result of a device scan (USB index, Basler serial, ...)."""
    identifier: str
    display_name: str
    extra: Dict[str, Any] = field(default_factory=dict)


class CameraError(Exception):
    """Raised by cameras for connection/grab failures (never propagates to UI thread)."""


class BaseCamera(abc.ABC):
    """Interface implemented by all camera adapters.

    Lifecycle:  connect() -> start() -> get_frame()* -> stop() -> disconnect()
    All methods must be non-throwing at the boundary: return False / None and set
    `last_error` instead of raising, so that a missing SDK or a wrong URL can never
    crash the application.
    """

    camera_type: str = "base"

    def __init__(self) -> None:
        self._connected = False
        self._streaming = False
        self._frame_counter = 0
        self.last_error: str = ""
        self._lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------------
    @abc.abstractmethod
    def connect(self) -> bool: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    def start(self) -> bool:
        with self._lock:
            if not self._connected:
                self.last_error = "Camera not connected"
                return False
            self._streaming = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._streaming = False

    @abc.abstractmethod
    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        """Return the next frame or None if no frame is available within `timeout`."""

    # -- state ----------------------------------------------------------------
    def is_connected(self) -> bool:
        return self._connected

    def is_streaming(self) -> bool:
        return self._streaming

    def is_paused(self) -> bool:
        """Only playback sources (video files) can be paused."""
        return False

    def is_finished(self) -> bool:
        """True when a finite source (video file without loop) reached its end."""
        return False

    @abc.abstractmethod
    def get_device_info(self) -> CameraInfo: ...

    # -- helpers --------------------------------------------------------------
    def _make_frame(self, image: np.ndarray) -> Frame:
        self._frame_counter += 1
        return Frame(image=image, frame_id=self._frame_counter, timestamp=time.time(), source=self.camera_type)

    @classmethod
    def scan_devices(cls) -> List[DeviceDescriptor]:
        return []

    @classmethod
    def sdk_available(cls) -> bool:
        return True

    @classmethod
    def sdk_status(cls) -> str:
        return "OK"
