"""Validate independent ROI bits against each other and system outputs."""
from __future__ import annotations

from typing import Dict

from ..config.schemas import PlcConfig, SignalMode
from .device_address import DeviceAddressError, parse_device
from .mitsubishi.mc_driver import series_uses_octal_xy


def normalize_roi_device(text: str, cfg: PlcConfig) -> str:
    octal = series_uses_octal_xy(cfg.connection.plc_series)
    address = parse_device(text, xy_octal=octal)
    if not address.is_bit or address.device in ("X", "DX"):
        raise DeviceAddressError("ROI cần bit đầu ra (ví dụ M200, Y10), không dùng thanh ghi D hoặc ngõ vào X")
    if octal and address.device == "Y":
        return f"Y{address.number:o}"
    return str(address)


def validate_roi_devices(devices: Dict[str, str], cfg: PlcConfig, *, require_assigned: bool = False) -> None:
    """Raise before writing anything if two logical outputs share a physical device."""
    octal = series_uses_octal_xy(cfg.connection.plc_series)
    used = {}
    m = cfg.mapping
    reserved = [("PERSON", m.device_person), ("Camera OK", m.device_camera_ok),
                ("AI Running", m.device_ai_running), ("Fault", m.device_fault)]
    if cfg.signal_mode == SignalMode.DUAL_BIT.value:
        reserved.append(("CLEAR", m.device_clear))
    if cfg.heartbeat.enabled:
        reserved.append(("Heartbeat", m.device_heartbeat))
    if cfg.word_output_enabled:
        reserved.append(("Status word", m.device_status_word))
    for label, text in reserved:
        if text:
            address = parse_device(text, xy_octal=octal)
            if address in used:
                raise DeviceAddressError(f"{label}: {text} trùng với {used[address]}")
            used[address] = label
    for rid, text in devices.items():
        if not text.strip():
            if require_assigned:
                raise DeviceAddressError(f"{rid}: chưa gán bit PLC; vào PLC → Devices, chọn ROI và nhập bit, ví dụ M200")
            continue
        canonical = normalize_roi_device(text, cfg)
        address = parse_device(canonical, xy_octal=octal)
        if address in used:
            raise DeviceAddressError(f"{rid}: bit {canonical} đã được dùng bởi {used[address]}")
        used[address] = rid


def validate_rois(rois, cfg: PlcConfig, camera_count: int) -> None:
    """Validate stored polygons as well as their device assignments."""
    watched = [roi for roi in rois if roi.is_include]
    validate_roi_devices({roi.id: roi.plc_device for roi in watched}, cfg)
    enabled = [roi for roi in watched if roi.enabled]
    validate_roi_devices({roi.id: roi.plc_device for roi in enabled}, cfg, require_assigned=True)
    for roi in enabled:
        if not roi.is_valid() or not 0 <= roi.camera < camera_count:
            raise ValueError(f"{roi.id}: vùng hoặc camera không hợp lệ")
