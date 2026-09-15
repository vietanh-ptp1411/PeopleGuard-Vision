"""Dahua AI camera event provider (HTTP eventManager attach).

Dahua streams alarms as a multipart body of key=value blocks:

    Code=CrossRegionDetection;action=Start;index=0;data={
       "Object" : { "ObjectType" : "Human" },
       "RegionID" : [ 1 ]
    }

Not the MVP priority (Hikvision is), but implemented well enough to be usable and, above all,
so that selecting Dahua can never crash the application.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ....config.schemas import AiCameraConfig
from ..base_event_provider import CameraEventProvider
from ..camera_event import CameraEvent, CameraEventType, TargetType
from ..hikvision.hikvision_isapi_client import IsapiClient, IsapiError

log = logging.getLogger("EVENT")

ATTACH_PATH = ("/cgi-bin/eventManager.cgi?action=attach&codes=%5BCrossRegionDetection%2C"
               "CrossLineDetection%2CVideoMotion%2CSmartMotionHuman%5D")

#: Dahua code -> (event when action=Start, event when action=Stop)
CODE_MAP: Dict[str, Tuple[CameraEventType, Optional[CameraEventType]]] = {
    "crossregiondetection": (CameraEventType.INTRUSION_START, CameraEventType.INTRUSION_END),
    "regiondetection": (CameraEventType.INTRUSION_START, CameraEventType.INTRUSION_END),
    "crosslinedetection": (CameraEventType.LINE_CROSS, None),
    "smartmotionhuman": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
    "humandetect": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
}

_EVENT_RE = re.compile(r"Code=(?P<code>[^;]+);action=(?P<action>[^;]+);index=(?P<index>\d+)"
                       r"(?:;data=(?P<data>\{.*?\}\s*))?", re.DOTALL)


class DahuaEventProvider(CameraEventProvider):
    name = "Dahua HTTP events"
    provider_type = "dahua_http"

    def __init__(self, config: AiCameraConfig) -> None:
        super().__init__()
        self.config = config
        self.client = IsapiClient(config.base_url(), config.username, config.password,
                                  timeout=max(2.0, float(config.event_timeout_s) * 2))
        self._chunks = None
        self._buffer = ""

    @classmethod
    def available(cls) -> bool:
        return IsapiClient.available()

    @classmethod
    def status(cls) -> str:
        return IsapiClient.status()

    def describe(self) -> str:
        return f"{self.name} @ {self.config.ip}"

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> bool:
        if not IsapiClient.available():
            self.last_error = IsapiClient.status()
            return False
        try:
            self.client.get("/cgi-bin/magicBox.cgi?action=getDeviceType")
        except IsapiError as exc:
            self.last_error = str(exc)
            self._connected = False
            return False
        except Exception as exc:
            self.last_error = f"Unexpected error: {exc}"
            self._connected = False
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
        path = (self.config.event_url or "").strip() or ATTACH_PATH
        try:
            self.client.open_stream(path, read_timeout=max(0.5, float(self.config.event_timeout_s)))
            self._chunks = self.client.iter_chunks(4096)
        except IsapiError as exc:
            self.last_error = str(exc)
            self._listening = False
            return False
        self._buffer = ""
        self._listening = True
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
            try:
                chunk = next(self._chunks)
            except StopIteration:
                self.last_error = "Event stream closed by the camera"
                self._listening = False
                self._connected = False
                return events
            except Exception as exc:
                if "timed out" in str(exc).lower():
                    break
                self.last_error = f"Event stream error: {exc}"
                self._listening = False
                self._connected = False
                return events
            if not chunk:
                break
            self._buffer += chunk.decode("utf-8", errors="replace")
            events.extend(self._drain_buffer())
            if events:
                break
        return events

    def _drain_buffer(self) -> List[CameraEvent]:
        events: List[CameraEvent] = []
        last_end = 0
        for match in _EVENT_RE.finditer(self._buffer):
            last_end = match.end()
            self._emit_raw(match.group(0))
            event = self._to_event(match.groupdict())
            if event is not None:
                events.append(event)
        if last_end:
            self._buffer = self._buffer[last_end:]
        if len(self._buffer) > 100_000:
            self._buffer = self._buffer[-8192:]
        return events

    def _to_event(self, groups: Dict[str, Optional[str]]) -> Optional[CameraEvent]:
        code = (groups.get("code") or "").strip().lower()
        action = (groups.get("action") or "").strip().lower()
        if code not in CODE_MAP:
            return None
        on_type, off_type = CODE_MAP[code]
        mapped = on_type if action in ("start", "pulse") else off_type
        if mapped is None:
            return None
        region_id: Optional[str] = None
        target: Optional[str] = None
        data_text = groups.get("data")
        raw: Dict[str, object] = {"Code": code, "action": action}
        if data_text:
            try:
                data = json.loads(data_text)
                raw["data"] = data
                obj = data.get("Object") or {}
                target = obj.get("ObjectType") or data.get("ObjectType")
                regions = data.get("RegionID") or data.get("RegionId")
                if isinstance(regions, list) and regions:
                    region_id = str(regions[0])
                elif regions is not None:
                    region_id = str(regions)
            except json.JSONDecodeError:
                raw["data_raw"] = data_text[:1000]
        event = CameraEvent(
            event_type=mapped.value,
            active=action in ("start", "pulse"),
            timestamp=datetime.now(),
            channel=int(groups.get("index") or 0) + 1,
            region_id=region_id,
            target_type=target,
            description=f"Dahua {code} {action}",
            raw_data=raw,
        )
        if event.is_non_human_target:
            log.info("Ignored Dahua %s: target is %s", code, event.target_enum.value)
            return None
        if self.config.logic.strict_human_only and event.target_enum != TargetType.HUMAN:
            return None
        return event

    def health_check(self) -> bool:
        return self._connected
