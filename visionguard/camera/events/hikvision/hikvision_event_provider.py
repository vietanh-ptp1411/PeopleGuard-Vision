"""Hikvision AI camera event provider (ISAPI alertStream).

connect()          -> GET /ISAPI/System/deviceInfo, proves the camera answers and the login works
start_listening()  -> opens /ISAPI/Event/notification/alertStream and keeps it open
poll()             -> reads whatever arrived, returns normalised CameraEvents
health_check()     -> periodic deviceInfo probe, because a silent stream is not proof of life
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from ....config.schemas import AiCameraConfig
from ..base_event_provider import CameraEventProvider
from ..camera_event import CameraEvent
from .hikvision_event_parser import HikvisionEventParser, HikvisionStreamParser
from .hikvision_isapi_client import ALERT_STREAM_PATH, IsapiClient, IsapiError, mask_url

log = logging.getLogger("EVENT")


class HikvisionEventProvider(CameraEventProvider):
    name = "Hikvision ISAPI"
    provider_type = "isapi"

    def __init__(self, config: AiCameraConfig) -> None:
        super().__init__()
        self.config = config
        self.client = IsapiClient(config.base_url(), config.username, config.password,
                                  timeout=max(2.0, float(config.event_timeout_s) * 2))
        self.stream_parser = HikvisionStreamParser()
        self.parser = HikvisionEventParser(human_only=True,
                                           strict_human_only=config.logic.strict_human_only)
        self._chunks = None
        self._device = ""
        self._last_data = 0.0
        self._last_health = 0.0

    # ------------------------------------------------------------------ availability
    @classmethod
    def available(cls) -> bool:
        return IsapiClient.available()

    @classmethod
    def status(cls) -> str:
        return IsapiClient.status()

    def describe(self) -> str:
        return f"{self.name} @ {self.config.ip}" + (f" ({self._device})" if self._device else "")

    # ------------------------------------------------------------------ connection
    def connect(self) -> bool:
        if not IsapiClient.available():
            self.last_error = IsapiClient.status()
            return False
        try:
            model, serial, firmware = self.client.device_info()
        except IsapiError as exc:
            self.last_error = str(exc)
            self._connected = False
            log.warning("Hikvision connect failed: %s", exc)
            return False
        except Exception as exc:  # never let a camera take the app down
            self.last_error = f"Unexpected error: {exc}"
            self._connected = False
            return False
        self._device = f"{model} SN {serial} FW {firmware}".strip()
        self._connected = True
        self.last_error = ""
        log.info("Hikvision camera %s: %s", self.config.ip, self._device)
        return True

    def disconnect(self) -> None:
        self.stop_listening()
        self.client.close()
        self._connected = False

    # ------------------------------------------------------------------ event stream
    def start_listening(self) -> bool:
        if not self._connected and not self.connect():
            return False
        path = (self.config.event_url or "").strip() or ALERT_STREAM_PATH
        if path.startswith("http"):                       # a full URL was configured
            path = path[path.find("/", 8):] or ALERT_STREAM_PATH
        try:
            self.client.open_stream(path, read_timeout=max(0.5, float(self.config.event_timeout_s)))
            self._chunks = self.client.iter_chunks(4096)
        except IsapiError as exc:
            self.last_error = str(exc)
            self._listening = False
            log.warning("Hikvision event stream failed: %s", exc)
            return False
        except Exception as exc:
            self.last_error = f"Unexpected error: {exc}"
            self._listening = False
            return False
        self.stream_parser.reset()
        self._listening = True
        self._last_data = time.monotonic()
        self._last_health = time.monotonic()
        self.last_error = ""
        return True

    def stop_listening(self) -> None:
        self._listening = False
        self._chunks = None
        self.client.close_stream()

    # ------------------------------------------------------------------ reading
    def poll(self, timeout: float = 0.5) -> List[CameraEvent]:
        if not self._listening or self._chunks is None:
            return []
        events: List[CameraEvent] = []
        deadline = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < deadline:
            chunk = self._next_chunk()
            if chunk is None:            # stream ended or died
                return events
            if not chunk:                # read timeout: normal, the camera is just quiet
                break
            self._last_data = time.monotonic()
            for payload in self.stream_parser.feed(chunk):
                self._emit_raw(payload)
                events.extend(self.parser.parse(payload))
            if events:
                break
        return events

    def _next_chunk(self) -> Optional[bytes]:
        """b'' = nothing right now, None = the stream is gone."""
        try:
            return next(self._chunks)
        except StopIteration:
            self.last_error = "Event stream closed by the camera"
            log.warning("Hikvision event stream closed")
            self._listening = False
            self._connected = False
            return None
        except Exception as exc:
            text = str(exc).lower()
            if "timed out" in text or "timeout" in text:
                return b""
            self.last_error = f"Event stream read error: {exc}"
            log.warning("Hikvision event stream error: %s", exc)
            self._listening = False
            self._connected = False
            return None

    # ------------------------------------------------------------------ health
    def health_check(self) -> bool:
        """A stream that sends nothing may still be dead: ask the camera directly."""
        now = time.monotonic()
        interval = max(5.0, float(self.config.health_interval_s))
        if now - self._last_health < interval:
            return self._connected
        self._last_health = now
        if now - self._last_data < interval:
            return self._connected          # data is flowing, no need to probe
        try:
            self.client.device_info()
            return True
        except Exception as exc:
            self.last_error = f"Camera not answering: {exc}"
            log.warning("Hikvision health check failed: %s", exc)
            self._connected = False
            self._listening = False
            return False

    # ------------------------------------------------------------------ diagnostics
    def test_connection(self) -> tuple[bool, str]:
        if not IsapiClient.available():
            return False, IsapiClient.status()
        try:
            model, serial, firmware = self.client.device_info()
            return True, f"OK - {model} (SN {serial}, firmware {firmware})"
        except IsapiError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Unexpected error: {exc}"

    def test_event_channel(self, seconds: float = 6.0) -> tuple[bool, str]:
        """Open the alarm stream briefly and report what came back."""
        opened = self.start_listening()
        if not opened:
            return False, self.last_error or "cannot open the event stream"
        url = mask_url(self.client.url((self.config.event_url or ALERT_STREAM_PATH)))
        seen: List[CameraEvent] = []
        raw_count = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self._next_chunk()
            if chunk is None:
                break
            if chunk:
                raw_count += 1
                for payload in self.stream_parser.feed(chunk):
                    self._emit_raw(payload)
                    seen.extend(self.parser.parse(payload))
        self.stop_listening()
        if seen:
            return True, f"Stream OK - {len(seen)} event(s) in {seconds:.0f}s, e.g. {seen[0].summary()}"
        if raw_count:
            return True, (f"Stream OK - camera is sending data but no person event in {seconds:.0f}s "
                          f"(walk into the zone and test again)")
        return True, (f"Stream open at {url} but silent for {seconds:.0f}s. Enable an AI rule "
                      f"(intrusion / region entrance) and 'Notify surveillance center' in the camera.")
