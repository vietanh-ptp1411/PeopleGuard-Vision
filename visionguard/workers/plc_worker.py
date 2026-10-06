"""PlcWorker (QThread): all PLC I/O off the UI thread, with auto-reconnect and heartbeat."""
from __future__ import annotations

import logging
import queue
import time
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from ..config.schemas import PlcConfig
from ..plc.base_plc import PlcConfigError, PlcError
from ..plc.mitsubishi.mc_protocol import McProtocolError
from ..plc.plc_manager import PlcManager, PlcOutputState

log = logging.getLogger("PLC")

#: A state write the PLC refused is retried this often until it goes through.
WRITE_RETRY_S = 1.0


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
        #: Last protocol-level refusal, so it is reported once instead of every tick.
        self._protocol_fault = ""
        self._pending_state: Optional[PlcOutputState] = None
        self._reconnect_attempt = 0
        self._next_retry = 0.0
        self._last_mem_emit = 0.0
        self._last_mem: Dict[str, int] = {}
        self._last_latency_emit = 0.0
        #: The last health the controller reported. The heartbeat is gated on it.
        self._healthy = False
        #: When to rewrite the whole state after a write failed (0 = nothing to retry).
        self._retry_write_at = 0.0

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
            elif name == "resync":
                self._write_state(None)
            elif name == "write":
                device, value = arg
                self._manager.manual_write(device, int(value))
                self._clear_protocol_fault()       # the PLC accepts writes again
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
            self._on_comm_error(exc)
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
                self._write_state(None)
            except PlcError as exc:
                self._on_comm_error(exc)
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
        self._write_state(state)

    def _write_state(self, state: Optional[PlcOutputState]) -> None:
        """Write `state`, or with None rewrite the whole last state (after a (re)connect).

        A heartbeat that keeps ticking while the camera is dead tells the PLC the zone is
        being watched when it is not, so the heartbeat is gated on the health of the state
        that actually reached the PLC.

        Both paths set that gate. Only `apply` used to: a state that arrived while the link
        was still coming up was written by the resync on connect, but the gate stayed shut,
        and the heartbeat stood still until somebody walked into a zone.

        A failed write is retried every WRITE_RETRY_S. Before, one refused write (wrong
        routing, 'write during RUN' off, a PLC switched to STOP) froze the heartbeat and the
        zone bits until the occupancy happened to change, even after the PLC accepted again.
        """
        self._healthy = False
        self._retry_write_at = time.monotonic() + WRITE_RETRY_S
        written = self._manager.apply_state(state) if state is not None else self._manager.resync()
        self._retry_write_at = 0.0
        last = self._manager._last_state
        self._healthy = bool(last is not None and last.running and not last.fault)
        for dev, val in written:
            self.device_written.emit(dev, val)
        if written:
            self._clear_protocol_fault()
            self._emit_memory(force=True)

    def _heartbeat_frozen(self) -> bool:
        """True while the heartbeat must stand still because the system cannot see.

        This is what makes a two-device interface safe. With only a person bit, a dead
        camera and an empty zone look identical on the wire - both read 0. Freezing the
        heartbeat instead lets the watchdog the PLC already needs catch a blind camera,
        and costs no extra device.
        """
        return self._manager.cfg.heartbeat.stop_on_fault and not self._healthy

    def _periodic(self) -> None:
        now = time.monotonic()
        if self._manager.is_connected():
            if self._retry_write_at and now >= self._retry_write_at:
                self._handle(("resync", None))
            try:
                hb = None if self._heartbeat_frozen() else self._manager.heartbeat_tick(now)
                if hb is not None:
                    self.heartbeat_toggled.emit(hb)
                    self.device_written.emit(self._manager.cfg.mapping.device_heartbeat, int(hb))
                    self._emit_memory()
                    # Only a tick that actually wrote proves the refusal is over. Clearing
                    # on every call instead counted the gaps between heartbeats as success
                    # and flipped the chip green and red twice a second.
                    self._clear_protocol_fault()
            except PlcError as exc:
                self._on_comm_error(exc)
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
                        self._write_state(None)
                    except PlcError as exc:
                        self._on_comm_error(exc)
                    self._emit_memory(force=True)
                else:
                    self._schedule_retry()

    def _on_comm_error(self, exc: Exception) -> None:
        message = str(exc)
        if isinstance(exc, (McProtocolError, PlcConfigError)):
            self._on_protocol_fault(message)   # the link is fine: report, keep it, retry
            return
        log.error("PLC communication error: %s", message)
        try:
            self._manager.driver.disconnect()
        except Exception:
            pass
        self.connection_changed.emit(False, message)
        self.plc_error.emit(message)
        if self._want_connected:
            self._schedule_retry()

    def _on_protocol_fault(self, message: str) -> None:
        """The PLC answered and said no. Keep the link; do not reconnect.

        An end code is not a broken connection - the socket is fine and the PLC is
        talking to us. Dropping it and reconnecting is what turned a refused write into
        a connect/disconnect loop on site: every reconnect succeeded (TCP was never the
        problem), resync wrote again, got the same refusal, and tore the link down again,
        several times a second. Reconnecting cannot fix a configuration the PLC is
        enforcing, and the flapping hid the one line that said what was wrong.

        Reported once per distinct message, because the heartbeat retries it every tick.
        Still reported as NOT connected: writes are being refused, so the PLC is not
        being signalled, and an operator must not read that as a working link.
        """
        if message == self._protocol_fault:
            return
        self._protocol_fault = message
        log.error("PLC refused the request: %s", message)
        self.connection_changed.emit(False, message)
        self.plc_error.emit(message)

    def _clear_protocol_fault(self) -> None:
        if not self._protocol_fault:
            return
        self._protocol_fault = ""
        log.info("PLC is accepting writes again")
        self.connection_changed.emit(True, self._manager.driver.name)

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
