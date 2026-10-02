"""ROI data model. Points are stored NORMALIZED (0..1) so any resolution works."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .geometry import clamp01, point_in_polygon, polygon_area

NormPoint = Tuple[float, float]


class ZoneLevel(str, Enum):
    """Legacy pipeline aggregate labels; PLC outputs now use individual ROI IDs."""
    ALARM = "alarm"
    WARNING = "warning"

    @property
    def label(self) -> str:
        return "Alarm" if self is ZoneLevel.ALARM else "Warning"


class RoiType(str, Enum):
    #: One watched ROI type; keep the existing "include" spelling on disk.
    INCLUDE = "include"
    WARNING = "warning"   # accepted on input, migrated to INCLUDE by Roi
    EXCLUDE = "exclude"

    @property
    def label(self) -> str:
        return "Exclusion" if self is RoiType.EXCLUDE else "ROI"

    @property
    def is_watched(self) -> bool:
        """A zone people are looked for in, as opposed to one they are ignored in."""
        return self is not RoiType.EXCLUDE

    @property
    def level(self) -> Optional[ZoneLevel]:
        if self is RoiType.INCLUDE:
            return ZoneLevel.ALARM
        if self is RoiType.WARNING:
            return ZoneLevel.WARNING
        return None

    @classmethod
    def parse(cls, value: str) -> "RoiType":
        v = str(value or "include").strip().lower()
        if v in ("alarm", "warning", "roi"):
            return cls.INCLUDE
        try:
            return cls(v)
        except ValueError:
            return cls.INCLUDE


#: The zone a camera is watched with when nobody has drawn one on it: the whole picture.
#: It is never stored or listed, only handed to the processor at evaluation time, so it
#: can have no PLC address of its own - a person in it raises the group bit only.
FULL_FRAME_ID = "FULL_FRAME"


def full_frame_roi(camera: int = 0) -> "Roi":
    return Roi(id=FULL_FRAME_ID, name="Toàn khung hình", type=RoiType.INCLUDE, enabled=True,
               points=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], camera=int(camera))


@dataclass
class Roi:
    id: str
    name: str
    type: RoiType = RoiType.INCLUDE
    enabled: bool = True
    points: List[NormPoint] = field(default_factory=list)   # normalized (x/w, y/h)
    color: str = ""                                          # empty -> theme default
    plc_device: str = ""                                     # one output bit, e.g. "M200"
    camera: int = 0                                          # which camera this zone is drawn on

    def __post_init__(self) -> None:
        # Old Warning polygons keep their identity and geometry, but now drive their
        # own device just like every other watched ROI. Keep old exclusions as masks.
        self.type = RoiType.parse(self.type.value if isinstance(self.type, RoiType) else self.type)

    # ------------------------------------------------------------------ helpers
    @property
    def is_include(self) -> bool:
        """A watched ROI. Legacy exclusions remain masks."""
        return self.type.is_watched

    @property
    def is_alarm(self) -> bool:
        return self.type == RoiType.INCLUDE

    @property
    def is_warning(self) -> bool:
        return self.type == RoiType.WARNING

    @property
    def is_exclude(self) -> bool:
        return self.type == RoiType.EXCLUDE

    @property
    def level(self) -> Optional[ZoneLevel]:
        return self.type.level

    def is_valid(self) -> bool:
        return len(self.points) >= 3 and polygon_area(self.points) > 1e-6

    def pixel_points(self, width: int, height: int) -> List[Tuple[float, float]]:
        return [(x * width, y * height) for x, y in self.points]

    def contains_normalized(self, pt: NormPoint) -> bool:
        return point_in_polygon(pt, self.points)

    def clamp(self) -> None:
        self.points = [(clamp01(x), clamp01(y)) for x, y in self.points]

    # ------------------------------------------------------------------ (de)serialization
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "enabled": bool(self.enabled),
            "points": [[round(float(x), 5), round(float(y), 5)] for x, y in self.points],
            "camera": int(self.camera),
        }
        if self.color:
            d["color"] = self.color
        if self.plc_device:
            d["plc_device"] = self.plc_device
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Roi":
        rtype = RoiType.parse(d.get("type", "include"))
        pts_raw = d.get("points", []) or []
        pts: List[NormPoint] = []
        for p in pts_raw:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append((clamp01(float(p[0])), clamp01(float(p[1]))))
        return cls(
            id=str(d.get("id") or new_roi_id(rtype)),
            name=str(d.get("name") or d.get("id") or "ROI"),
            type=rtype,
            enabled=bool(d.get("enabled", True)),
            points=pts,
            color=str(d.get("color", "") or ""),
            plc_device=str(d.get("plc_device", "") or "").upper(),
            camera=int(d.get("camera", 0) or 0),
        )


def new_roi_id(rtype: RoiType, existing: List[str] | None = None) -> str:
    """Readable unique IDs for watched ROIs and legacy exclusion masks."""
    prefix = "EX" if rtype == RoiType.EXCLUDE else "ROI"
    taken = set(existing or [])
    for i in range(1, 1000):
        cand = f"{prefix}_{i:03d}"
        if cand not in taken:
            return cand
    while True:
        candidate = f"{prefix}_{uuid.uuid4().hex[:8]}"
        if candidate not in taken:
            return candidate
