"""RTSP / IP camera adapter (Hikvision, Dahua, UNV, Ezviz, generic ONVIF/RTSP) via OpenCV+FFmpeg."""
from __future__ import annotations

import logging
import os
import socket
import time
from typing import Dict, Optional, Tuple
from urllib.parse import quote, urlparse

import cv2

from ..utils.redact import mask_url  # noqa: F401 (re-exported)
from ..config.schemas import RtspCameraConfig
from .base_camera import BaseCamera, CameraInfo, Frame

log = logging.getLogger("CAMERA")

# Vendor presets are only URL *templates*; nothing is hard-coded in the pipeline.
RTSP_PRESETS: Dict[str, Dict[str, str]] = {
    "generic": {"label": "Generic RTSP", "path": "/stream1", "port": "554"},
    "hikvision": {"label": "Hikvision", "path": "/Streaming/Channels/101", "port": "554"},
    "hikvision_sub": {"label": "Hikvision (sub-stream)", "path": "/Streaming/Channels/102", "port": "554"},
    "dahua": {"label": "Dahua", "path": "/cam/realmonitor?channel=1&subtype=0", "port": "554"},
    "dahua_sub": {"label": "Dahua (sub-stream)", "path": "/cam/realmonitor?channel=1&subtype=1", "port": "554"},
    "unv": {"label": "UNV / Uniview", "path": "/media/video1", "port": "554"},
    "ezviz": {"label": "Ezviz (RTSP enabled)", "path": "/h264/ch1/main/av_stream", "port": "554"},
    "axis": {"label": "Axis", "path": "/axis-media/media.amp", "port": "554"},
    "onvif": {"label": "ONVIF Profile S (generic)", "path": "/onvif1", "port": "554"},
}


def build_rtsp_url(cfg: RtspCameraConfig) -> str:
    if cfg.url.strip():
        return cfg.url.strip()
    auth = ""
    if cfg.username:
        auth = quote(cfg.username, safe="")
        if cfg.password:
            auth += ":" + quote(cfg.password, safe="")
        auth += "@"
    path = cfg.path if cfg.path.startswith("/") else "/" + cfg.path
    port = f":{cfg.port}" if cfg.port and cfg.port != 554 else ":554"
    return f"rtsp://{auth}{cfg.ip}{port}{path}"





_warned_transport = False


def _apply_transport_env(transport: str) -> None:
    """The FFmpeg capture options cannot be set here; say so if the config asks for it.

    This function used to write OPENCV_FFMPEG_CAPTURE_OPTIONS before every open, with the
    transport from the camera's config. The OpenCV 5 FFmpeg plugin reads that variable
    once, at import, and never again (measured: "timeout;1" set here still opened fine,
    "udp" set here still connected over TCP). The process-wide value is set in main.py,
    before cv2 is imported, and it is TCP. Timeouts that do work per camera go through the
    open parameters (CAP_PROP_OPEN_TIMEOUT_MSEC / CAP_PROP_READ_TIMEOUT_MSEC) in _open.

    OpenCV, and not PyAV, deliberately. PyAV opens four cameras in parallel where OpenCV
    serialises them - but inside this process, sharing the GIL with YOLO and the UI, its
    readers fell to 62-72% of real time and the picture drifted further behind every
    second (30 s after 80 s, measured). cap.read() is one C++ call that releases the GIL
    for decode and colour conversion both, and it holds 20 fps.
    """
    global _warned_transport
    transport = (transport or "tcp").lower()
    active = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    if transport != "tcp" and f"rtsp_transport;{transport}" not in active and not _warned_transport:
        _warned_transport = True
        log.warning("RTSP transport %r in the camera config has no effect: OpenCV fixes the transport "
                    "when it loads (currently %r). Set OPENCV_FFMPEG_CAPTURE_OPTIONS before start-up.",
                    transport, active or "FFmpeg default")


class RtspCamera(BaseCamera):
    camera_type = "rtsp"

    def __init__(self, config: RtspCameraConfig) -> None:
        super().__init__()
        self.config = config
        self._cap: Optional[cv2.VideoCapture] = None
        self._url = build_rtsp_url(config)
        self._info = CameraInfo(camera_type=self.camera_type, name=mask_url(self._url))
        self._consecutive_failures = 0

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        with self._lock:
            self.disconnect()
            self._url = build_rtsp_url(self.config)
            if not self._url.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
                self.last_error = f"Invalid stream URL: {mask_url(self._url)}"
                return False
            cap, err = self._open(self._url, self.config)
            if cap is None:
                self.last_error = err
                return False
            self._cap = cap
            self._info = CameraInfo(
                camera_type=self.camera_type,
                name=mask_url(self._url),
                width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=float(cap.get(cv2.CAP_PROP_FPS) or 0.0),
                extra={"transport": self.config.transport},
            )
            self._consecutive_failures = 0
            self._connected = True
            self.last_error = ""
            log.info("Connected RTSP %s", self._info.summary())
            return True

    @staticmethod
    def _open(url: str, cfg: RtspCameraConfig) -> Tuple[Optional[cv2.VideoCapture], str]:
        _apply_transport_env(cfg.transport)
        params = []
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            params += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(cfg.open_timeout_ms)]
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            params += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(cfg.read_timeout_ms)]
        # Through the open parameters and nowhere else. "threads;1" in the capture options
        # string is accepted and ignored by the OpenCV 5 FFmpeg plugin (measured: no change
        # from 1 to 12), and so is OPENCV_FFMPEG_THREADS. Only this property reaches the codec.
        if hasattr(cv2, "CAP_PROP_N_THREADS") and int(cfg.decode_threads) > 0:
            params += [cv2.CAP_PROP_N_THREADS, int(cfg.decode_threads)]
        try:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params) if params else cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        except (cv2.error, TypeError):
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            return None, f"Cannot open stream {mask_url(url)} (check IP/port/credentials/path)"
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, _ = cap.read()
        if not ok:
            cap.release()
            return None, f"Stream {mask_url(url)} opened but no frame received"
        return cap, ""

    def disconnect(self) -> None:
        with self._lock:
            self._streaming = False
            self._connected = False
            if self._cap is not None:
                try:
                    self._cap.release()
                except cv2.error:
                    pass
                self._cap = None

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        cap = self._cap
        if cap is None or not self._streaming:
            return None
        try:
            ok, img = cap.read()
        except cv2.error as exc:
            self.last_error = f"OpenCV read error: {exc}"
            ok, img = False, None
        if not ok or img is None:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 10:
                # FFmpeg keeps returning nothing: declare the link dead so the worker reconnects.
                self.last_error = "RTSP stream lost (no frames)"
                self._connected = False
            return None
        self._consecutive_failures = 0
        return self._make_frame(img)

    def get_device_info(self) -> CameraInfo:
        return self._info

    def reachable(self, timeout: float = 1.0) -> bool:
        """Does anything answer on the camera's RTSP port right now?

        One TCP handshake, no RTSP. A camera whose cable is out, or which is rebooting
        after a power cut (a PoE camera reboots when its cable is pulled, and takes about
        90 s to come back), does not answer at all - and then a full open sits through its
        5 s timeout for nothing. Measured against the real camera: a refused or absent
        port fails here in well under a second; a live one answers in a few ms.
        """
        parsed = urlparse(build_rtsp_url(self.config))
        host, port = parsed.hostname, parsed.port or 554
        if not host:
            return True                # let connect() produce the proper error message
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError as exc:
            self.last_error = f"{host}:{port} not answering ({exc.__class__.__name__})"
            return False

    # ------------------------------------------------------------------ test
    @staticmethod
    def test_connection(cfg: RtspCameraConfig) -> Tuple[bool, str]:
        url = build_rtsp_url(cfg)
        t0 = time.perf_counter()
        cap, err = RtspCamera._open(url, cfg)
        if cap is None:
            return False, err
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return True, f"OK {w}x{h} @ {fps:.0f}fps in {(time.perf_counter() - t0) * 1000:.0f} ms"
