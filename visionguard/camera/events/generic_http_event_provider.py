"""GenericHttpEventProvider: any camera that streams or polls JSON / XML alarms over HTTP.

Two shapes are supported, decided automatically:
  * streaming  - the URL stays open and pushes payloads (multipart, chunked, newline JSON)
  * polling    - the URL answers one payload per request; it is re-requested every interval

Payloads are reused from the Hikvision parser (it already handles XML and JSON alarm shapes),
so a camera that mimics that format works out of the box. Anything unknown lands in RAW EVENT.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from ...config.schemas import AiCameraConfig
from .base_event_provider import CameraEventProvider
from .camera_event import CameraEvent
from .hikvision.hikvision_event_parser import HikvisionEventParser, HikvisionStreamParser
from .hikvision.hikvision_isapi_client import IsapiClient, IsapiError, mask_url

log = logging.getLogger("EVENT")


class GenericHttpEventProvider(CameraEventProvider):
    name = "Generic HTTP events"
    provider_type = "generic_http"

    def __init__(self, config: AiCameraConfig, path: str = "", poll_interval: float = 2.0) -> None:
        super().__init__()
        self.config = config
        self.path = (path or config.event_url or "").strip()
        self.poll_interval = poll_interval
        self.client = IsapiClient(config.base_url(), config.username, config.password,
                                  timeout=max(2.0, float(config.event_timeout_s) * 2))
        self.stream_parser = HikvisionStreamParser()
        self.parser = HikvisionEventParser(human_only=True,
                                           strict_human_only=config.logic.strict_human_only)
        self._chunks = None
        self._streaming = False
        self._last_poll = 0.0

    @classmethod
    def available(cls) -> bool:
        return IsapiClient.available()

    @classmethod
    def status(cls) -> str:
        return IsapiClient.status()

    def describe(self) -> str:
        return f"{self.name} @ {mask_url(self.client.url(self.path or '/'))}"

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        if not IsapiClient.available():
            self.last_error = IsapiClient.status()
            return False
        if not self.path:
            self.last_error = "No Event API URL configured for the generic provider"
            return False
        self._connected = True
        self.last_error = ""
        return True

    def disconnect(self) -> None:
        self.stop_listening()
        self.client.close()
        self._connected = False

    def start_listening(self) -> bool:
        if not self._connected and not self.connect():
            return False
        try:                                   # try a streaming read first
            self.client.open_stream(self.path, read_timeout=max(0.5, float(self.config.event_timeout_s)))
            self._chunks = self.client.iter_chunks(4096)
            self._streaming = True
        except IsapiError as exc:
            log.info("Generic provider: streaming not available (%s), falling back to polling", exc)
            self._streaming = False
            self._chunks = None
        self.stream_parser.reset()
        self._listening = True
        self._last_poll = 0.0
        return True

    def stop_listening(self) -> None:
        self._listening = False
        self._chunks = None
        self._streaming = False
        self.client.close_stream()

    # ------------------------------------------------------------------ reading
    def poll(self, timeout: float = 0.5) -> List[CameraEvent]:
        if not self._listening:
            return []
        return self._poll_stream(timeout) if self._streaming else self._poll_request()

    def _poll_stream(self, timeout: float) -> List[CameraEvent]:
        events: List[CameraEvent] = []
        deadline = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < deadline:
            chunk = self._next_chunk()
            if chunk is None:
                return events
            if not chunk:
                break
            for payload in self.stream_parser.feed(chunk):
                self._emit_raw(payload)
                events.extend(self.parser.parse(payload))
            if events:
                break
        return events

    def _next_chunk(self) -> Optional[bytes]:
        try:
            return next(self._chunks)
        except StopIteration:
            self.last_error = "Event stream closed"
            self._listening = False
            self._connected = False
            return None
        except Exception as exc:
            if "timed out" in str(exc).lower():
                return b""
            self.last_error = f"Event stream error: {exc}"
            self._listening = False
            self._connected = False
            return None

    def _poll_request(self) -> List[CameraEvent]:
        now = time.monotonic()
        if now - self._last_poll < self.poll_interval:
            return []
        self._last_poll = now
        try:
            text = self.client.get(self.path)
        except IsapiError as exc:
            self.last_error = str(exc)
            self._connected = False
            return []
        if not text.strip():
            return []
        self._emit_raw(text)
        return self.parser.parse(text)

    def health_check(self) -> bool:
        return self._connected
