"""Generic GenICam camera via Harvesters + a vendor GenTL producer (.cti file).

Works with any GenICam-compliant camera (Basler, Daheng, FLIR, Allied Vision, IDS,
Baumer, Lucid, JAI, ...) as long as the vendor's GenTL producer is installed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

from ...config.schemas import IndustrialCameraConfig
from ..base_camera import CameraInfo, DeviceDescriptor, Frame
from .industrial_base import IndustrialCameraBase, to_bgr

log = logging.getLogger("CAMERA")

try:  # optional SDK
    from harvesters.core import Harvester  # type: ignore

    _HARVESTER_OK = True
    _HARVESTER_STATUS = "OK"
except Exception as _exc:
    Harvester = None  # type: ignore
    _HARVESTER_OK = False
    _HARVESTER_STATUS = f"SDK not installed (pip install harvesters; {type(_exc).__name__})"


class GenICamCamera(IndustrialCameraBase):
    camera_type = "genicam"
    vendor = "GenICam"

    def __init__(self, config: IndustrialCameraConfig) -> None:
        super().__init__(config)
        self._harvester = None
        self._ia = None  # image acquirer

    @classmethod
    def sdk_available(cls) -> bool:
        return _HARVESTER_OK

    @classmethod
    def sdk_status(cls) -> str:
        return _HARVESTER_STATUS

    # ------------------------------------------------------------------ scan
    @classmethod
    def scan_devices(cls, cti_path: str = "") -> List[DeviceDescriptor]:
        if not _HARVESTER_OK or not cti_path or not Path(cti_path).is_file():
            return []
        h = Harvester()
        try:
            h.add_file(cti_path)
            h.update()
            out = []
            for info in h.device_info_list:
                serial = getattr(info, "serial_number", "") or ""
                model = getattr(info, "model", "") or ""
                vendor = getattr(info, "vendor", "") or ""
                out.append(DeviceDescriptor(serial, f"{vendor} {model} [{serial}]".strip(), {"model": model, "vendor": vendor}))
            return out
        except Exception as exc:
            log.error("GenICam scan failed: %s", exc)
            return []
        finally:
            try:
                h.reset()
            except Exception:
                pass

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        if self._sdk_missing():
            return False
        cti = (self.config.cti_path or "").strip()
        if not cti or not Path(cti).is_file():
            self.last_error = f"GenTL producer (.cti) not found: '{cti}'"
            log.error(self.last_error)
            return False
        with self._lock:
            self.disconnect()
            try:
                h = Harvester()
                h.add_file(cti)
                h.update()
                if not h.device_info_list:
                    self.last_error = "No GenICam device found through this .cti"
                    h.reset()
                    return False
                wanted = (self.config.serial_number or "").strip()
                create = getattr(h, "create", None) or getattr(h, "create_image_acquirer")
                if wanted:
                    ia = create({"serial_number": wanted})
                else:
                    ia = create(0)
                self._harvester, self._ia = h, ia
                self.apply_config()
                nm = ia.remote_device.node_map
                info = h.device_info_list[0]
                self._info = CameraInfo(
                    camera_type=self.camera_type,
                    name="GenICam",
                    model=getattr(info, "model", "") or "",
                    serial=wanted or (getattr(info, "serial_number", "") or ""),
                    width=int(self._node(nm, "Width", 0)),
                    height=int(self._node(nm, "Height", 0)),
                    fps=float(self._node(nm, "AcquisitionFrameRate", 0.0)),
                    extra={"cti": cti},
                )
                self._connected = True
                self.last_error = ""
                log.info("Connected %s", self._info.summary())
                return True
            except Exception as exc:
                self.last_error = f"GenICam connect failed: {exc}"
                log.error(self.last_error)
                self._cleanup()
                return False

    def _cleanup(self) -> None:
        ia, h = self._ia, self._harvester
        self._ia, self._harvester = None, None
        if ia is not None:
            for fn in ("stop", "stop_acquisition", "destroy"):
                try:
                    getattr(ia, fn)()
                except Exception:
                    pass
        if h is not None:
            try:
                h.reset()
            except Exception:
                pass

    def disconnect(self) -> None:
        with self._lock:
            self._streaming = False
            self._connected = False
            self._cleanup()

    def start(self) -> bool:
        with self._lock:
            if self._ia is None:
                self.last_error = "Camera not connected"
                return False
            try:
                start = getattr(self._ia, "start", None) or getattr(self._ia, "start_acquisition")
                start()
                self._streaming = True
                return True
            except Exception as exc:
                self.last_error = f"GenICam start failed: {exc}"
                return False

    def stop(self) -> None:
        with self._lock:
            self._streaming = False
            if self._ia is not None:
                try:
                    stop = getattr(self._ia, "stop", None) or getattr(self._ia, "stop_acquisition")
                    stop()
                except Exception:
                    pass

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        ia = self._ia
        if ia is None or not self._streaming:
            return None
        fetch = getattr(ia, "fetch", None) or getattr(ia, "fetch_buffer")
        try:
            buffer = fetch(timeout=self._grab_timeout_ms() / 1000.0)
        except Exception as exc:  # TimeoutException etc.
            if "timeout" not in str(exc).lower():
                self.last_error = f"GenICam fetch: {exc}"
            return None
        try:
            comp = buffer.payload.components[0]
            w, h = int(comp.width), int(comp.height)
            fmt = str(getattr(comp, "data_format", ""))
            data = np.asarray(comp.data)
            channels = data.size // (w * h) if w and h else 1
            image = data.reshape(h, w, channels) if channels > 1 else data.reshape(h, w)
            return self._make_frame(to_bgr(image.copy(), fmt))
        except Exception as exc:
            self.last_error = f"GenICam frame decode: {exc}"
            return None
        finally:
            try:
                buffer.queue()
            except Exception:
                pass

    # ------------------------------------------------------------------ features
    @staticmethod
    def _node(nm, name: str, default):
        try:
            return getattr(nm, name).value
        except Exception:
            return default

    def _set_node(self, name: str, value) -> bool:
        try:
            setattr(getattr(self._ia.remote_device.node_map, name), "value", value)
            return True
        except Exception as exc:
            self.last_error = f"{name}: {exc}"
            return False

    def set_exposure(self, exposure_us: float) -> bool:
        self._set_node("ExposureAuto", "Off")
        return self._set_node("ExposureTime", float(exposure_us)) or self._set_node("ExposureTimeAbs", float(exposure_us))

    def set_gain(self, gain: float) -> bool:
        self._set_node("GainAuto", "Off")
        return self._set_node("Gain", float(gain)) or self._set_node("GainRaw", int(gain))

    def set_frame_rate(self, fps: float) -> bool:
        if fps <= 0:
            return self._set_node("AcquisitionFrameRateEnable", False)
        self._set_node("AcquisitionFrameRateEnable", True)
        return self._set_node("AcquisitionFrameRate", float(fps)) or self._set_node("AcquisitionFrameRateAbs", float(fps))

    def set_trigger_mode(self, enabled: bool) -> bool:
        self._set_node("TriggerSelector", "FrameStart")
        return self._set_node("TriggerMode", "On" if enabled else "Off")
