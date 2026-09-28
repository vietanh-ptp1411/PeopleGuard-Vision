"""Hikrobot (HIKROBOT MVS SDK) camera adapter.

The MVS Python binding is not on PyPI - it ships inside the MVS installation as loose
.py files. This module finds that folder itself (see _find_mvimport); when MVS is not
installed the adapter reports "SDK not installed" and never crashes.
"""
from __future__ import annotations

import logging
import os
import sys
from ctypes import POINTER, byref, c_ubyte, cast, memmove, memset, sizeof
from pathlib import Path
from typing import List, Optional

import numpy as np

from ...config.schemas import IndustrialCameraConfig
from ...utils.paths import app_dir
from ..base_camera import CameraInfo, DeviceDescriptor, Frame
from .industrial_base import IndustrialCameraBase, to_bgr

log = logging.getLogger("CAMERA")

_MVIMPORT_TAIL = Path("Samples") / "Python" / "MvImport"


def _mvimport_candidates() -> List[Path]:
    """Everywhere the MVS Python binding is plausibly sitting, best guess first.

    MVS sets MVCAM_COMMON_RUNENV to its Development folder when it installs, so that is
    the answer on a machine that has it; the fixed paths are the fallback for an install
    that did not set the variable, or a session that started before it existed.
    """
    out: List[Path] = []
    run_env = os.environ.get("MVCAM_COMMON_RUNENV", "").strip('"')
    if run_env:
        out.append(Path(run_env) / _MVIMPORT_TAIL)
    sdk = os.environ.get("MVCAM_SDK_PATH", "").strip('"')
    if sdk:
        out.append(Path(sdk) / "Development" / _MVIMPORT_TAIL)
    for base in (r"C:\Program Files (x86)\MVS", r"C:\Program Files\MVS"):
        out.append(Path(base) / "Development" / _MVIMPORT_TAIL)
    # A copy dropped beside the application, for a machine where MVS cannot be installed.
    out.append(app_dir() / "MvImport")
    return out


def _find_mvimport() -> Optional[Path]:
    """Put the MVS binding on sys.path, because asking the customer to do it does not work.

    This used to be a line in the docs: "add <MVS>/Development/Samples/Python/MvImport to
    PYTHONPATH". On the machine it was written for that is a five minute job; on a machine
    somebody else set up, the camera page just says SDK not installed and nobody connects
    the dots. The folder is in exactly one of a handful of places - find it.
    """
    for path in _mvimport_candidates():
        try:
            if (path / "MvCameraControl_class.py").is_file():
                if str(path) not in sys.path:
                    sys.path.insert(0, str(path))
                return path
        except OSError:
            continue
    return None


#: Handles from os.add_dll_directory. They must be kept alive: dropping one removes the
#: directory again, and the next DLL the SDK pulls in would not be found.
_DLL_DIRS: List = []


def _mvs_runtime_dirs() -> List[Path]:
    """Folders holding MvCameraControl.dll and the SDK's own dependencies."""
    out: List[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        text = entry.strip().strip('"')
        if text and "MVS" in text.upper() and "RUNTIME" in text.upper():
            out.append(Path(text))
    for fixed in (r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
                  r"C:\Program Files\Common Files\MVS\Runtime\Win64_x64"):
        out.append(Path(fixed))
    return out


def _preload_mvs_dll() -> Optional[str]:
    """Load MvCameraControl.dll by absolute path before the SDK asks for it by name.

    MVS puts its runtime on PATH and its Python binding loads the DLL by bare name, which
    is fine in a normal interpreter. It is not fine in a frozen build: the PyInstaller
    bootloader calls SetDefaultDllDirectories, and that takes PATH out of the DLL search
    order altogether - so the same install that works from source fails packaged, with
    "Failed to load dynlib/dll 'MvCameraControl.dll'" and no hint that PATH is the reason.

    Loading it here by full path puts the module in the process; the binding's own bare-name
    load then finds it already loaded. add_dll_directory is for what the SDK loads next.
    """
    import ctypes

    for folder in _mvs_runtime_dirs():
        dll = folder / "MvCameraControl.dll"
        try:
            if not dll.is_file():
                continue
            if hasattr(os, "add_dll_directory"):
                _DLL_DIRS.append(os.add_dll_directory(str(folder)))
            ctypes.WinDLL(str(dll), winmode=0)
            return str(dll)
        except OSError as exc:
            log.debug("MVS runtime at %s did not load: %s", folder, exc)
            continue
    return None


_MVIMPORT = _find_mvimport()
_MVS_DLL = _preload_mvs_dll() if _MVIMPORT else None

try:  # optional SDK
    import MvCameraControl_class as mv  # type: ignore

    _MVS_OK = True
    # Short on purpose: the camera-type dropdown puts this next to the vendor name, and a
    # 60-character install path there would widen the side panel on its own. The path is
    # logged instead - that is where you look when the wrong MVS turns out to be in use.
    _MVS_STATUS = "OK"
    log.info("Hikrobot MVS binding: %s (dll: %s)", _MVIMPORT, _MVS_DLL or "tìm theo PATH")
except Exception as _exc:
    mv = None  # type: ignore
    _MVS_OK = False
    if _MVIMPORT is None:
        _MVS_STATUS = ("SDK not installed (MVS not found - install Hikrobot MVS, "
                       "then restart the app)")
    else:
        # The files are there but will not load: almost always the SDK's own DLLs, which
        # means a broken or half-removed MVS rather than a missing one. Worth separating,
        # because the fix is reinstall rather than install.
        _MVS_STATUS = (f"SDK broken ({_MVIMPORT} found, dll={_MVS_DLL}, "
                       f"{type(_exc).__name__}: {_exc})")

_SDK_INITIALIZED = False


def _init_sdk() -> None:
    """Newer MVS versions require MV_CC_Initialize() once per process."""
    global _SDK_INITIALIZED
    if _MVS_OK and not _SDK_INITIALIZED and hasattr(mv.MvCamera, "MV_CC_Initialize"):
        try:
            mv.MvCamera.MV_CC_Initialize()
        except Exception as exc:
            log.warning("MV_CC_Initialize failed: %s", exc)
    _SDK_INITIALIZED = True


def _cstr(arr) -> str:
    try:
        return bytes(arr).split(b"\x00", 1)[0].decode(errors="ignore")
    except Exception:
        return ""


class HikrobotCamera(IndustrialCameraBase):
    camera_type = "hikrobot"
    vendor = "Hikrobot"

    def __init__(self, config: IndustrialCameraConfig) -> None:
        super().__init__(config)
        self._cam = None
        self._dev_info = None
        self._is_gige = False

    @classmethod
    def sdk_available(cls) -> bool:
        return _MVS_OK

    @classmethod
    def sdk_status(cls) -> str:
        return _MVS_STATUS

    # ------------------------------------------------------------------ enumerate
    @classmethod
    def _enumerate(cls):
        _init_sdk()
        dev_list = mv.MV_CC_DEVICE_INFO_LIST()
        layer = mv.MV_GIGE_DEVICE | mv.MV_USB_DEVICE
        ret = mv.MvCamera.MV_CC_EnumDevices(layer, dev_list)
        if ret != 0:
            raise RuntimeError(f"MV_CC_EnumDevices failed 0x{ret:08X}")
        devices = []
        for i in range(dev_list.nDeviceNum):
            info = cast(dev_list.pDeviceInfo[i], POINTER(mv.MV_CC_DEVICE_INFO)).contents
            if info.nTLayerType == mv.MV_GIGE_DEVICE:
                g = info.SpecialInfo.stGigEInfo
                model, serial, user = _cstr(g.chModelName), _cstr(g.chSerialNumber), _cstr(g.chUserDefinedName)
                ip = g.nCurrentIp
                extra = {"ip": f"{(ip >> 24) & 255}.{(ip >> 16) & 255}.{(ip >> 8) & 255}.{ip & 255}", "gige": True}
            else:
                u = info.SpecialInfo.stUsb3VInfo
                model, serial, user = _cstr(u.chModelName), _cstr(u.chSerialNumber), _cstr(u.chUserDefinedName)
                extra = {"gige": False}
            devices.append((info, DeviceDescriptor(serial, f"{model} [{serial}] {user}".strip(), {"model": model, **extra})))
        return devices

    @classmethod
    def scan_devices(cls) -> List[DeviceDescriptor]:
        if not _MVS_OK:
            return []
        try:
            return [d for _, d in cls._enumerate()]
        except Exception as exc:
            log.error("Hikrobot scan failed: %s", exc)
            return []

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        if self._sdk_missing():
            return False
        with self._lock:
            self.disconnect()
            try:
                devices = self._enumerate()
                if not devices:
                    self.last_error = "No Hikrobot camera found"
                    return False
                wanted = (self.config.serial_number or "").strip()
                chosen = None
                for info, desc in devices:
                    if not wanted or desc.identifier == wanted:
                        chosen = (info, desc)
                        break
                if chosen is None:
                    # Name what IS plugged in. "serial 'X' not found" on its own reads as
                    # "no camera", and the operator goes looking at cables; the camera is
                    # right there under a different serial, and the fix is to clear the
                    # field (blank = first device) or paste the one below.
                    found = ", ".join(d.identifier for _, d in devices)
                    self.last_error = (f"Không có camera Hikrobot serial '{wanted}'. "
                                       f"Đang cắm: {found}. Để trống ô Serial Number "
                                       f"= dùng camera đầu tiên.")
                    return False
                info, desc = chosen
                cam = mv.MvCamera()
                ret = cam.MV_CC_CreateHandle(info)
                if ret != 0:
                    self.last_error = f"MV_CC_CreateHandle failed 0x{ret:08X}"
                    return False
                ret = cam.MV_CC_OpenDevice(mv.MV_ACCESS_Exclusive, 0)
                if ret != 0:
                    cam.MV_CC_DestroyHandle()
                    self.last_error = f"MV_CC_OpenDevice failed 0x{ret:08X}"
                    return False
                self._cam = cam
                self._dev_info = info
                self._is_gige = bool(desc.extra.get("gige"))
                if self._is_gige:
                    try:
                        psize = cam.MV_CC_GetOptimalPacketSize()
                        if psize > 0:
                            cam.MV_CC_SetIntValue("GevSCPSPacketSize", psize)
                    except Exception as exc:
                        log.warning("GevSCPSPacketSize: %s", exc)
                self.apply_config()
                self._info = CameraInfo(
                    camera_type=self.camera_type,
                    name="Hikrobot",
                    model=desc.extra.get("model", ""),
                    serial=desc.identifier,
                    width=self._get_int("Width"),
                    height=self._get_int("Height"),
                    fps=self._get_float("ResultingFrameRate"),
                    extra={"ip": desc.extra.get("ip", "")},
                )
                self._connected = True
                self.last_error = ""
                log.info("Connected %s", self._info.summary())
                return True
            except Exception as exc:
                self.last_error = f"Hikrobot connect failed: {exc}"
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
                for fn in ("MV_CC_StopGrabbing", "MV_CC_CloseDevice", "MV_CC_DestroyHandle"):
                    try:
                        getattr(cam, fn)()
                    except Exception:
                        pass

    def start(self) -> bool:
        with self._lock:
            if self._cam is None:
                self.last_error = "Camera not connected"
                return False
            ret = self._cam.MV_CC_StartGrabbing()
            if ret != 0:
                self.last_error = f"MV_CC_StartGrabbing failed 0x{ret:08X}"
                return False
            self._streaming = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._streaming = False
            if self._cam is not None:
                try:
                    self._cam.MV_CC_StopGrabbing()
                except Exception:
                    pass

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        cam = self._cam
        if cam is None or not self._streaming:
            return None
        out = mv.MV_FRAME_OUT()
        memset(byref(out), 0, sizeof(out))
        ret = cam.MV_CC_GetImageBuffer(out, self._grab_timeout_ms())
        if ret != 0:
            if ret not in (getattr(mv, "MV_E_NODATA", 0x80000007),):
                self.last_error = f"MV_CC_GetImageBuffer 0x{ret & 0xFFFFFFFF:08X}"
            return None
        try:
            fi = out.stFrameInfo
            w, h, n = int(fi.nWidth), int(fi.nHeight), int(fi.nFrameLen)
            buf = (c_ubyte * n)()
            memmove(byref(buf), out.pBufAddr, n)
            raw = np.frombuffer(buf, dtype=np.uint8)
            image = self._decode(raw, w, h, int(fi.enPixelType))
            if image is None:
                return None
            return self._make_frame(image)
        finally:
            cam.MV_CC_FreeImageBuffer(out)

    def _decode(self, raw: np.ndarray, w: int, h: int, pixel_type: int) -> Optional[np.ndarray]:
        pt = mv  # constants module
        try:
            if pixel_type == pt.PixelType_Gvsp_Mono8:
                return to_bgr(raw.reshape(h, w), "Mono8")
            if pixel_type == getattr(pt, "PixelType_Gvsp_BGR8_Packed", -1):
                return raw.reshape(h, w, 3)
            if pixel_type == getattr(pt, "PixelType_Gvsp_RGB8_Packed", -1):
                return to_bgr(raw.reshape(h, w, 3), "RGB8")
            for name, fmt in (("BayerRG8", "BayerRG8"), ("BayerGB8", "BayerGB8"), ("BayerGR8", "BayerGR8"), ("BayerBG8", "BayerBG8")):
                if pixel_type == getattr(pt, f"PixelType_Gvsp_{name}", -1):
                    return to_bgr(raw.reshape(h, w), fmt)
        except ValueError as exc:
            self.last_error = f"Frame reshape failed: {exc}"
            return None
        # Fallback: unknown/packed formats -> ask the SDK to convert to BGR8.
        try:
            conv = mv.MV_CC_PIXEL_CONVERT_PARAM()
            memset(byref(conv), 0, sizeof(conv))
            conv.nWidth, conv.nHeight = w, h
            conv.pSrcData = cast(raw.ctypes.data_as(POINTER(c_ubyte)), POINTER(c_ubyte))
            conv.nSrcDataLen = raw.size
            conv.enSrcPixelType = pixel_type
            conv.enDstPixelType = pt.PixelType_Gvsp_BGR8_Packed
            dst = (c_ubyte * (w * h * 3))()
            conv.pDstBuffer = dst
            conv.nDstBufferSize = w * h * 3
            ret = self._cam.MV_CC_ConvertPixelType(conv)
            if ret != 0:
                self.last_error = f"MV_CC_ConvertPixelType 0x{ret & 0xFFFFFFFF:08X}"
                return None
            return np.frombuffer(dst, dtype=np.uint8).reshape(h, w, 3).copy()
        except Exception as exc:
            self.last_error = f"Unsupported pixel type 0x{pixel_type:08X}: {exc}"
            return None

    # ------------------------------------------------------------------ features
    def _get_int(self, name: str) -> int:
        try:
            v = mv.MVCC_INTVALUE()
            memset(byref(v), 0, sizeof(v))
            if self._cam.MV_CC_GetIntValue(name, v) == 0:
                return int(v.nCurValue)
        except Exception:
            pass
        return 0

    def _get_float(self, name: str) -> float:
        try:
            v = mv.MVCC_FLOATVALUE()
            memset(byref(v), 0, sizeof(v))
            if self._cam.MV_CC_GetFloatValue(name, v) == 0:
                return float(v.fCurValue)
        except Exception:
            pass
        return 0.0

    def _set(self, kind: str, name: str, value) -> bool:
        fn = {"float": "MV_CC_SetFloatValue", "enum": "MV_CC_SetEnumValue", "bool": "MV_CC_SetBoolValue",
              "int": "MV_CC_SetIntValue", "enum_str": "MV_CC_SetEnumValueByString"}[kind]
        try:
            ret = getattr(self._cam, fn)(name, value)
            if ret != 0:
                self.last_error = f"{name} 0x{ret & 0xFFFFFFFF:08X}"
                return False
            return True
        except Exception as exc:
            self.last_error = f"{name}: {exc}"
            return False

    def set_exposure(self, exposure_us: float) -> bool:
        self._set("enum_str", "ExposureAuto", "Off")
        return self._set("float", "ExposureTime", float(exposure_us))

    def set_gain(self, gain: float) -> bool:
        self._set("enum_str", "GainAuto", "Off")
        return self._set("float", "Gain", float(gain))

    def set_frame_rate(self, fps: float) -> bool:
        if fps <= 0:
            return self._set("bool", "AcquisitionFrameRateEnable", False)
        self._set("bool", "AcquisitionFrameRateEnable", True)
        return self._set("float", "AcquisitionFrameRate", float(fps))

    def set_trigger_mode(self, enabled: bool) -> bool:
        return self._set("enum", "TriggerMode", mv.MV_TRIGGER_MODE_ON if enabled else mv.MV_TRIGGER_MODE_OFF)
