import json

from visionguard.config.schemas import ContainmentMode
from visionguard.roi.roi_manager import RoiManager
from visionguard.roi.roi_model import Roi, RoiType
from visionguard.roi.roi_processor import RoiProcessor
from visionguard.vision.detection import BBox, Detection, DetectionZoneStatus

W, H = 1000, 1000
INCLUDE = Roi("ROI_001", "Robot Zone", RoiType.INCLUDE, True,
              [(0.20, 0.30), (0.80, 0.30), (0.85, 0.85), (0.15, 0.85)], plc_device="M100")
EXCLUDE = Roi("EX_001", "Walkway", RoiType.EXCLUDE, True,
              [(0.10, 0.10), (0.30, 0.10), (0.30, 0.90), (0.10, 0.90)])


def det(x1, y1, x2, y2, conf=0.9):
    return Detection(BBox(x1, y1, x2, y2), conf, 0, "person")


def test_foot_point_inside_outside_excluded():
    proc = RoiProcessor(ContainmentMode.FOOT_POINT)
    inside = det(450, 300, 550, 700)     # foot (500, 700) -> inside include
    outside = det(850, 100, 950, 250)    # foot (900, 250) -> outside
    excluded = det(200, 300, 280, 600)   # foot (240, 600) -> in exclusion (also inside include) -> ignored
    ev = proc.evaluate([inside, outside, excluded], W, H, [INCLUDE, EXCLUDE])
    assert [e.status for e in ev.detections] == [
        DetectionZoneStatus.IN_ROI, DetectionZoneStatus.OUTSIDE, DetectionZoneStatus.IGNORED_EXCLUSION]
    assert ev.total == 3 and ev.in_roi == 1 and ev.outside == 1 and ev.ignored == 1
    assert ev.roi_counts == {"ROI_001": 1}
    assert ev.any_occupied and ev.occupied_roi_ids() == ["ROI_001"]
    assert ev.detections[2].exclusion_id == "EX_001"


def test_center_point_mode_differs_from_foot():
    # bbox whose foot is outside (below ROI) but center inside
    d = det(450, 700, 550, 950)   # foot y=950 > 0.85*1000 -> outside ; center y=825 inside
    foot = RoiProcessor(ContainmentMode.FOOT_POINT).evaluate([d], W, H, [INCLUDE])
    center = RoiProcessor(ContainmentMode.CENTER_POINT).evaluate([d], W, H, [INCLUDE])
    assert foot.in_roi == 0 and center.in_roi == 1


def test_intersection_mode_threshold():
    d = det(700, 200, 900, 400)  # partially overlapping the include polygon
    low = RoiProcessor(ContainmentMode.INTERSECTION, 0.10).evaluate([d], W, H, [INCLUDE])
    high = RoiProcessor(ContainmentMode.INTERSECTION, 0.95).evaluate([d], W, H, [INCLUDE])
    assert low.in_roi == 1 and high.in_roi == 0


def test_disabled_and_invalid_rois_are_ignored():
    disabled = Roi("ROI_002", "off", RoiType.INCLUDE, False, INCLUDE.points)
    invalid = Roi("ROI_003", "bad", RoiType.INCLUDE, True, [(0.1, 0.1), (0.2, 0.2)])
    ev = RoiProcessor().evaluate([det(450, 300, 550, 700)], W, H, [disabled, invalid])
    assert ev.in_roi == 0 and ev.roi_counts == {}


def test_two_persons_one_inside():
    ev = RoiProcessor().evaluate([det(450, 300, 550, 700), det(850, 100, 950, 250)], W, H, [INCLUDE])
    assert ev.total == 2 and ev.in_roi == 1 and ev.any_occupied


def test_roi_manager_roundtrip(tmp_path):
    path = tmp_path / "roi_config.json"
    m = RoiManager(path)
    r = m.create(RoiType.INCLUDE, [(0.2, 0.3), (0.8, 0.3), (0.85, 0.85), (0.15, 0.85)], name="Robot Zone", plc_device="m100")
    e = m.create(RoiType.EXCLUDE, [(0.1, 0.1), (0.3, 0.1), (0.3, 0.9), (0.1, 0.9)])
    assert r.id == "ROI_001" and e.id == "EX_001" and r.plc_device == "M100"
    assert m.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rois"][0]["points"][0] == [0.2, 0.3]
    m2 = RoiManager(path)
    assert m2.load() and len(m2) == 2
    assert m2.get("ROI_001").name == "Robot Zone"
    assert m2.update_points("ROI_001", [(0, 0), (1, 0), (1, 1)])
    assert len(m2.get("ROI_001").points) == 3
    assert m2.delete("EX_001") and not m2.delete("EX_001")
    m2.clear()
    assert len(m2) == 0
