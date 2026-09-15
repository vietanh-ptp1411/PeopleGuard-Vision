"""PlcManager: maps system state -> PLC devices, writes only on change, toggles heartbeat.

Runs inside the PLC worker thread. It never touches the UI.

Word status codes (device_status_word):
    0 CLEAR, 1 OCCUPIED, 2 CAMERA ERROR, 3 PLC ERROR, 4 AI ERROR, 5 NOT RUNNING
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..config.schemas import FaultPersonOutput, PlcConfig, SignalMode
from ..utils.performance import MovingAverage
from .base_plc import BasePLC, PlcError
from .mitsubishi.mc_driver import MitsubishiMCDriver, series_uses_octal_xy
from .simulated_plc import SimulatedPLC

log = logging.getLogger("PLC")

WORD_CLEAR = 0
WORD_OCCUPIED = 1
WORD_CAMERA_ERROR = 2
WORD_PLC_ERROR = 3
WORD_AI_ERROR = 4
WORD_NOT_RUNNING = 5


@dataclass
class PlcOutputState:
    """Everything the PLC needs to know, produced by the pipeline/controller."""
    running: bool = False               # system started (monitoring active)
    area_occupied: bool = False         # debounced global occupancy
    camera_ok: bool = False
    ai_ok: bool = False
    fault: bool = False
    fault_code: int = 0                 # WORD_CAMERA_ERROR / WORD_AI_ERROR ...
    roi_occupied: Dict[str, bool] = field(default_factory=dict)   # roi id -> occupied
    roi_devices: Dict[str, str] = field(default_factory=dict)     # roi id -> "M2xx"


HOLD = None  # sentinel: do not write this device now


class PlcOutputMapper:
    """Pure function: PlcOutputState + PlcConfig -> {device: value}. Values None = hold."""

    @staticmethod
    def map(state: PlcOutputState, cfg: PlcConfig) -> Dict[str, Optional[int]]:
        m = cfg.mapping
        out: Dict[str, Optional[int]] = {}
        dual = cfg.signal_mode == SignalMode.DUAL_BIT.value

        # --- PERSON / OCCUPIED bit ------------------------------------------------
        if not state.running:
            person: Optional[int] = HOLD                      # stopped: keep last value
        elif state.fault:
            mode = cfg.failsafe.person_output_on_fault
            person = 1 if mode == FaultPersonOutput.ON.value else 0 if mode == FaultPersonOutput.OFF.value else HOLD
        else:
            person = 1 if state.area_occupied else 0
        if m.device_person:
            out[m.device_person] = person

        # --- AREA CLEAR bit (dual-bit mode) -----------------------------------------
        if dual and m.device_clear:
            if not state.running:
                clear: Optional[int] = 0
            elif state.fault:
                clear = 0 if cfg.failsafe.clear_off_on_fault else HOLD
            else:
                clear = 0 if state.area_occupied else 1
            out[m.device_clear] = clear

        # --- diagnostics bits --------------------------------------------------------
        if m.device_camera_ok:
            out[m.device_camera_ok] = 1 if state.camera_ok else 0
        if m.device_ai_running:
            out[m.device_ai_running] = 1 if (state.running and state.ai_ok) else 0
        if m.device_fault:
            out[m.device_fault] = 1 if state.fault else 0

        # --- status word -------------------------------------------------------------
        if cfg.word_output_enabled and m.device_status_word:
            if not state.running:
                word = WORD_NOT_RUNNING
            elif state.fault:
                word = state.fault_code or WORD_AI_ERROR
            else:
                word = WORD_OCCUPIED if state.area_occupied else WORD_CLEAR
            out[m.device_status_word] = word

        # --- per-ROI bits ------------------------------------------------------------
        if m.write_roi_devices:
            for rid, dev in state.roi_devices.items():
                if not dev:
                    continue
                if not state.running:
                    val: Optional[int] = HOLD
                elif state.fault:
                    mode = cfg.failsafe.person_output_on_fault
                    val = 1 if mode == FaultPersonOutput.ON.value else 0 if mode == FaultPersonOutput.OFF.value else HOLD
                else:
                    val = 1 if state.roi_occupied.get(rid, False) else 0
                if dev not in out or out[dev] is HOLD:
                    out[dev] = val
        return out


class PlcManager:
    def __init__(self, cfg: PlcConfig) -> None:
        self.cfg = cfg
        self.driver: BasePLC = self.create_driver(cfg)
        self._cache: Dict[str, int] = {}          # last value written per device
        self._last_state: Optional[PlcOutputState] = None
        self._hb_value = False
        self._hb_last = float("-inf")   # first heartbeat is due immediately after connect
        self.latency = MovingAverage(20)
        self.last_error = ""

    # ------------------------------------------------------------------ driver
    @staticmethod
    def create_driver(cfg: PlcConfig) -> BasePLC:
        if cfg.simulation_mode:
            return SimulatedPLC(xy_octal=series_uses_octal_xy(cfg.connection.plc_series))
        return MitsubishiMCDriver(cfg.connection)

    def reconfigure(self, cfg: PlcConfig) -> None:
        """Swap driver (sim <-> real / new IP). Caller must reconnect afterwards."""
        try:
            self.driver.disconnect()
        except Exception:
            pass
        self.cfg = cfg
        self.driver = self.create_driver(cfg)
        self._cache.clear()
        self._hb_value = False

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
        written: List[Tuple[str, int]] = []
        for device, value in desired.items():
            if value is HOLD:
                continue
            if not force and self._cache.get(device) == value:
                continue
            self._write_device(device, int(value))
            written.append((device, int(value)))
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


def _is_bit(device: str, driver: BasePLC) -> bool:
    from .device_address import parse_device

    xy_octal = getattr(driver, "xy_octal", False)
    try:
        return parse_device(device, xy_octal).is_bit
    except ValueError as exc:
        raise PlcError(str(exc)) from exc
