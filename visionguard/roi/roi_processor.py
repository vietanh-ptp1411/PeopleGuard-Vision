"""RoiProcessor: decide for every detection whether it is IN ROI / OUTSIDE / IGNORED (exclusion).

Order of evaluation (per detection):
    1. compute the test point (foot / center) or the bbox for intersection mode
    2. if it falls in any enabled EXCLUDE ROI  -> IGNORED_EXCLUSION
    3. else if it falls in any enabled INCLUDE ROI -> IN_ROI (list of ROI ids)
    4. else -> OUTSIDE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..config.schemas import ContainmentMode
from ..vision.detection import Detection, DetectionZoneStatus, EvaluatedDetection
from .geometry import bbox_polygon_intersection_ratio, point_in_polygon
from .roi_model import Roi


@dataclass
class RoiEvaluation:
    detections: List[EvaluatedDetection] = field(default_factory=list)
    roi_counts: Dict[str, int] = field(default_factory=dict)   # include ROI id -> persons inside
    total: int = 0
    in_roi: int = 0
    outside: int = 0
    ignored: int = 0

    @property
    def any_occupied(self) -> bool:
        return self.in_roi > 0

    def occupied_roi_ids(self) -> List[str]:
        return [rid for rid, n in self.roi_counts.items() if n > 0]


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
        excludes = [r for r in rois if r.enabled and r.is_exclude and r.is_valid()]
        result = RoiEvaluation(roi_counts={r.id: 0 for r in includes})
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

    def _inside(self, norm_pt, norm_box, roi: Roi) -> bool:
        if self.mode == ContainmentMode.INTERSECTION:
            return bbox_polygon_intersection_ratio(norm_box, roi.points) >= self.intersection_threshold
        return point_in_polygon(norm_pt, roi.points)

    def _first_hit(self, norm_pt, norm_box, rois: Sequence[Roi]) -> Optional[str]:
        for r in rois:
            if self._inside(norm_pt, norm_box, r):
                return r.id
        return None

    def _all_hits(self, norm_pt, norm_box, rois: Sequence[Roi]) -> List[str]:
        return [r.id for r in rois if self._inside(norm_pt, norm_box, r)]
