"""PlcManager: maps system state -> PLC devices, writes only on change, toggles heartbeat.

Runs inside the PLC worker thread. It never touches the UI. Occupancy reaches the PLC only
through the per-ROI bits; the heartbeat is the one system device.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..config.schemas import FaultPersonOutput, PlcConfig
from ..utils.performance import MovingAverage
from .base_plc import BasePLC, PlcConfigError, PlcError
from .mitsubishi.mc_driver import MitsubishiMCDriver, series_uses_octal_xy
from .simulated_plc import SimulatedPLC
from .roi_mapping import normalize_roi_device, validate_roi_devices

log = logging.getLogger("PLC")

@dataclass
class PlcOutputState:
    """Everything the PLC needs to know, produced by the pipeline/controller."""
    running: bool = False               # system started (monitoring active)
    fault: bool = False
    roi_occupied: Dict[str, bool] = field(default_factory=dict)   # roi id -> occupied
    roi_devices: Dict[str, str] = field(default_factory=dict)     # roi id -> "M2xx"


HOLD = None  # sentinel: do not write this device now


class PlcOutputMapper:
    """Pure function: PlcOutputState + PlcConfig -> {device: value}. Values None = hold."""

    @staticmethod
    def occupancy_bit(state: PlcOutputState, cfg: PlcConfig, occupied: bool) -> Optional[int]:
        """The value of a ROI bit, with the stop / fault policy applied.

        A stopped system keeps the last value, a fault does whatever the fail-safe setting
        says, and only a running, healthy system is allowed to say 0.
        """
        if not state.running:
            return HOLD                                       # stopped: keep last value
        if state.fault:
            mode = cfg.failsafe.person_output_on_fault
            return 1 if mode == FaultPersonOutput.ON.value else 0 if mode == FaultPersonOutput.OFF.value else HOLD
        return 1 if occupied else 0

    @staticmethod
    def map(state: PlcOutputState, cfg: PlcConfig) -> Dict[str, Optional[int]]:
        out: Dict[str, Optional[int]] = {}
        bit = PlcOutputMapper.occupancy_bit
        try:
            validate_roi_devices(state.roi_devices, cfg)
        except ValueError as exc:
            raise PlcConfigError(str(exc)) from exc

        # --- per-ROI bits ------------------------------------------------------------
        for rid, dev in state.roi_devices.items():
            if dev:
                out[normalize_roi_device(dev, cfg)] = (
                    bit(state, cfg, state.roi_occupied.get(rid, False))
                    if rid in state.roi_occupied or state.fault or not state.running else HOLD)
        return out


class PlcManager:
    def __init__(self, cfg: PlcConfig) -> None:
        self.cfg = cfg
        self.driver: BasePLC = self.create_driver(cfg)
        self._cache: Dict[str, int] = {}          # last value written per device
        self._last_state: Optional[PlcOutputState] = None
        self._hb_value = False
        self._hb_written = False        # this heartbeat bit has been driven at least once
        self._hb_last = float("-inf")   # first heartbeat is due immediately after connect
        self.latency = MovingAverage(20)
        self.last_error = ""
        self._roi_devices: set[str] = set()
        self._retired_devices: set[str] = set()

    # ------------------------------------------------------------------ driver
    @staticmethod
    def create_driver(cfg: PlcConfig) -> BasePLC:
        if cfg.simulation_mode:
            return SimulatedPLC(xy_octal=series_uses_octal_xy(cfg.connection.plc_series))
        return MitsubishiMCDriver(cfg.connection)

    def reconfigure(self, cfg: PlcConfig) -> None:
        """Swap driver (sim <-> real / new IP). Caller must reconnect afterwards.

        On the same PLC, a bit we stop driving must not stay latched where we left it: a
        heartbeat or ROI bit moved from M110 to M12000 used to leave M110 sitting at 1 -
        "a bit nobody configured turned on". Those are retired (written 0 once healthy).
        Pointed at another PLC, the old addresses mean nothing there and are forgotten.
        """
        try:
            self.driver.disconnect()
        except Exception:
            pass
        old = self.cfg
        if _target(old) == _target(cfg):
            self._retired_devices |= self._roi_devices
            if self._hb_written and old.mapping.device_heartbeat:
                self._retired_devices.add(old.mapping.device_heartbeat)
            if cfg.heartbeat.enabled:
                self._retired_devices.discard(cfg.mapping.device_heartbeat)
        else:
            self._retired_devices.clear()
        self.cfg = cfg
        self.driver = self.create_driver(cfg)
        self._cache.clear()
        self._hb_value = False
        self._hb_written = False
        self._roi_devices.clear()

    @property
    def simulated(self) -> bool:
        return self.driver.is_simulated

    def connect(self) -> bool:
        ok = self.driver.connect()
        self.last_error = "" if ok else self.driver.last_error
        if ok:
            self._cache.clear()   # unknown PLC memory: force full resync
            self._hb_last = float("-inf")
        return ok

    def disconnect(self) -> None:
        self.driver.disconnect()
        self._cache.clear()

    def is_connected(self) -> bool:
        return self.driver.is_connected()

    # ------------------------------------------------------------------ output
    def apply_state(self, state: PlcOutputState, force: bool = False) -> List[Tuple[str, int]]:
        """Write every device whose desired value differs from the last written one."""
        self._last_state = state
        desired = PlcOutputMapper.map(state, self.cfg)
        current = {normalize_roi_device(dev, self.cfg) for dev in state.roi_devices.values() if dev}
        self._retired_devices.update(self._roi_devices - current)
        self._retired_devices.difference_update(current)
        self._roi_devices = current
        # Deleted/remapped ROIs must not leave a latched bit behind. Defer clearing
        # until monitoring is healthy; STOP and faults keep their existing policy.
        retired = set(self._retired_devices) if state.running and not state.fault else set()
        for device in retired:
            if device not in desired and device != self.cfg.mapping.device_heartbeat:
                desired[device] = 0
        written: List[Tuple[str, int]] = []
        for device, value in desired.items():
            if value is HOLD:
                continue
            if not force and self._cache.get(device) == value:
                continue
            self._write_device(device, int(value))
            written.append((device, int(value)))
        self._retired_devices.difference_update(retired)
        return written

    def resync(self) -> List[Tuple[str, int]]:
        """After a reconnect: rewrite the complete last state."""
        self._cache.clear()
        if self._last_state is None:
            return []
        return self.apply_state(self._last_state, force=True)

    def _write_device(self, device: str, value: int) -> None:
        t0 = time.perf_counter()
        try:
            if _is_bit(device, self.driver):
                self.driver.write_bit(device, bool(value))
                log.info("%s -> %s", device, "ON" if value else "OFF")
            else:
                self.driver.write_word(device, int(value))
                log.info("%s -> %d", device, value)
        except PlcError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.latency.add((time.perf_counter() - t0) * 1000.0)
        self._cache[device] = int(value)

    # ------------------------------------------------------------------ heartbeat
    def heartbeat_due(self, now: float) -> bool:
        hb = self.cfg.heartbeat
        return hb.enabled and bool(self.cfg.mapping.device_heartbeat) and (now - self._hb_last) * 1000.0 >= hb.interval_ms

    def heartbeat_tick(self, now: float) -> Optional[bool]:
        if not self.heartbeat_due(now):
            return None
        self._hb_last = now
        self._hb_value = not self._hb_value
        t0 = time.perf_counter()
        try:
            self.driver.write_bit(self.cfg.mapping.device_heartbeat, self._hb_value)
            self._hb_written = True
        except PlcError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.latency.add((time.perf_counter() - t0) * 1000.0)
        return self._hb_value

    @property
    def heartbeat_value(self) -> bool:
        return self._hb_value

    # ------------------------------------------------------------------ manual I/O (test screen)
    def manual_write(self, device: str, value: int) -> None:
        self._write_device(device, int(value))

    def manual_read(self, device: str) -> int:
        t0 = time.perf_counter()
        try:
            if _is_bit(device, self.driver):
                return int(self.driver.read_bit(device))
            return int(self.driver.read_word(device))
        finally:
            self.latency.add((time.perf_counter() - t0) * 1000.0)

    def memory_snapshot(self) -> Dict[str, int]:
        if isinstance(self.driver, SimulatedPLC):
            return self.driver.memory_snapshot()
        return dict(self._cache)

    @property
    def latency_ms(self) -> float:
        return self.latency.value


def _target(cfg: PlcConfig) -> tuple:
    """What identifies the physical PLC (and its address radix) behind a configuration."""
    c = cfg.connection
    return (cfg.simulation_mode, c.ip, c.port, c.network_no, c.pc_no, c.dest_module_io,
            c.dest_module_station, c.plc_series)


def _is_bit(device: str, driver: BasePLC) -> bool:
    from .device_address import parse_device

    xy_octal = getattr(driver, "xy_octal", False)
    try:
        return parse_device(device, xy_octal).is_bit
    except ValueError as exc:
        raise PlcError(str(exc)) from exc
