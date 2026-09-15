"""CameraEventWorker (QThread): owns the AI camera event channel.

One thread, independent from the video (CameraWorker) and from the PLC (PlcWorker), so the
three links fail and recover on their own. Every provider call happens here; the UI only sees
Qt signals.

Loop:  connect -> start_listening -> poll() -> emit -> periodic health_check -> reconnect on loss
"""
from __future__ import annotations

import logging
import queue
import time
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from ..camera.events.base_event_provider import CameraEventProvider
from ..camera.events.camera_event import CameraEvent, CameraEventType
from ..camera.events.event_manager import EventManager
from ..config.schemas import AiCameraConfig, CameraBrand

log = logging.getLogger("EVENT")


class EventChannelState:
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    DISABLED = "DISABLED"     # PC AI / YOLO mode: no camera event channel at all


class CameraEventWorker(QThread):
    event_received = Signal(object)          # CameraEvent
    state_changed = Signal(str, str)         # EventChannelState, message
    raw_event = Signal(str)                  # payload exactly as the camera sent it
    test_result = Signal(bool, str)          # Test Event / Test Camera outcome
    provider_info = Signal(str)              # human readable provider description

    def __init__(self, manager: EventManager, config: AiCameraConfig,
                 brand: CameraBrand = CameraBrand.HIKVISION) -> None:
        super().__init__()
        self._manager = manager
        self._config = config
        self._brand = brand
        self._provider: Optional[CameraEventProvider] = None
        self._commands: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._running = True
        self._want_connected = False
        self._state = EventChannelState.DISCONNECTED
        self._attempt = 0
        self._next_retry = 0.0
        self._events_seen = 0
        self._last_event_time = 0.0

    # ------------------------------------------------------------------ API (any thread)
    @property
    def state(self) -> str:
        return self._state

    @property
    def online(self) -> bool:
        return self._state == EventChannelState.ONLINE

    @property
    def events_seen(self) -> int:
        return self._events_seen

    def seconds_since_last_event(self) -> float:
        return 0.0 if self._last_event_time <= 0 else time.monotonic() - self._last_event_time

    def set_config(self, config: AiCameraConfig, brand: CameraBrand) -> None:
        self._commands.put(("config", (config, brand)))

    def request_connect(self) -> None:
        self._commands.put(("connect", None))

    def request_disconnect(self) -> None:
        self._commands.put(("disconnect", None))

    def request_disable(self) -> None:
        """PC AI / YOLO mode: stop the channel and report it as not used."""
        self._commands.put(("disable", None))

    def request_test_camera(self) -> None:
        self._commands.put(("test_camera", None))

    def request_test_event(self, seconds: float = 6.0) -> None:
        self._commands.put(("test_event", seconds))

    def simulate(self, action: str, region_id: str = "1") -> None:
        """Mock provider only: 'enter', 'exit', 'intrusion_on', 'intrusion_off', 'vehicle', 'disconnect'."""
        self._commands.put(("simulate", (action, region_id)))

    def stop_worker(self) -> None:
        self._running = False
        self._commands.put(("quit", None))

    # ------------------------------------------------------------------ thread body
    def run(self) -> None:
        log.debug("Camera event worker started")
        while self._running:
            self._drain_commands()
            if not self._running:
                break
            if self._want_connected:
                if self._provider is not None and self._provider.is_listening():
                    self._read_events()
                    self._check_health()
                else:
                    self._try_connect()
                    self._sleep(0.1)
            else:
                self._sleep(0.05)
        self._teardown()
        log.debug("Camera event worker stopped")

    def _sleep(self, seconds: float) -> None:
        try:
            self._handle(self._commands.get(timeout=seconds))
        except queue.Empty:
            pass

    def _drain_commands(self) -> None:
        for _ in range(16):
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            self._handle(cmd)

    # ------------------------------------------------------------------ commands
    def _handle(self, cmd: Tuple[str, Any]) -> None:
        name, arg = cmd
        try:
            if name == "quit":
                self._running = False
            elif name == "config":
                config, brand = arg
                was = self._want_connected
                self._release_provider()
                self._config, self._brand = config, brand
                self._set_state(EventChannelState.DISCONNECTED, "Reconfigured")
                if was:
                    self._want_connected = True
                    self._attempt = 0
                    self._next_retry = 0.0
            elif name == "connect":
                self._want_connected = True
                self._attempt = 0
                self._next_retry = 0.0
                self._try_connect(force=True)
            elif name == "disconnect":
                self._want_connected = False
                self._release_provider()
                self._set_state(EventChannelState.DISCONNECTED, "Disconnected")
            elif name == "disable":
                self._want_connected = False
                self._release_provider()
                self._set_state(EventChannelState.DISABLED, "Not used in PC AI / YOLO mode")
            elif name == "test_camera":
                self._do_test_camera()
            elif name == "test_event":
                self._do_test_event(float(arg or 6.0))
            elif name == "simulate":
                self._do_simulate(*arg)
        except Exception as exc:                      # a provider must never kill the thread
            log.exception("Event command %s failed: %s", name, exc)
            self._set_state(EventChannelState.ERROR, str(exc))

    # ------------------------------------------------------------------ connection
    def _ensure_provider(self) -> CameraEventProvider:
        if self._provider is None:
            self._provider = self._manager.create_provider(self._config, self._brand)
            self._provider.set_raw_listener(self.raw_event.emit)
            self.provider_info.emit(self._provider.describe())
        return self._provider

    def _release_provider(self) -> None:
        provider, self._provider = self._provider, None
        if provider is not None:
            try:
                provider.set_raw_listener(None)
                provider.disconnect()
            except Exception as exc:
                log.debug("provider disconnect: %s", exc)

    def _try_connect(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self._next_retry:
            return
        provider = self._ensure_provider()
        if self._attempt == 0:
            self._set_state(EventChannelState.CONNECTING, "Connecting to the event channel...")
        if provider.connect() and provider.start_listening():
            self._attempt = 0
            self._last_event_time = time.monotonic()
            self.provider_info.emit(provider.describe())
            self._set_state(EventChannelState.ONLINE, provider.describe())
            self.event_received.emit(CameraEvent.health(CameraEventType.CAMERA_CONNECTED,
                                                        provider.describe()))
            return
        self._attempt += 1
        delays = self._config.reconnect_delays_s or [1.0, 2.0, 5.0]
        self._next_retry = now + float(delays[min(self._attempt - 1, len(delays) - 1)])
        message = provider.last_error or "cannot open the event channel"
        self._set_state(EventChannelState.RECONNECTING if self._attempt > 1 else EventChannelState.ERROR,
                        f"{message} (retry #{self._attempt + 1})")

    # ------------------------------------------------------------------ reading
    def _read_events(self) -> None:
        provider = self._provider
        if provider is None:
            return
        try:
            events = provider.poll(0.3)
        except Exception as exc:
            log.exception("Event poll failed: %s", exc)
            self._on_lost(str(exc))
            return
        for event in events:
            if event.type_enum == CameraEventType.CAMERA_DISCONNECTED:
                self.event_received.emit(event)
                # the provider already reported it, so _on_lost must not repeat the event
                self._on_lost(event.description or "camera reported a disconnect", announce=False)
                return
            self._events_seen += 1
            self._last_event_time = time.monotonic()
            self.event_received.emit(event)
        if not provider.is_listening():
            self._on_lost(provider.last_error or "event stream closed")

    def _check_health(self) -> None:
        provider = self._provider
        if provider is None:
            return
        try:
            if not provider.health_check():
                self._on_lost(provider.last_error or "camera health check failed")
        except Exception as exc:
            self._on_lost(f"health check error: {exc}")

    def _on_lost(self, reason: str, announce: bool = True) -> None:
        log.error("AI event channel lost: %s", reason)
        if announce and self._state == EventChannelState.ONLINE:
            self.event_received.emit(CameraEvent.health(CameraEventType.CAMERA_DISCONNECTED, reason))
        try:
            if self._provider is not None:
                self._provider.stop_listening()
        except Exception:
            pass
        self._attempt = 0
        self._next_retry = time.monotonic() + float((self._config.reconnect_delays_s or [1.0])[0])
        self._set_state(EventChannelState.RECONNECTING, f"Event channel lost ({reason}) - reconnecting")

    # ------------------------------------------------------------------ tests / simulation
    def _do_test_camera(self) -> None:
        provider = self._ensure_provider()
        tester = getattr(provider, "test_connection", None)
        if callable(tester):
            ok, message = tester()
        else:
            ok = provider.connect()
            message = provider.describe() if ok else (provider.last_error or "connect failed")
        self.test_result.emit(ok, f"Camera: {message}")

    def _do_test_event(self, seconds: float) -> None:
        provider = self._ensure_provider()
        if self.online:
            self.test_result.emit(True, f"Event channel already ONLINE ({self._events_seen} event(s) received)")
            return
        tester = getattr(provider, "test_event_channel", None)
        if callable(tester):
            ok, message = tester(seconds)
        else:
            ok = provider.connect() and provider.start_listening()
            message = "stream opened" if ok else (provider.last_error or "cannot open the event stream")
            provider.stop_listening()
        self.test_result.emit(ok, f"Event channel: {message}")

    def _do_simulate(self, action: str, region_id: str) -> None:
        provider = self._ensure_provider()
        if not provider.supports_simulation:
            self.test_result.emit(False, "Simulation needs the 'Simulated events' provider")
            return
        actions = {
            "enter": lambda: provider.simulate_person_enter(region_id),
            "exit": lambda: provider.simulate_person_exit(region_id),
            "intrusion_on": lambda: provider.simulate_intrusion(True, region_id),
            "intrusion_off": lambda: provider.simulate_intrusion(False, region_id),
            "vehicle": lambda: provider.simulate_vehicle(region_id),
            "disconnect": provider.simulate_disconnect,
            "reconnect": provider.simulate_reconnect,
        }
        fn = actions.get(action)
        if fn is None:
            return
        fn()
        if action == "reconnect":
            self._attempt = 0
            self._next_retry = 0.0
            self._want_connected = True

    # ------------------------------------------------------------------ helpers
    def _set_state(self, state: str, message: str = "") -> None:
        if state != self._state:
            log.info("Event channel %s -> %s%s", self._state, state, f" ({message})" if message else "")
        self._state = state
        self.state_changed.emit(state, message)

    def _teardown(self) -> None:
        self._release_provider()

    def provider_supports_simulation(self) -> bool:
        provider = self._provider
        return bool(provider and provider.supports_simulation)
