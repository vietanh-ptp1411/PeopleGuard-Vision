"""Turn the Hikvision alertStream bytes into CameraEvent objects.

The stream is a never ending multipart body; each part is one XML (sometimes JSON) alarm:

    --MIME_boundary
    Content-Type: application/xml; charset="UTF-8"

    <EventNotificationAlert version="2.0" xmlns="...">
      <channelID>1</channelID>
      <dateTime>2026-09-15T14:23:15+07:00</dateTime>
      <eventType>fielddetection</eventType>
      <eventState>active</eventState>
      <DetectionRegionList>
        <DetectionRegionEntry>
          <regionID>1</regionID>
          <detectionTarget>human</detectionTarget>
        </DetectionRegionEntry>
      </DetectionRegionList>
    </EventNotificationAlert>

Rather than trusting the boundary string (models disagree on it), the extractor simply pulls
out complete `<EventNotificationAlert>` documents, or complete JSON objects on models that
send JSON. Anything it cannot read is handed to the RAW EVENT tab and skipped.
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..camera_event import CameraEvent, CameraEventType, TargetType

log = logging.getLogger("EVENT")

XML_OPEN = b"<EventNotificationAlert"
XML_CLOSE = b"</EventNotificationAlert>"
MAX_BUFFER = 1_000_000   # a malformed stream must not eat memory

#: camera eventType (lower case) -> (event when active, event when inactive)
#: `None` means "this transition carries no occupancy meaning, ignore it".
EVENT_MAP: Dict[str, Tuple[Optional[CameraEventType], Optional[CameraEventType]]] = {
    # intrusion / field detection: the camera keeps the alarm active while a target stays inside
    "fielddetection": (CameraEventType.INTRUSION_START, CameraEventType.INTRUSION_END),
    "field_detection": (CameraEventType.INTRUSION_START, CameraEventType.INTRUSION_END),
    "intrusion": (CameraEventType.INTRUSION_START, CameraEventType.INTRUSION_END),
    "regiondetection": (CameraEventType.INTRUSION_START, CameraEventType.INTRUSION_END),
    # enter / exit: momentary, they are counted instead of held
    "regionentrance": (CameraEventType.REGION_ENTER, None),
    "regionentry": (CameraEventType.REGION_ENTER, None),
    "regionexiting": (CameraEventType.REGION_EXIT, None),
    "regionexit": (CameraEventType.REGION_EXIT, None),
    # line crossing: a pulse
    "linedetection": (CameraEventType.LINE_CROSS, None),
    "crossline": (CameraEventType.LINE_CROSS, None),
    # human / people detection where the camera reports presence directly
    "humandetection": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
    "peopledetection": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
    "humanattribute": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
    "humanrecognition": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
    "targetdetection": (CameraEventType.PERSON_DETECTED, CameraEventType.PERSON_CLEARED),
    # video health
    "videoloss": (CameraEventType.CAMERA_ERROR, None),     # inactive videoloss is the keepalive
    "shelteralarm": (CameraEventType.CAMERA_ERROR, None),
}

#: recognised but deliberately not driving occupancy (motion is not person specific)
IGNORED_TYPES = {"vmd", "motiondetection", "scenechangedetection", "diskfull", "diskerror",
                 "illegalaccess", "ipconflict", "nicbroken", "badvideo", "storagedetection",
                 "facedetection", "facesnap", "attendance", "audioexception", "pir", "io"}


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_time(text: str) -> datetime:
    if text:
        try:
            return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.now()


class HikvisionStreamParser:
    """Feed raw bytes in, get complete payload strings out."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes) -> List[str]:
        """Append bytes and return every complete alarm payload found so far."""
        if not chunk:
            return []
        self._buffer.extend(chunk)
        if len(self._buffer) > MAX_BUFFER:
            log.warning("Event buffer overflow (%d bytes) - dropping partial data", len(self._buffer))
            del self._buffer[:-8192]

        payloads: List[str] = []
        while True:
            end = self._buffer.find(XML_CLOSE)
            if end != -1:
                start = self._buffer.rfind(XML_OPEN, 0, end)
                if start == -1:                      # closing tag without an opening one
                    del self._buffer[: end + len(XML_CLOSE)]
                    continue
                payload = bytes(self._buffer[start : end + len(XML_CLOSE)])
                del self._buffer[: end + len(XML_CLOSE)]
                payloads.append(payload.decode("utf-8", errors="replace"))
                continue
            json_payload = self._take_json()
            if json_payload:
                payloads.append(json_payload)
                continue
            break
        return payloads

    def _take_json(self) -> Optional[str]:
        """Pull one balanced JSON object out of the buffer (models that stream JSON)."""
        start = self._buffer.find(b"{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(self._buffer)):
            ch = self._buffer[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == 0x5C:      # backslash
                    escape = True
                elif ch == 0x22:      # quote
                    in_string = False
                continue
            if ch == 0x22:
                in_string = True
            elif ch == 0x7B:          # {
                depth += 1
            elif ch == 0x7D:          # }
                depth -= 1
                if depth == 0:
                    payload = bytes(self._buffer[start : i + 1])
                    del self._buffer[: i + 1]
                    return payload.decode("utf-8", errors="replace")
        return None


class HikvisionEventParser:
    """Payload text -> CameraEvent list (one per detection region)."""

    def __init__(self, human_only: bool = True, strict_human_only: bool = False) -> None:
        self.human_only = human_only
        self.strict_human_only = strict_human_only
        self.unknown_types: Dict[str, int] = {}

    # ------------------------------------------------------------------ entry point
    def parse(self, payload: str) -> List[CameraEvent]:
        text = (payload or "").strip()
        if not text:
            return []
        if text.startswith("{"):
            return self._parse_json(text)
        return self._parse_xml(text)

    # ------------------------------------------------------------------ XML
    def _parse_xml(self, text: str) -> List[CameraEvent]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            log.debug("Unparsable alarm payload (%s)", exc)
            return []
        fields: Dict[str, str] = {}
        regions: List[Dict[str, str]] = []
        target_attrs: List[str] = []
        for node in root.iter():
            tag = strip_ns(node.tag)
            if tag == "DetectionRegionEntry":
                entry: Dict[str, str] = {}
                for child in node.iter():
                    ctag = strip_ns(child.tag)
                    if ctag != "DetectionRegionEntry" and child.text and child.text.strip():
                        entry.setdefault(ctag, child.text.strip())
                regions.append(entry)
            elif tag in ("targetType", "objectType", "detectionTarget"):
                if node.text and node.text.strip():
                    target_attrs.append(node.text.strip())
            elif node.text and node.text.strip() and tag not in fields:
                fields[tag] = node.text.strip()
        return self._build(fields, regions, target_attrs, text)

    # ------------------------------------------------------------------ JSON
    def _parse_json(self, text: str) -> List[CameraEvent]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            log.debug("Unparsable JSON alarm payload (%s)", exc)
            return []
        fields = {k: str(v) for k, v in data.items() if not isinstance(v, (dict, list))}
        regions: List[Dict[str, str]] = []
        targets: List[str] = []
        for key in ("DetectionRegionList", "detectionRegionList", "regions"):
            for entry in data.get(key, []) or []:
                if isinstance(entry, dict):
                    inner = entry.get("DetectionRegionEntry", entry)
                    regions.append({k: str(v) for k, v in inner.items() if not isinstance(v, (dict, list))})
        for key in ("targetType", "detectionTarget", "objectType"):
            if key in data and isinstance(data[key], str):
                targets.append(data[key])
        return self._build(fields, regions, targets, text)

    # ------------------------------------------------------------------ shared mapping
    def _build(self, fields: Dict[str, str], regions: List[Dict[str, str]], targets: List[str],
               raw_text: str) -> List[CameraEvent]:
        raw_type = (fields.get("eventType") or "").strip()
        key = raw_type.lower().replace("-", "").replace("_", "").replace(" ", "")
        state = (fields.get("eventState") or "active").strip().lower()
        active = state not in ("inactive", "false", "0", "end", "stop")
        channel = _to_int(fields.get("channelID") or fields.get("dynChannelID") or fields.get("channel"), 1)
        stamp = _parse_time(fields.get("dateTime", ""))
        description = fields.get("eventDescription", "") or raw_type
        post_count = _to_int(fields.get("activePostCount"), 0)

        if key in IGNORED_TYPES:
            return []
        if key not in EVENT_MAP:
            self.unknown_types[raw_type] = self.unknown_types.get(raw_type, 0) + 1
            if self.unknown_types[raw_type] == 1:
                log.info("Camera event type '%s' is not mapped - see the RAW EVENT tab", raw_type)
            return []

        on_type, off_type = EVENT_MAP[key]
        mapped = on_type if active else off_type
        if mapped is None:
            return []

        # videoloss/inactive is Hikvision's keepalive; only an ACTIVE one is a real problem
        if mapped == CameraEventType.CAMERA_ERROR and not active:
            return []

        base_raw = {"eventType": raw_type, "eventState": state, "channelID": channel,
                    "activePostCount": post_count, "payload": raw_text[:4000]}

        # one event per detection region, or a single event when the camera sends no region list
        entries = regions or [{}]
        events: List[CameraEvent] = []
        for entry in entries:
            region_id = entry.get("regionID") or entry.get("regionId") or fields.get("regionID")
            target = (entry.get("detectionTarget") or entry.get("targetType")
                      or (targets[0] if targets else None))
            event = CameraEvent(
                event_type=mapped.value,
                active=active,
                timestamp=stamp,
                channel=channel,
                region_id=str(region_id) if region_id not in (None, "") else None,
                target_type=target,
                count=_to_int(entry.get("targetNum") or fields.get("targetNum"), None),
                description=description,
                raw_data={**base_raw, "region": entry},
            )
            if self._rejected(event):
                continue
            events.append(event)
        return events

    def _rejected(self, event: CameraEvent) -> bool:
        """Drop events whose target is explicitly something other than a person."""
        if not self.human_only or event.type_enum.is_health:
            return False
        target = event.target_enum
        if target == TargetType.HUMAN:
            return False
        if target == TargetType.UNKNOWN:
            if self.strict_human_only:
                log.debug("Dropped %s: camera did not report a target type (strict mode)", event.event_type)
                return True
            return False
        log.info("Ignored %s in region %s: target is %s, not a person",
                 event.event_type, event.region, target.value)
        return True


def _to_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
