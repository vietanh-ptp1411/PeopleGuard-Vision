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
        self._unexpected = 0
        self._state = CameraState.DISCONNECTED
        self._want_streaming = False
        # What the operator has asked for, as opposed to what the camera is doing. A
        # camera that cannot be reached is only a fault if somebody wanted it connected;
        # it is what tells the retry loop whether to keep going.
        self._want_connected = False
        self._reconnect_attempt = 0
        self._next_retry = 0.0
        self._unreachable_since = 0.0     # when the camera stopped answering its port (0 = it answers)
        self._last_frame_time = 0.0
        self._fps = FpsCounter()
        # Preview pacing. See _emit_preview for why this exists at all.
        self._preview_fps = 30.0
        self._preview_due = 0.0
        self._preview_inflight = False
        self._preview_sent_at = 0.0
        self._last_fps_emit = 0.0
        self._last_pos_emit = 0.0
        self._frame_sink = None

    def set_frame_sink(self, callback) -> None:
        """A nonblocking recorder callback, independent of preview delivery."""
        self._frame_sink = callback

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
    def run(self) -> None:
        log.debug("Camera worker started")
        while self._running:
            try:
                self._step()
            except Exception as exc:
                self._on_unexpected(exc)
        self._teardown()
        log.debug("Camera worker stopped")

    def _step(self) -> None:
        self._drain_commands()
        if not self._running:
            return
        cam = self._camera
        if cam is not None and self._state == CameraState.STREAMING:
            self._grab(cam)
        elif self._state == CameraState.RECONNECTING:
            self._try_reconnect()
            self._sleep_for_command(0.1)
        else:
            self._sleep_for_command(0.05)

    def _on_unexpected(self, exc: Exception) -> None:
        """A camera thread that dies takes its camera with it, silently and for good.

        OpenCV throws out of a plain cap.read() - a failed allocation, a codec giving up
        on a damaged stream. Unhandled it walks straight out of run(): PySide prints
        "Error calling Python override of QThread::run()" to a console nobody is reading,
        the tile freezes on its last picture, and no reconnect ever runs because the loop
        that would do the reconnecting is the thing that just died.

        From outside, this is indistinguishable from the camera being unplugged, so it
        goes down the path that already handles that and is already tested.
        """
        log.exception("Camera %d: unexpected error in the worker loop: %s", self.index, exc)
        self._unexpected += 1
        try:
            self._on_lost(f"unexpected error: {exc}")
        except Exception:
            log.exception("Camera %d: recovering from that error failed too", self.index)
            self._set_state(CameraState.ERROR, str(exc))
        # If it keeps throwing, back off instead of burning a core writing tracebacks.
        # _sleep_for_command returns at once when a stop arrives, so this never delays exit.
        self._sleep_for_command(min(0.5 * self._unexpected, 5.0))

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
        self._want_connected = True
        self._set_state(CameraState.CONNECTING, "Connecting...")
        cam = self._manager.create_video_source(self._config)
        if not cam.reachable(timeout=1.0):
            # Same probe the retry loop uses. Without it a camera that is simply not there
            # spent a 5 s FFmpeg open at every START - and OpenCV opens streams one at a
            # time, so every other camera's open waited those 5 s behind it.
            self._camera = None
            self._fail_or_retry(cam.last_error or "not answering")
            return False
        if not cam.connect():
            self._camera = None
            # Into the retry loop, not into a dead end. Reconnection used to cover only a
            # camera that had been streaming and then dropped; a camera that was already
            # down when START was pressed went to ERROR and stayed there for ever, however
            # long you waited and whatever you plugged back in. Restarting the whole app
            # was the only way out, which is exactly what it looked like from outside.
            self._fail_or_retry(cam.last_error or "connect failed")
            return False
        self._camera = cam
        self.info_changed.emit(cam.get_device_info())
        self._set_state(CameraState.CONNECTED, cam.get_device_info().summary())
        return True

    def _do_disconnect(self, silent: bool = False) -> None:
        cam, self._camera = self._camera, None
        if cam is not None:
            try:
                cam.stop()
                cam.disconnect()
            except Exception as exc:
                log.warning("disconnect: %s", exc)
        self._reconnect_attempt = 0
        if not silent:
            # An explicit Disconnect ends the retrying too. Silent disconnects are the
            # internal tidy-up _do_connect does before opening a new camera, and those
            # must not cancel the intent the caller is in the middle of expressing -
            # clearing _want_streaming here is what left a recovered camera sitting
            # connected and blank, because _do_start had set it one line earlier.
            self._want_connected = False
            self._want_streaming = False
        if not silent or self._state != CameraState.DISCONNECTED:
            self._set_state(CameraState.DISCONNECTED, "Disconnected")

    def _do_start(self) -> None:
        # Recorded first, before anything that can fail. START means "stream", and if the
        # camera is unreachable right now the retry loop has to know that streaming is
        # what it is retrying towards - otherwise it reconnects to a camera that then sits
        # there connected and showing nothing, which is its own kind of broken.
        self._want_streaming = True
        cam = self._camera
        if cam is None:
            if not self._do_connect():
                return
            cam = self._camera
        assert cam is not None
        if not cam.start():
            self._fail_or_retry(cam.last_error or "start failed")
            return
        self._buffer.clear()
        self._fps.reset()
        self._last_frame_time = time.monotonic()
        self._set_state(CameraState.STREAMING, "Streaming")

    def _fail_or_retry(self, reason: str) -> None:
        """Could not reach the camera. Keep trying if that is still what was asked for."""
        if self._config.reconnect.enabled and self._want_connected:
            self._reconnect_attempt = 0
            self._schedule_retry()
            self._set_state(CameraState.RECONNECTING, f"{reason} - retrying")
        else:
            self._set_state(CameraState.ERROR, reason)

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

    # ------------------------------------------------------------------ preview
    #: If the UI never acknowledges a frame (an exception on its side), let the preview
    #: resume rather than freeze for good.
    PREVIEW_STALE_S = 2.0

    def set_preview_fps(self, fps: float) -> None:
        """How often at most to hand the UI a picture. 0 means every frame."""
        self._preview_fps = max(0.0, float(fps or 0.0))

    def preview_delivered(self) -> None:
        """Called from the UI thread once it has taken the frame it was given."""
        self._preview_inflight = False

    def _emit_preview(self, frame, now: float) -> None:
        """Give the UI a frame only when it is ready for one.

        A queued signal has no backpressure. Emitting every captured frame means that
        the moment the UI thread falls behind - a dialog, a slow repaint, a busy machine,
        a video source with realtime off - the events pile up in its queue and each one
        holds a full picture. Measured at 652 frames/s across three cameras: 930 MB grew
        to 9955 MB in 49 seconds and was still climbing.

        Detection is not affected. The inference worker reads from the buffer, which
        keeps only the newest frame, so a skipped preview never costs a detection.
        """
        if self._preview_inflight and now - self._preview_sent_at < self.PREVIEW_STALE_S:
            return                       # the UI has not taken the last one yet
        interval = 1.0 / self._preview_fps if self._preview_fps > 0 else 0.0
        if interval and now < self._preview_due:
            return
        self._preview_due = max(now, self._preview_due) + interval
        self._preview_inflight = True
        self._preview_sent_at = now
        self.frame_ready.emit(frame)

    # ------------------------------------------------------------------ grabbing
    def _grab(self, cam: BaseCamera) -> None:
        frame = cam.get_frame(timeout=0.5)
        now = time.monotonic()
        if frame is not None:
            self._last_frame_time = now
            frame.camera = self.index      # the index travels with the picture
            self._buffer.put(frame)
            sink = self._frame_sink
            if sink is not None:
                sink(frame.image, now)
            self._emit_preview(frame, now)
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
        self._unreachable_since = 0.0
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
        cam = self._manager.create_video_source(self._config)
        if not cam.reachable(timeout=1.0):
            # Nothing on the port: cable out, camera rebooting. Ask again in a second
            # instead of spending a 5 s open timeout plus a 5 s back-off per attempt.
            # Measured before this: a camera that came back was noticed up to 10 s later,
            # and a 90 s reboot showed as ten failed attempts in the log. Probes are not
            # attempts - they are not counted, not logged one by one, and the operator sees
            # a running count of how long the camera has been silent.
            if not self._unreachable_since:
                self._unreachable_since = now
                log.warning("Camera %d: %s - waiting for it to answer", self.index, cam.last_error)
            silent = now - self._unreachable_since
            self.state_changed.emit(CameraState.RECONNECTING,
                                    f"Camera not answering for {silent:.0f}s ({cam.last_error})")
            self._next_retry = now + 1.0
            return
        if self._unreachable_since:
            log.info("Camera %d answers again after %.0fs - connecting",
                     self.index, now - self._unreachable_since)
            self._unreachable_since = 0.0
        self._reconnect_attempt += 1
        log.info("Camera reconnect attempt %d", self._reconnect_attempt)
        if cam.connect() and (cam.start() if self._want_streaming else True):
            self._camera = cam
            self._buffer.clear()
            self._last_frame_time = time.monotonic()
            self._reconnect_attempt = 0
            self.info_changed.emit(cam.get_device_info())
            if self._want_streaming:
                self._set_state(CameraState.STREAMING, "Reconnected")
            else:
                # Reconnected without starting the stream, because nobody had started it:
                # the operator pressed Connect and no more. Coming back up streaming would
                # be the worker deciding on its own to do something it was not asked to.
                self._set_state(CameraState.CONNECTED, cam.get_device_info().summary())
            return
        # Release it. connect() cleans up after itself when it fails, but a camera that
        # connected and then failed to start is still holding an open RTSP session, and
        # leaking one per attempt every five seconds is how you run a camera out of them.
        try:
            cam.stop()
            cam.disconnect()
        except Exception:
            pass
        self._schedule_retry()
        # Logged, not only signalled. Without this the log showed "reconnect attempt 7"
        # over and over with no hint as to what went wrong on attempts 1 through 6 - the
        # one thing anybody diagnosing this from a site log actually needs.
        reason = cam.last_error or "unknown error"
        log.warning("Camera reconnect attempt %d failed: %s", self._reconnect_attempt, reason)
        self.state_changed.emit(CameraState.RECONNECTING,
                                f"Reconnect failed ({reason}) - retry #{self._reconnect_attempt + 1}")

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
