"""Basler camera adapter (pypylon). Falls back gracefully when pylon is not installed."""
from __future__ import annotations

import logging
from typing import List, Optional

from ...config.schemas import IndustrialCameraConfig
from ..base_camera import CameraInfo, DeviceDescriptor, Frame
from .industrial_base import IndustrialCameraBase

log = logging.getLogger("CAMERA")

try:  # SDK is optional
    from pypylon import pylon  # type: ignore

    _PYLON_OK = True
    _PYLON_STATUS = "OK"
except Exception as _exc:  # ImportError or missing runtime DLLs
    pylon = None  # type: ignore
    _PYLON_OK = False
    _PYLON_STATUS = f"SDK not installed (pypylon: {type(_exc).__name__})"


class BaslerCamera(IndustrialCameraBase):
    camera_type = "basler"
    vendor = "Basler"

    def __init__(self, config: IndustrialCameraConfig) -> None:
        super().__init__(config)
        self._cam = None
        self._converter = None

    # ------------------------------------------------------------------ sdk
    @classmethod
    def sdk_available(cls) -> bool:
        return _PYLON_OK

    @classmethod
    def sdk_status(cls) -> str:
        return _PYLON_STATUS

    @classmethod
    def scan_devices(cls) -> List[DeviceDescriptor]:
        if not _PYLON_OK:
            return []
        out: List[DeviceDescriptor] = []
        try:
            for di in pylon.TlFactory.GetInstance().EnumerateDevices():
                serial = di.GetSerialNumber() if di.IsSerialNumberAvailable() else ""
                model = di.GetModelName() if di.IsModelNameAvailable() else ""
                name = di.GetFriendlyName() if di.IsFriendlyNameAvailable() else model
                out.append(DeviceDescriptor(serial, f"{name} [{serial}]", {"model": model, "class": di.GetDeviceClass()}))
        except Exception as exc:
            log.error("Basler scan failed: %s", exc)
        return out

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        if self._sdk_missing():
            return False
        with self._lock:
            self.disconnect()
            try:
                tl = pylon.TlFactory.GetInstance()
                devices = tl.EnumerateDevices()
                if not devices:
                    self.last_error = "No Basler camera found"
                    return False
                target = None
                wanted = (self.config.serial_number or "").strip()
                for di in devices:
                    if not wanted or (di.IsSerialNumberAvailable() and di.GetSerialNumber() == wanted):
                        target = di
                        break
                if target is None:
                    self.last_error = f"Basler camera with serial '{wanted}' not found"
                    return False
                cam = pylon.InstantCamera(tl.CreateDevice(target))
                cam.Open()
                self._cam = cam
                self.apply_config()
                conv = pylon.ImageFormatConverter()
                conv.OutputPixelFormat = pylon.PixelType_BGR8packed
                conv.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
                self._converter = conv
                self._info = CameraInfo(
                    camera_type=self.camera_type,
                    name="Basler",
                    model=target.GetModelName() if target.IsModelNameAvailable() else "",
                    serial=target.GetSerialNumber() if target.IsSerialNumberAvailable() else "",
                    width=int(self._node_value("Width", 0)),
                    height=int(self._node_value("Height", 0)),
                    fps=float(self._node_value("ResultingFrameRate", 0.0) or self._node_value("ResultingFrameRateAbs", 0.0)),
                )
                self._connected = True
                self.last_error = ""
                log.info("Connected %s", self._info.summary())
                return True
            except Exception as exc:
                self.last_error = f"Basler connect failed: {exc}"
                log.error(self.last_error)
                self._cam = None
                return False

    def disconnect(self) -> None:
        with self._lock:
            self._streaming = False
            self._connected = False
            cam = self._cam
            self._cam = None
            if cam is not None:
                try:
                    if cam.IsGrabbing():
                        cam.StopGrabbing()
                    cam.Close()
                except Exception as exc:
                    log.warning("Basler close: %s", exc)

    def start(self) -> bool:
        with self._lock:
            if self._cam is None:
                self.last_error = "Camera not connected"
                return False
            try:
                if not self._cam.IsGrabbing():
                    self._cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
                self._streaming = True
                return True
            except Exception as exc:
                self.last_error = f"Basler StartGrabbing failed: {exc}"
                return False

    def stop(self) -> None:
        with self._lock:
            self._streaming = False
            if self._cam is not None:
                try:
                    if self._cam.IsGrabbing():
                        self._cam.StopGrabbing()
                except Exception as exc:
                    log.warning("Basler StopGrabbing: %s", exc)

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        cam = self._cam
        if cam is None or not self._streaming:
            return None
        try:
            result = cam.RetrieveResult(self._grab_timeout_ms(), pylon.TimeoutHandling_Return)
        except Exception as exc:
            self.last_error = f"Basler grab error: {exc}"
            self._connected = False
            return None
        try:
            if not result.GrabSucceeded():
                return None
            image = self._converter.Convert(result).GetArray()
            return self._make_frame(image)
        finally:
            result.Release()

    # ------------------------------------------------------------------ features
    def _node_value(self, name: str, default):
        try:
            node = getattr(self._cam, name)
            return node.GetValue()
        except Exception:
            return default

    def _set_first(self, names: List[str], value) -> bool:
        for name in names:
            try:
                node = getattr(self._cam, name)
                node.SetValue(value)
                return True
            except Exception as exc:  # node missing / out of range
                self.last_error = f"{name}: {exc}"
        return False

    def set_exposure(self, exposure_us: float) -> bool:
        try:
            self._set_first(["ExposureAuto"], "Off")
        except Exception:
            pass
        return self._set_first(["ExposureTime", "ExposureTimeAbs"], float(exposure_us)) or self._set_first(
            ["ExposureTimeRaw"], int(exposure_us)
        )

    def set_gain(self, gain: float) -> bool:
        try:
            self._set_first(["GainAuto"], "Off")
        except Exception:
            pass
        return self._set_first(["Gain"], float(gain)) or self._set_first(["GainRaw"], int(gain))

    def set_frame_rate(self, fps: float) -> bool:
        if fps <= 0:
            return self._set_first(["AcquisitionFrameRateEnable"], False)
        self._set_first(["AcquisitionFrameRateEnable"], True)
        return self._set_first(["AcquisitionFrameRate", "AcquisitionFrameRateAbs"], float(fps))

    def set_trigger_mode(self, enabled: bool) -> bool:
        self._set_first(["TriggerSelector"], "FrameStart")
        return self._set_first(["TriggerMode"], "On" if enabled else "Off")
