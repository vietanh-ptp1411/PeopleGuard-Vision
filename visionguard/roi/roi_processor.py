"""RoiProcessor: decide for every detection whether it is IN ROI / OUTSIDE / IGNORED (exclusion).

Order of evaluation (per detection):
    1. compute the test point (foot / center) or the bbox for intersection mode
    2. if it falls in any enabled EXCLUDE ROI  -> IGNORED_EXCLUSION
    3. else if it falls in any enabled ALARM or WARNING ROI -> IN_ROI (list of ROI ids)
    4. else -> OUTSIDE

Alarm and warning zones are evaluated exactly the same way; the evaluation only records
which level each zone has (`roi_levels`) so the occupancy tracker can raise one bit per level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..config.schemas import ContainmentMode
from ..vision.detection import Detection, DetectionZoneStatus, EvaluatedDetection
from .geometry import bbox_polygon_intersection_ratio, point_in_polygon
from .roi_model import Roi, ZoneLevel, full_frame_roi


@dataclass
class RoiEvaluation:
    detections: List[EvaluatedDetection] = field(default_factory=list)
    roi_counts: Dict[str, int] = field(default_factory=dict)   # watched ROI id -> persons inside
    roi_levels: Dict[str, str] = field(default_factory=dict)   # watched ROI id -> ZoneLevel value
    total: int = 0
    in_roi: int = 0
    outside: int = 0
    ignored: int = 0

    @property
    def any_occupied(self) -> bool:
        return self.in_roi > 0

    def occupied_roi_ids(self) -> List[str]:
        return [rid for rid, n in self.roi_counts.items() if n > 0]

    def level_count(self, level: ZoneLevel) -> int:
        """Persons inside zones of one level (a person in two zones counts twice)."""
        return sum(n for rid, n in self.roi_counts.items() if self.roi_levels.get(rid) == level.value)

    def level_occupied(self, level: ZoneLevel) -> bool:
        return self.level_count(level) > 0


class RoiProcessor:
    def __init__(self, mode: ContainmentMode = ContainmentMode.FOOT_POINT, intersection_threshold: float = 0.3) -> None:
        self.mode = mode
        self.intersection_threshold = float(intersection_threshold)

    def configure(self, mode: ContainmentMode, intersection_threshold: float) -> None:
        self.mode = mode
        self.intersection_threshold = float(intersection_threshold)

    # ------------------------------------------------------------------ core
    def evaluate(self, detections: Sequence[Detection], width: int, height: int, rois: Sequence[Roi]) -> RoiEvaluation:
        includes = [r for r in rois if r.enabled and r.is_include and r.is_valid()]
        if not includes:
            # No zone drawn on this camera means the whole picture is the zone, not that
            # nothing is watched. A camera that was pointed at a machine and then left
            # without a polygon used to detect people all day and never raise anything -
            # a guard that looks like it is working and is not. Exclusions still apply.
            includes = [full_frame_roi()]
        excludes = [r for r in rois if r.enabled and r.is_exclude and r.is_valid()]
        result = RoiEvaluation(
            roi_counts={r.id: 0 for r in includes},
            roi_levels={r.id: (r.level or ZoneLevel.ALARM).value for r in includes},
        )
        w = float(max(1, width))
        h = float(max(1, height))

        for det in detections:
            test_pt_px = self._test_point(det)
            norm_pt = (test_pt_px[0] / w, test_pt_px[1] / h)
            norm_box = (det.bbox.x1 / w, det.bbox.y1 / h, det.bbox.x2 / w, det.bbox.y2 / h)

            excl_id = self._first_hit(norm_pt, norm_box, excludes)
            if excl_id is not None:
                ev = EvaluatedDetection(det, DetectionZoneStatus.IGNORED_EXCLUSION, [], test_pt_px, excl_id)
                result.ignored += 1
            else:
                hits = self._all_hits(norm_pt, norm_box, includes)
                if hits:
                    ev = EvaluatedDetection(det, DetectionZoneStatus.IN_ROI, hits, test_pt_px)
                    result.in_roi += 1
                    for rid in hits:
                        result.roi_counts[rid] = result.roi_counts.get(rid, 0) + 1
                else:
                    ev = EvaluatedDetection(det, DetectionZoneStatus.OUTSIDE, [], test_pt_px)
                    result.outside += 1
            result.detections.append(ev)
            result.total += 1
        return result

    # ------------------------------------------------------------------ helpers
    def _test_point(self, det: Detection) -> Tuple[float, float]:
        if self.mode == ContainmentMode.CENTER_POINT:
            return det.bbox.center
        return det.bbox.foot_point

    #: Any Overlap used to fire on any touch at all (1e-6 of the box). On the floor that
    #: meant a person walking along the edge of a zone, or an arm reaching over it, tripped
    #: the guard. A tenth of the box has to be inside now: still the most sensitive mode,
    #: still far below the 30% Intersection Percentage asks for, but no longer a graze.
    ANY_OVERLAP_MIN = 0.10

    def _inside(self, norm_pt, norm_box, roi: Roi) -> bool:
        if self.mode == ContainmentMode.ANY_OVERLAP:
            return bbox_polygon_intersection_ratio(norm_box, roi.points) >= self.ANY_OVERLAP_MIN - 1e-9
        if self.mode == ContainmentMode.INTERSECTION:
            return bbox_polygon_intersection_ratio(norm_box, roi.points) >= self.intersection_threshold
        # A clipped person's foot can lie exactly on the bottom image/ROI edge.
        # Include that boundary for FootPoint alarm zones, without expanding exclusions.
        return point_in_polygon(
            norm_pt, roi.points,
            include_boundary=self.mode == ContainmentMode.FOOT_POINT and roi.is_include,
        )

    def _excluded(self, norm_pt, norm_box, roi: Roi) -> bool:
        """Whether an exclusion zone swallows this detection.

        Deliberately NOT the permissive test, even in Any Overlap mode. Every containment
        mode answers "is this person in the zone", but the two kinds of zone want opposite
        biases from that answer: on an include zone, leaning towards yes means warning a
        little early, while on an exclude zone it means dropping a person who was standing
        in the danger area but whose box happened to clip the corner of somewhere we agreed
        to ignore. Early warnings are a nuisance; a dropped person is the failure this whole
        system exists to prevent. So exclusion always asks the strict question.
        """
        if self.mode == ContainmentMode.ANY_OVERLAP:
            return point_in_polygon(norm_pt, roi.points)
        return self._inside(norm_pt, norm_box, roi)

    def _first_hit(self, norm_pt, norm_box, rois: Sequence[Roi]) -> Optional[str]:
        for r in rois:
            if self._excluded(norm_pt, norm_box, r):
                return r.id
        return None

    def _all_hits(self, norm_pt, norm_box, rois: Sequence[Roi]) -> List[str]:
        return [r.id for r in rois if self._inside(norm_pt, norm_box, r)]
