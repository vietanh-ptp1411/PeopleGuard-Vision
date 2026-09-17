"""CameraWorker (QThread): owns the camera instance, grabs frames, auto-reconnects.

All camera SDK calls happen in this thread. The UI only enqueues commands and
listens to signals. Frames go to the LatestFrameBuffer (for inference) and are
also emitted for live preview.
"""
from __future__ import annotations

import logging
import queue
import time
from typing import Any, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from ..camera.base_camera import BaseCamera, CameraInfo
from ..camera.camera_manager import CameraManager
from ..camera.video_camera import VideoCamera
from ..config.schemas import CameraConfig
from ..utils.performance import FpsCounter
from .frame_buffer import LatestFrameBuffer

log = logging.getLogger("CAMERA")


class CameraState:
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"       # connected, not streaming
    STREAMING = "STREAMING"
    RECONNECTING = "RECONNECTING"
    LOST = "LOST"
    ERROR = "ERROR"
    FINISHED = "FINISHED"          # video file ended (not a fault)


class CameraWorker(QThread):
    frame_ready = Signal(object)              # Frame (preview)
    state_changed = Signal(str, str)          # state, message
    info_changed = Signal(object)             # CameraInfo
    fps_changed = Signal(float)
    video_position = Signal(int, int)         # current, total (video files)

    def __init__(self, manager: CameraManager, buffer: LatestFrameBuffer, config: CameraConfig,
                 index: int = 0) -> None:
        super().__init__()
        self._manager = manager
        self._buffer = buffer
        self._config = config
        self.index = int(index)      # which camera of the group this worker drives
        self._camera: Optional[BaseCamera] = None
        self._commands: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._running = True
        self._state = CameraState.DISCONNECTED
        self._want_streaming = False
        self._reconnect_attempt = 0
        self._next_retry = 0.0
        self._last_frame_time = 0.0
        self._fps = FpsCounter()
        self._last_fps_emit = 0.0
        self._last_pos_emit = 0.0

    # ------------------------------------------------------------------ public API (any thread)
    @property
    def state(self) -> str:
        return self._state

    def set_config(self, config: CameraConfig) -> None:
        self._commands.put(("config", config))

    def request_connect(self) -> None:
        self._commands.put(("connect", None))

    def request_disconnect(self) -> None:
        self._commands.put(("disconnect", None))

    def request_start(self) -> None:
        self._commands.put(("start", None))

    def request_stop(self) -> None:
        self._commands.put(("stop", None))

    def request_test(self) -> None:
        self._commands.put(("test", None))

    def video_command(self, name: str, arg: Any = None) -> None:
        self._commands.put(("video", (name, arg)))

    def stop_worker(self) -> None:
        self._running = False
        self._commands.put(("quit", None))

    # ------------------------------------------------------------------ thread body
    def run(self) -> None:  # noqa: C901 (worker loop)
        log.debug("Camera worker started")
        while self._running:
            self._drain_commands()
            if not self._running:
                break
            cam = self._camera
            if cam is not None and self._state == CameraState.STREAMING:
                self._grab(cam)
            elif self._state == CameraState.RECONNECTING:
                self._try_reconnect()
                self._sleep_for_command(0.1)
            else:
                self._sleep_for_command(0.05)
        self._teardown()
        log.debug("Camera worker stopped")

    def _sleep_for_command(self, timeout: float) -> None:
        try:
            cmd = self._commands.get(timeout=timeout)
        except queue.Empty:
            return
        self._handle(cmd)

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
                self._config = arg
                if self._camera is not None:
                    log.info("Camera config changed - reconnect required")
            elif name == "connect":
                self._do_connect()
            elif name == "disconnect":
                self._do_disconnect()
            elif name == "start":
                self._do_start()
            elif name == "stop":
                self._do_stop()
            elif name == "test":
                self._do_test()
            elif name == "video":
                self._do_video(*arg)
        except Exception as exc:  # never let a command kill the thread
            log.exception("Camera command %s failed: %s", name, exc)
            self._set_state(CameraState.ERROR, str(exc))

    def _do_connect(self) -> bool:
        self._do_disconnect(silent=True)
        self._set_state(CameraState.CONNECTING, "Connecting...")
        cam = self._manager.create_video_source(self._config)
        if not cam.connect():
            self._camera = None
            self._set_state(CameraState.ERROR, cam.last_error or "connect failed")
            return False
        self._camera = cam
        self.info_changed.emit(cam.get_device_info())
        self._set_state(CameraState.CONNECTED, cam.get_device_info().summary())
        return True

    def _do_disconnect(self, silent: bool = False) -> None:
        self._want_streaming = False
        cam, self._camera = self._camera, None
        if cam is not None:
            try:
                cam.stop()
                cam.disconnect()
            except Exception as exc:
                log.warning("disconnect: %s", exc)
        self._reconnect_attempt = 0
        if not silent or self._state != CameraState.DISCONNECTED:
            self._set_state(CameraState.DISCONNECTED, "Disconnected")

    def _do_start(self) -> None:
        cam = self._camera
        if cam is None:
            if not self._do_connect():
                return
            cam = self._camera
        assert cam is not None
        if not cam.start():
            self._set_state(CameraState.ERROR, cam.last_error or "start failed")
            return
        self._want_streaming = True
        self._buffer.clear()
        self._fps.reset()
        self._last_frame_time = time.monotonic()
        self._set_state(CameraState.STREAMING, "Streaming")

    def _do_stop(self) -> None:
        self._want_streaming = False
        cam = self._camera
        if cam is not None:
            cam.stop()
            self._set_state(CameraState.CONNECTED, "Stopped")
        else:
            self._set_state(CameraState.DISCONNECTED, "Disconnected")

    def _do_test(self) -> None:
        """Open, grab one frame, close - without touching the running camera."""
        cam = self._manager.create_video_source(self._config)
        t0 = time.perf_counter()
        ok = cam.connect() and cam.start()
        frame = cam.get_frame(2.0) if ok else None
        info = cam.get_device_info()
        cam.stop()
        cam.disconnect()
        if frame is not None:
            frame.camera = self.index
            self.frame_ready.emit(frame)
            self.state_changed.emit("TEST_OK", f"Test OK: {info.summary()} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
        else:
            self.state_changed.emit("TEST_FAIL", f"Test failed: {cam.last_error or 'no frame'}")

    def _do_video(self, name: str, arg: Any) -> None:
        cam = self._camera
        if not isinstance(cam, VideoCamera):
            return
        if name == "pause":
            cam.pause()
        elif name == "resume":
            cam.resume()
        elif name == "toggle":
            cam.toggle_pause()
        elif name == "restart":
            cam.restart()
            if self._state == CameraState.FINISHED:
                self._last_frame_time = time.monotonic()
                self._set_state(CameraState.STREAMING, "Streaming")
        elif name == "seek":
            cam.seek_fraction(float(arg))
        elif name == "loop":
            cam.set_loop(bool(arg))
            if self._state == CameraState.FINISHED and arg:
                self._last_frame_time = time.monotonic()
                self._set_state(CameraState.STREAMING, "Streaming")

    # ------------------------------------------------------------------ grabbing
    def _grab(self, cam: BaseCamera) -> None:
        frame = cam.get_frame(timeout=0.5)
        now = time.monotonic()
        if frame is not None:
            self._last_frame_time = now
            frame.camera = self.index      # the index travels with the picture
            self._buffer.put(frame)
            self.frame_ready.emit(frame)
            self._fps.tick(now)
            if now - self._last_fps_emit > 0.5:
                self._last_fps_emit = now
                self.fps_changed.emit(self._fps.fps)
            if isinstance(cam, VideoCamera) and now - self._last_pos_emit > 0.25:
                self._last_pos_emit = now
                self.video_position.emit(*cam.position())
            return

        if cam.is_paused():
            self._last_frame_time = now
            return
        if cam.is_finished():
            self._set_state(CameraState.FINISHED, "Video finished")
            self.fps_changed.emit(0.0)
            return
        timeout = float(self._config.reconnect.frame_timeout_s)
        if not cam.is_connected() or (now - self._last_frame_time) > timeout:
            self._on_lost(cam.last_error or f"No frame for {timeout:.0f}s")

    def _on_lost(self, reason: str) -> None:
        log.error("Camera lost: %s", reason)
        self.fps_changed.emit(0.0)
        cam, self._camera = self._camera, None
        if cam is not None:
            try:
                cam.stop()
                cam.disconnect()
            except Exception:
                pass
        if self._config.reconnect.enabled:
            self._reconnect_attempt = 0
            self._schedule_retry()
            self._set_state(CameraState.RECONNECTING, f"Camera lost ({reason}) - reconnecting")
        else:
            self._set_state(CameraState.LOST, f"Camera lost: {reason}")

    def _schedule_retry(self) -> None:
        delays = self._config.reconnect.delays_s or [1.0, 2.0, 5.0]
        delay = delays[min(self._reconnect_attempt, len(delays) - 1)]
        self._next_retry = time.monotonic() + float(delay)

    def _try_reconnect(self) -> None:
        now = time.monotonic()
        if now < self._next_retry:
            return
        rc = self._config.reconnect
        if rc.max_attempts and self._reconnect_attempt >= rc.max_attempts:
            self._set_state(CameraState.LOST, "Reconnect attempts exhausted")
            return
        self._reconnect_attempt += 1
        log.info("Camera reconnect attempt %d", self._reconnect_attempt)
        cam = self._manager.create_video_source(self._config)
        if cam.connect() and cam.start():
            self._camera = cam
            self._buffer.clear()
            self._last_frame_time = time.monotonic()
            self._reconnect_attempt = 0
            self.info_changed.emit(cam.get_device_info())
            self._set_state(CameraState.STREAMING, "Reconnected")
            return
        self._schedule_retry()
        self.state_changed.emit(CameraState.RECONNECTING, f"Reconnect failed ({cam.last_error}) - retry #{self._reconnect_attempt + 1}")

    # ------------------------------------------------------------------ helpers
    def _set_state(self, state: str, message: str = "") -> None:
        if state != self._state:
            log.info("Camera state %s -> %s%s", self._state, state, f" ({message})" if message else "")
        self._state = state
        self.state_changed.emit(state, message)

    def _teardown(self) -> None:
        cam, self._camera = self._camera, None
        if cam is not None:
            try:
                cam.stop()
                cam.disconnect()
            except Exception:
                pass

    def camera_info(self) -> Optional[CameraInfo]:
        cam = self._camera
        return cam.get_device_info() if cam else None
