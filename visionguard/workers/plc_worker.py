"""PlcWorker (QThread): all PLC I/O off the UI thread, with auto-reconnect and heartbeat."""
from __future__ import annotations

import logging
import queue
import time
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from ..config.schemas import PlcConfig
from ..plc.base_plc import PlcError
from ..plc.plc_manager import PlcManager, PlcOutputState

log = logging.getLogger("PLC")


class PlcWorker(QThread):
    connection_changed = Signal(bool, str)     # connected, message
    device_written = Signal(str, int)          # device, value (also heartbeat)
    heartbeat_toggled = Signal(bool)
    latency_changed = Signal(float)            # ms (moving average)
    memory_changed = Signal(object)            # dict device -> value (simulation / cache view)
    io_result = Signal(bool, str)              # manual test result: ok, message
    plc_error = Signal(str)

    def __init__(self, cfg: PlcConfig) -> None:
        super().__init__()
        self._manager = PlcManager(cfg)
        self._commands: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._running = True
        self._want_connected = False
        self._pending_state: Optional[PlcOutputState] = None
        self._reconnect_attempt = 0
        self._next_retry = 0.0
        self._last_mem_emit = 0.0
        self._last_mem: Dict[str, int] = {}
        self._last_latency_emit = 0.0

    # ------------------------------------------------------------------ API (any thread)
    @property
    def simulated(self) -> bool:
        return self._manager.simulated

    def is_connected(self) -> bool:
        return self._manager.is_connected()

    def reconfigure(self, cfg: PlcConfig) -> None:
        self._commands.put(("reconfigure", cfg))

    def request_connect(self) -> None:
        self._commands.put(("connect", None))

    def request_disconnect(self) -> None:
        self._commands.put(("disconnect", None))

    def update_output(self, state: PlcOutputState) -> None:
        """Coalesced: only the newest state is applied."""
        self._pending_state = state
        self._commands.put(("apply", None))

    def manual_write(self, device: str, value: int) -> None:
        self._commands.put(("write", (device, value)))

    def manual_read(self, device: str) -> None:
        self._commands.put(("read", device))

    def request_test(self) -> None:
        self._commands.put(("test", None))

    def stop_worker(self) -> None:
        self._running = False
        self._commands.put(("quit", None))

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        log.debug("PLC worker started")
        while self._running:
            try:
                cmd = self._commands.get(timeout=0.02)
                self._handle(cmd)
            except queue.Empty:
                pass
            if not self._running:
                break
            self._periodic()
        try:
            self._manager.disconnect()
        except Exception:
            pass
        log.debug("PLC worker stopped")

    def _handle(self, cmd: Tuple[str, Any]) -> None:
        name, arg = cmd
        try:
            if name == "quit":
                self._running = False
            elif name == "reconfigure":
                was = self._want_connected
                self._want_connected = False
                self._manager.reconfigure(arg)
                self.connection_changed.emit(False, "Reconfigured")
                self._emit_memory(force=True)
                if was:
                    self._do_connect()
            elif name == "connect":
                self._do_connect()
            elif name == "disconnect":
                self._want_connected = False
                self._manager.disconnect()
                self.connection_changed.emit(False, "Disconnected")
            elif name == "apply":
                self._apply_pending()
            elif name == "write":
                device, value = arg
                self._manager.manual_write(device, int(value))
                self.device_written.emit(device, int(value))
                self.io_result.emit(True, f"WRITE {device} = {int(value)} OK")
                self._emit_memory(force=True)
            elif name == "read":
                value = self._manager.manual_read(arg)
                self.io_result.emit(True, f"READ {arg} = {value}")
            elif name == "test":
                ok, msg = self._manager.driver.test_connection(self._manager.cfg.mapping.device_heartbeat or "SM400")
                self.io_result.emit(ok, f"Test connection: {msg}")
                if ok and not self._want_connected:
                    self._want_connected = True
                    self.connection_changed.emit(True, "Connected")
        except PlcError as exc:
            self._on_comm_error(str(exc))
            if name in ("write", "read", "test"):
                self.io_result.emit(False, str(exc))
        except Exception as exc:
            log.exception("PLC command %s failed: %s", name, exc)
            self.plc_error.emit(str(exc))

    # ------------------------------------------------------------------ actions
    def _do_connect(self) -> None:
        self._want_connected = True
        if self._manager.connect():
            self._reconnect_attempt = 0
            self.connection_changed.emit(True, self._manager.driver.name)
            try:
                written = self._manager.resync()
                for dev, val in written:
                    self.device_written.emit(dev, val)
            except PlcError as exc:
                self._on_comm_error(str(exc))
            self._emit_memory(force=True)
        else:
            self.connection_changed.emit(False, self._manager.last_error or "connect failed")
            self._schedule_retry()

    def _apply_pending(self) -> None:
        state, self._pending_state = self._pending_state, None
        if state is None:
            return
        if not self._manager.is_connected():
            self._manager._last_state = state  # remember for resync after reconnect
            return
        written = self._manager.apply_state(state)
        for dev, val in written:
            self.device_written.emit(dev, val)
        if written:
            self._emit_memory(force=True)

    def _periodic(self) -> None:
        now = time.monotonic()
        if self._manager.is_connected():
            try:
                hb = self._manager.heartbeat_tick(now)
                if hb is not None:
                    self.heartbeat_toggled.emit(hb)
                    self.device_written.emit(self._manager.cfg.mapping.device_heartbeat, int(hb))
                    self._emit_memory()
            except PlcError as exc:
                self._on_comm_error(str(exc))
            if now - self._last_latency_emit > 0.5:
                self._last_latency_emit = now
                self.latency_changed.emit(self._manager.latency_ms)
        elif self._want_connected and self._manager.cfg.connection.auto_reconnect:
            if now >= self._next_retry and self._next_retry > 0:
                self._next_retry = 0.0
                self._reconnect_attempt += 1
                log.info("PLC reconnect attempt %d", self._reconnect_attempt)
                if self._manager.connect():
                    self._reconnect_attempt = 0
                    self.connection_changed.emit(True, "Reconnected")
                    try:
                        for dev, val in self._manager.resync():
                            self.device_written.emit(dev, val)
                    except PlcError as exc:
                        self._on_comm_error(str(exc))
                    self._emit_memory(force=True)
                else:
                    self._schedule_retry()

    def _on_comm_error(self, message: str) -> None:
        log.error("PLC communication error: %s", message)
        try:
            self._manager.driver.disconnect()
        except Exception:
            pass
        self.connection_changed.emit(False, message)
        self.plc_error.emit(message)
        if self._want_connected:
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self._manager.cfg.connection.auto_reconnect:
            return
        delays = self._manager.cfg.connection.reconnect_delays_s or [1.0, 2.0, 5.0]
        delay = delays[min(self._reconnect_attempt, len(delays) - 1)]
        self._next_retry = time.monotonic() + float(delay)

    def _emit_memory(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_mem_emit < 0.25:
            return
        snap = self._manager.memory_snapshot()
        if force or snap != self._last_mem:
            self._last_mem = snap
            self._last_mem_emit = now
            self.memory_changed.emit(dict(snap))
