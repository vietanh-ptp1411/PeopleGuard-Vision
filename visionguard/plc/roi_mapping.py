"""Validate independent ROI bits against each other and the heartbeat."""
from __future__ import annotations

from typing import Dict

from ..config.schemas import PlcConfig
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
    reserved = [("Heartbeat", cfg.mapping.device_heartbeat)] if cfg.heartbeat.enabled else []
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


NO_ZONE_MESSAGE = "Chưa vẽ zone nào: vẽ vùng tại tab Zones, rồi gán bit cho từng vùng tại PLC → Devices"


def validate_rois(rois, cfg: PlcConfig, camera_count: int) -> None:
    """Validate stored polygons as well as their device assignments.

    The ROI bits are the only occupancy signal the PLC gets, so with no zone drawn the PLC
    hears nothing at all - that is an error, not a whole-picture fallback. Zones that exist
    but are all switched off are a deliberate pause and stay allowed, so their bits can clear.
    """
    watched = [roi for roi in rois if roi.is_include]
    if not watched:
        raise DeviceAddressError(NO_ZONE_MESSAGE)
    validate_roi_devices({roi.id: roi.plc_device for roi in watched}, cfg)
    enabled = [roi for roi in watched if roi.enabled]
    validate_roi_devices({roi.id: roi.plc_device for roi in enabled}, cfg, require_assigned=True)
    for roi in enabled:
        if not roi.is_valid() or not 0 <= roi.camera < camera_count:
            raise ValueError(f"{roi.id}: vùng hoặc camera không hợp lệ")
