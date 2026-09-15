"""Normalised AI camera event.

Every brand words its alarms differently (fielddetection, regionEntrance, CrossRegion,
SmartMotionHuman, ...). Providers translate whatever the camera sends into this one shape
so the state machine, the PLC layer and the UI never need brand-specific code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class CameraEventType(str, Enum):
    # --- detection / area events
    PERSON_DETECTED = "PERSON_DETECTED"
    PERSON_CLEARED = "PERSON_CLEARED"
    INTRUSION_START = "INTRUSION_START"
    INTRUSION_END = "INTRUSION_END"
    REGION_ENTER = "REGION_ENTER"
    REGION_EXIT = "REGION_EXIT"
    LINE_CROSS = "LINE_CROSS"
    # --- channel health
    CAMERA_CONNECTED = "CAMERA_CONNECTED"
    CAMERA_DISCONNECTED = "CAMERA_DISCONNECTED"
    CAMERA_ERROR = "CAMERA_ERROR"
    # --- anything the provider recognised but does not map to occupancy
    OTHER = "OTHER"

    @property
    def is_health(self) -> bool:
        return self in (CameraEventType.CAMERA_CONNECTED, CameraEventType.CAMERA_DISCONNECTED,
                        CameraEventType.CAMERA_ERROR)

    @property
    def starts_presence(self) -> bool:
        """Event that means 'a person is in the zone right now'."""
        return self in (CameraEventType.PERSON_DETECTED, CameraEventType.INTRUSION_START,
                        CameraEventType.REGION_ENTER)

    @property
    def ends_presence(self) -> bool:
        return self in (CameraEventType.PERSON_CLEARED, CameraEventType.INTRUSION_END,
                        CameraEventType.REGION_EXIT)


class TargetType(str, Enum):
    HUMAN = "human"
    VEHICLE = "vehicle"
    ANIMAL = "animal"
    OTHER = "other"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Optional[str]) -> "TargetType":
        if not value:
            return cls.UNKNOWN
        v = str(value).strip().lower()
        if v in ("human", "person", "people", "pedestrian", "man"):
            return cls.HUMAN
        if v in ("vehicle", "car", "truck", "bus", "motor", "motorcycle", "bicycle", "nonmotor", "non-motor"):
            return cls.VEHICLE
        if v in ("animal", "pet", "dog", "cat"):
            return cls.ANIMAL
        if v in ("unknown", "", "all", "any"):
            return cls.UNKNOWN
        return cls.OTHER


DEFAULT_REGION = "1"


@dataclass
class CameraEvent:
    """One alarm notification from the camera, already normalised."""
    event_type: str
    active: bool = True
    timestamp: datetime = field(default_factory=datetime.now)
    channel: int = 1
    region_id: Optional[str] = None
    target_type: Optional[str] = None
    count: Optional[int] = None          # targets reported by the camera, when it sends one
    description: str = ""
    raw_data: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ helpers
    @property
    def type_enum(self) -> CameraEventType:
        try:
            return CameraEventType(self.event_type)
        except ValueError:
            return CameraEventType.OTHER

    @property
    def target_enum(self) -> TargetType:
        return TargetType.parse(self.target_type)

    @property
    def is_human(self) -> bool:
        return self.target_enum == TargetType.HUMAN

    @property
    def is_non_human_target(self) -> bool:
        """True only when the camera explicitly reported a non-human target."""
        return self.target_enum in (TargetType.VEHICLE, TargetType.ANIMAL, TargetType.OTHER)

    @property
    def region(self) -> str:
        return str(self.region_id) if self.region_id not in (None, "") else DEFAULT_REGION

    def summary(self) -> str:
        parts = [self.event_type]
        if self.region_id:
            parts.append(f"region {self.region_id}")
        if self.target_type:
            parts.append(str(self.target_type))
        if self.count is not None:
            parts.append(f"x{self.count}")
        if not self.active and not self.type_enum.is_health:
            parts.append("(inactive)")
        if self.description:
            parts.append(f"- {self.description}")
        return "  ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "active": self.active,
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
            "channel": self.channel,
            "region_id": self.region_id,
            "target_type": self.target_type,
            "count": self.count,
            "description": self.description,
        }

    # ------------------------------------------------------------------ factories
    @classmethod
    def health(cls, event_type: CameraEventType, description: str = "") -> "CameraEvent":
        return cls(event_type=event_type.value, active=event_type != CameraEventType.CAMERA_DISCONNECTED,
                   description=description)
