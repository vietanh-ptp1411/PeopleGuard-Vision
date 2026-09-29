"""Two kinds of watched zone - ALARM and WARNING - each raising its own PLC bit.

Detection is identical for both; only the bit differs. These tests pin that down end to
end: model round-trip, processor, occupancy tracker, pipeline result, and the PLC mapper.
"""
import unittest

from visionguard.config.schemas import ContainmentMode, PlcConfig, SignalMode
from visionguard.logic.debounce import DebounceConfig
from visionguard.logic.occupancy_state_machine import OccupancyState, OccupancyTracker
from visionguard.plc.plc_manager import HOLD, WORD_CLEAR, WORD_OCCUPIED, WORD_WARNING, PlcOutputMapper, PlcOutputState
from visionguard.roi.roi_manager import RoiManager
from visionguard.roi.roi_model import Roi, RoiType, ZoneLevel, new_roi_id
from visionguard.roi.roi_processor import RoiProcessor
from visionguard.vision.detection import BBox, Detection

LEFT = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]     # x < 0.5
RIGHT = [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)]    # x > 0.5


def person_at(x: float, y: float = 0.5) -> Detection:
    return Detection(BBox(x * 1000 - 10, y * 1000 - 100, x * 1000 + 10, y * 1000), 0.9, 0, "person")


def instant() -> DebounceConfig:
    return DebounceConfig(on_delay_ms=0, off_delay_ms=0, min_detection_frames=1)


class ModelTests(unittest.TestCase):
    def test_old_include_zones_are_alarm_zones(self):
        roi = Roi.from_dict({"id": "ROI_001", "name": "x", "type": "include", "points": LEFT})
        self.assertTrue(roi.is_alarm)
        self.assertTrue(roi.is_include)
        self.assertEqual(roi.level, ZoneLevel.ALARM)
        self.assertEqual(roi.type.label, "Alarm")

    def test_warning_round_trip_and_alias(self):
        roi = Roi.from_dict({"id": "WRN_001", "name": "w", "type": "warning", "points": LEFT})
        self.assertTrue(roi.is_warning)
        self.assertTrue(roi.is_include)
        self.assertFalse(roi.is_alarm)
        self.assertEqual(Roi.from_dict(roi.to_dict()).type, RoiType.WARNING)
        self.assertEqual(RoiType.parse("alarm"), RoiType.INCLUDE)
        self.assertEqual(RoiType.parse("garbage"), RoiType.INCLUDE)

    def test_ids_and_default_names_per_type(self):
        self.assertEqual(new_roi_id(RoiType.INCLUDE), "ROI_001")
        self.assertEqual(new_roi_id(RoiType.WARNING), "WRN_001")
        self.assertEqual(new_roi_id(RoiType.EXCLUDE), "EX_001")
        mgr = RoiManager("nonexistent/roi.json")
        alarm = mgr.create(RoiType.INCLUDE, LEFT)
        warn = mgr.create(RoiType.WARNING, RIGHT)
        self.assertEqual(alarm.name, "ROI 001")
        self.assertEqual(warn.name, "Warning 001")
        self.assertEqual([r.id for r in mgr.alarm_rois()], ["ROI_001"])
        self.assertEqual([r.id for r in mgr.warning_rois()], ["WRN_001"])
        self.assertEqual(len(mgr.include_rois()), 2)


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.alarm = Roi("A", "Alarm", type=RoiType.INCLUDE, points=LEFT)
        self.warn = Roi("W", "Warn", type=RoiType.WARNING, points=RIGHT)
        self.proc = RoiProcessor(ContainmentMode.FOOT_POINT)

    def test_same_containment_different_level(self):
        ev = self.proc.evaluate([person_at(0.25), person_at(0.75)], 1000, 1000, [self.alarm, self.warn])
        self.assertEqual(ev.in_roi, 2)
        self.assertEqual(ev.roi_counts, {"A": 1, "W": 1})
        self.assertEqual(ev.roi_levels, {"A": "alarm", "W": "warning"})
        self.assertTrue(ev.level_occupied(ZoneLevel.ALARM))
        self.assertTrue(ev.level_occupied(ZoneLevel.WARNING))

    def test_warning_only(self):
        ev = self.proc.evaluate([person_at(0.75)], 1000, 1000, [self.alarm, self.warn])
        self.assertFalse(ev.level_occupied(ZoneLevel.ALARM))
        self.assertTrue(ev.level_occupied(ZoneLevel.WARNING))

    def test_exclusion_applies_to_warning_zones_too(self):
        excl = Roi("X", "Ex", type=RoiType.EXCLUDE, points=RIGHT)
        ev = self.proc.evaluate([person_at(0.75)], 1000, 1000, [self.alarm, self.warn, excl])
        self.assertEqual(ev.ignored, 1)
        self.assertFalse(ev.level_occupied(ZoneLevel.WARNING))

    def test_only_warning_zones_means_no_full_frame_alarm(self):
        ev = self.proc.evaluate([person_at(0.25)], 1000, 1000, [self.warn])
        self.assertEqual(ev.outside, 1)
        self.assertFalse(ev.level_occupied(ZoneLevel.ALARM))

    def test_no_zone_at_all_is_a_full_frame_alarm(self):
        ev = self.proc.evaluate([person_at(0.25)], 1000, 1000, [])
        self.assertTrue(ev.level_occupied(ZoneLevel.ALARM))


class TrackerTests(unittest.TestCase):
    def test_level_machines_follow_their_zones(self):
        tr = OccupancyTracker(instant())
        levels = {"A": "alarm", "W": "warning"}
        tr.update({"A": 0, "W": 1}, 1.0, levels)
        self.assertFalse(tr.level_occupied("alarm"))
        self.assertTrue(tr.level_occupied("warning"))
        self.assertTrue(tr.area_occupied)
        tr.update({"A": 1, "W": 0}, 2.0, levels)
        self.assertTrue(tr.level_occupied("alarm"))
        self.assertFalse(tr.level_occupied("warning"))
        self.assertEqual(tr.level_states(), {"alarm": OccupancyState.OCCUPIED, "warning": OccupancyState.CLEAR})

    def test_level_bit_clears_when_its_last_zone_is_deleted(self):
        tr = OccupancyTracker(instant())
        tr.update({"W": 1}, 1.0, {"W": "warning"})
        self.assertTrue(tr.level_occupied("warning"))
        tr.update({}, 2.0, {})
        self.assertFalse(tr.level_occupied("warning"))

    def test_aggregate_ids_are_not_zones(self):
        tr = OccupancyTracker(instant())
        transitions = tr.update({"A": 1}, 1.0, {"A": "alarm"})
        ids = {t.roi_id for t in transitions}
        self.assertIn("A", ids)
        self.assertTrue(all(OccupancyTracker.is_aggregate_id(i) for i in ids - {"A"}))
        self.assertGreaterEqual(len(ids - {"A"}), 2)   # global + alarm level

    def test_unknown_level_defaults_to_alarm(self):
        tr = OccupancyTracker(instant())
        tr.update({"A": 1}, 1.0)
        self.assertTrue(tr.level_occupied("alarm"))


class MapperTests(unittest.TestCase):
    def cfg(self, dual=False):
        c = PlcConfig()
        c.mapping.device_person = "M100"
        c.mapping.device_warning = "M101"
        c.mapping.device_clear = "M102"
        c.mapping.device_status_word = "D100"
        c.word_output_enabled = True
        c.signal_mode = SignalMode.DUAL_BIT.value if dual else SignalMode.SINGLE_BIT.value
        return c

    def state(self, alarm=False, warning=False, running=True, fault=False):
        return PlcOutputState(running=running, fault=fault, area_occupied=alarm or warning,
                              alarm_occupied=alarm, warning_occupied=warning)

    def test_each_level_has_its_own_bit(self):
        out = PlcOutputMapper.map(self.state(warning=True), self.cfg(dual=True))
        self.assertEqual((out["M100"], out["M101"], out["M102"], out["D100"]), (0, 1, 0, WORD_WARNING))
        out = PlcOutputMapper.map(self.state(alarm=True), self.cfg(dual=True))
        self.assertEqual((out["M100"], out["M101"], out["M102"], out["D100"]), (1, 0, 0, WORD_OCCUPIED))
        out = PlcOutputMapper.map(self.state(alarm=True, warning=True), self.cfg(dual=True))
        self.assertEqual((out["M100"], out["M101"], out["M102"], out["D100"]), (1, 1, 0, WORD_OCCUPIED))
        out = PlcOutputMapper.map(self.state(), self.cfg(dual=True))
        self.assertEqual((out["M100"], out["M101"], out["M102"], out["D100"]), (0, 0, 1, WORD_CLEAR))

    def test_warning_bit_fails_like_the_alarm_bit(self):
        out = PlcOutputMapper.map(self.state(warning=True, fault=True), self.cfg())
        self.assertIs(out["M100"], HOLD)
        self.assertIs(out["M101"], HOLD)
        out = PlcOutputMapper.map(self.state(warning=True, running=False), self.cfg())
        self.assertIs(out["M101"], HOLD)

    def test_empty_warning_device_is_not_written(self):
        c = self.cfg()
        c.mapping.device_warning = ""
        out = PlcOutputMapper.map(self.state(warning=True), c)
        self.assertNotIn("", out)
        self.assertEqual(out["M100"], 0)


class PipelineTests(unittest.TestCase):
    def test_pipeline_result_carries_both_levels(self):
        import numpy as np
        from visionguard.camera.base_camera import Frame
        from visionguard.config.schemas import LogicConfig
        from visionguard.logic.pipeline import ProcessingPipeline

        mgr = RoiManager("nonexistent/roi.json")
        mgr.create(RoiType.INCLUDE, LEFT)
        mgr.create(RoiType.WARNING, RIGHT)
        pipe = ProcessingPipeline(mgr, LogicConfig(on_delay_ms=0, off_delay_ms=0, min_detection_frames=1))
        frame = Frame(np.zeros((1000, 1000, 3), dtype=np.uint8), 0, 0.0)
        res = pipe.process(frame, [person_at(0.75)], 1.0)
        self.assertFalse(res.alarm_occupied)
        self.assertTrue(res.warning_occupied)
        self.assertTrue(res.area_occupied)
        self.assertTrue(pipe.warning_occupied)
        self.assertFalse(pipe.alarm_occupied)
        res = pipe.process(frame, [person_at(0.25)], 1.0)
        self.assertTrue(res.alarm_occupied)
        self.assertFalse(res.warning_occupied)


if __name__ == "__main__":
    unittest.main()
