"""Detection data model shared by the vision, ROI and UI layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class BBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0

    @property
    def foot_point(self) -> Tuple[float, float]:
        """Bottom-center of the box: where the person touches the floor."""
        return (self.x1 + self.x2) / 2.0, self.y2

    def as_int(self) -> Tuple[int, int, int, int]:
        return int(round(self.x1)), int(round(self.y1)), int(round(self.x2)), int(round(self.y2))


@dataclass
class Detection:
    bbox: BBox
    confidence: float
    class_id: int
    class_name: str
    track_id: Optional[int] = None


class DetectionZoneStatus(str, Enum):
    OUTSIDE = "outside"
    IN_ROI = "in_roi"
    IGNORED_EXCLUSION = "ignored_exclusion"


@dataclass
class EvaluatedDetection:
    """A detection after ROI evaluation (what the UI draws and the state machine consumes)."""
    detection: Detection
    status: DetectionZoneStatus
    roi_ids: List[str] = field(default_factory=list)      # include ROIs the person is inside
    test_point: Optional[Tuple[float, float]] = None      # pixel point used for containment test
    exclusion_id: Optional[str] = None

    @property
    def in_roi(self) -> bool:
        return self.status == DetectionZoneStatus.IN_ROI
