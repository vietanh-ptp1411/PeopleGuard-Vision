"""ROI data model. Points are stored NORMALIZED (0..1) so any resolution works."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Tuple

from .geometry import clamp01, point_in_polygon, polygon_area

NormPoint = Tuple[float, float]


class RoiType(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"

    @property
    def label(self) -> str:
        return "Include" if self is RoiType.INCLUDE else "Exclusion"


@dataclass
class Roi:
    id: str
    name: str
    type: RoiType = RoiType.INCLUDE
    enabled: bool = True
    points: List[NormPoint] = field(default_factory=list)   # normalized (x/w, y/h)
    color: str = ""                                          # empty -> theme default
    plc_device: str = ""                                     # e.g. "M100" (include ROIs only)
    camera: int = 0                                          # which camera this zone is drawn on

    # ------------------------------------------------------------------ helpers
    @property
    def is_include(self) -> bool:
        return self.type == RoiType.INCLUDE

    @property
    def is_exclude(self) -> bool:
        return self.type == RoiType.EXCLUDE

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
        }
        if self.color:
            d["color"] = self.color
        if self.plc_device:
            d["plc_device"] = self.plc_device
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Roi":
        try:
            rtype = RoiType(str(d.get("type", "include")).lower())
        except ValueError:
            rtype = RoiType.INCLUDE
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
        )


def new_roi_id(rtype: RoiType, existing: List[str] | None = None) -> str:
    """Human friendly IDs: ROI_001 / EX_001 (falls back to uuid if exhausted)."""
    prefix = "ROI" if rtype == RoiType.INCLUDE else "EX"
    taken = set(existing or [])
    for i in range(1, 1000):
        cand = f"{prefix}_{i:03d}"
        if cand not in taken:
            return cand
    return f"{prefix}_{uuid.uuid4().hex[:6]}"
