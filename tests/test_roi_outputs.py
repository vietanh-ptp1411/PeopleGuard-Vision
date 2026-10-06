"""Independent ROI outputs: persistence, detection, debounce and simulated PLC I/O."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from visionguard.camera.base_camera import Frame
from visionguard.config.config_manager import from_dict
from visionguard.config.schemas import ContainmentMode, LogicConfig, PlcConfig
from visionguard.logic.debounce import DebounceConfig
from visionguard.logic.occupancy_state_machine import OccupancyTracker
from visionguard.logic.pipeline import ProcessingPipeline
from visionguard.plc.base_plc import PlcError
from visionguard.plc.plc_manager import HOLD, PlcManager, PlcOutputMapper, PlcOutputState
from visionguard.plc.roi_mapping import NO_ZONE_MESSAGE, normalize_roi_device, validate_roi_devices, validate_rois
from visionguard.roi.roi_manager import RoiManager
from visionguard.roi.roi_model import Roi, RoiType
from visionguard.roi.roi_processor import RoiProcessor
from visionguard.vision.detection import BBox, Detection

LEFT = [(0., 0.), (.5, 0.), (.5, 1.), (0., 1.)]
RIGHT = [(.5, 0.), (1., 0.), (1., 1.), (.5, 1.)]


def person(x):
    return Detection(BBox(x * 1000 - 10, 400, x * 1000 + 10, 500), .9, 0, "person")


def state(a=False, b=False, **kwargs):
    return PlcOutputState(running=True,
                          roi_devices={"ROI_001": "M200", "ROI_002": "M201"},
                          roi_occupied={"ROI_001": a, "ROI_002": b}, **kwargs)


class PersistenceTests(unittest.TestCase):
    def test_warning_migrates_without_losing_identity_or_device(self):
        old = dict(id="WRN_012", name="Near robot", type="warning", points=RIGHT,
                   plc_device="M201", camera=1, enabled=False)
        roi = Roi.from_dict(old)
        self.assertEqual(roi.type, RoiType.INCLUDE)
        self.assertEqual(roi.to_dict(), {**old, "type": "include", "points": [list(p) for p in RIGHT]})
        self.assertEqual(Roi.from_dict(roi.to_dict()), roi)

    def test_ids_unique_across_cameras_and_never_reused_after_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "roi.json"
            mgr = RoiManager(path)
            a = mgr.create(RoiType.INCLUDE, LEFT, camera=0, plc_device="M200")
            b = mgr.create(RoiType.INCLUDE, RIGHT, camera=1, plc_device="M201")
            self.assertNotEqual(a.id, b.id)
            mgr.delete(a.id)
            mgr.save()
            other = RoiManager(path)
            self.assertTrue(other.load())
            self.assertEqual(other.get(b.id), b)
            c = other.create(RoiType.INCLUDE, LEFT)
            self.assertNotIn(c.id, (a.id, b.id))

    def test_duplicate_missing_and_reserved_ids_are_repaired(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "roi.json"
            path.write_text(json.dumps({"rois": [dict(id=i, points=LEFT) for i in
                                                ("ROI_001", "ROI_001", "", "FULL_FRAME", "__AREA__")]}))
            mgr = RoiManager(path)
            self.assertTrue(mgr.load())
            self.assertEqual(len(mgr.ids()), len(set(mgr.ids())))
            self.assertNotIn("FULL_FRAME", mgr.ids())
            self.assertNotIn("__AREA__", mgr.ids())

    def test_old_disabled_roi_output_switch_does_not_disable_new_outputs(self):
        cfg = from_dict(PlcConfig, {"mapping": {"write_roi_devices": False, "device_warning": "M101"}})
        self.assertEqual(PlcOutputMapper.map(state(b=True), cfg), {"M200": 0, "M201": 1})

    def test_old_exclusions_remain_masks(self):
        mask = Roi.from_dict(dict(id="EX_001", type="exclude", points=RIGHT))
        roi = Roi("ROI_001", "ROI", points=RIGHT)
        result = RoiProcessor(ContainmentMode.FOOT_POINT).evaluate([person(.75)], 1000, 1000, [roi, mask])
        self.assertEqual(result.ignored, 1)
        self.assertEqual(result.roi_counts[roi.id], 0)


class MappingTests(unittest.TestCase):
    def test_each_roi_only_drives_its_bit(self):
        for a, b in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(a=a, b=b):
                self.assertEqual(PlcOutputMapper.map(state(a, b), PlcConfig()), {"M200": int(a), "M201": int(b)})

    def test_legacy_system_bits_in_old_config_are_never_written(self):
        cfg = from_dict(PlcConfig, {"signal_mode": "dual_bit", "word_output_enabled": True,
                                    "mapping": {"device_person": "M100", "device_clear": "M101",
                                                "device_fault": "M102", "device_status_word": "D100"}})
        self.assertEqual(PlcOutputMapper.map(state(b=True), cfg), {"M200": 0, "M201": 1})

    def test_no_zone_drawn_is_an_error(self):
        with self.assertRaisesRegex(ValueError, NO_ZONE_MESSAGE):
            validate_rois([], PlcConfig(), camera_count=1)
        with self.assertRaisesRegex(ValueError, "ROI_001"):
            validate_rois([Roi("ROI_001", "ROI", points=LEFT)], PlcConfig(), camera_count=1)
        validate_rois([Roi("ROI_001", "ROI", points=LEFT, plc_device="M200")], PlcConfig(), camera_count=1)

    def test_stop_holds_and_fault_obeys_each_policy(self):
        cfg = PlcConfig()
        stopped = state(True)
        stopped.running = False
        self.assertTrue(all(v is HOLD for v in PlcOutputMapper.map(stopped, cfg).values()))
        for policy, expected in (("hold", HOLD), ("on", 1), ("off", 0)):
            cfg.failsafe.person_output_on_fault = policy
            out = PlcOutputMapper.map(state(True, fault=True), cfg)
            self.assertEqual(out, {"M200": expected, "M201": expected})

    def test_missing_result_holds_instead_of_claiming_clear(self):
        s = state()
        s.roi_occupied.pop("ROI_001")
        self.assertIs(PlcOutputMapper.map(s, PlcConfig())["M200"], HOLD)

    def test_reject_word_input_invalid_and_duplicate_bits(self):
        cfg = PlcConfig()
        cfg.mapping.device_heartbeat = "M110"
        for devices in ({"A": "D200"}, {"A": "X0"}, {"A": "bad"},
                        {"A": "M200", "B": "m 0200"}, {"A": "M110"}):
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                validate_roi_devices(devices, cfg)

    def test_check_required_bit(self):
        with self.assertRaisesRegex(ValueError, "ROI_001"):
            validate_roi_devices({"ROI_001": ""}, PlcConfig(), require_assigned=True)

    def test_y_address_uses_selected_plc_radix(self):
        cfg = PlcConfig()
        self.assertEqual(normalize_roi_device("y 010", cfg), "Y10")
        with self.assertRaises(ValueError):
            normalize_roi_device("Y18", cfg)
        cfg.connection.plc_series = "Q Series"
        self.assertEqual(normalize_roi_device("Y18", cfg), "Y18")

    def test_conflict_detected_before_any_driver_write(self):
        mgr = PlcManager(PlcConfig())
        mgr.connect()
        s = state()
        s.roi_devices["ROI_002"] = "M0200"
        with patch.object(mgr.driver, "write_bit") as write, self.assertRaises(PlcError):
            mgr.apply_state(s)
        write.assert_not_called()

    def test_deleted_or_remapped_bit_is_cleared_when_healthy(self):
        mgr = PlcManager(PlcConfig())
        mgr.connect()
        mgr.apply_state(state(True))
        s = state(True)
        s.roi_devices["ROI_001"] = "M202"
        mgr.apply_state(s)
        mem = mgr.memory_snapshot()
        self.assertEqual((mem["M200"], mem["M202"]), (0, 1))
        s.roi_devices.pop("ROI_001")
        mgr.apply_state(s)
        self.assertEqual(mgr.memory_snapshot()["M202"], 0)

    def test_retired_bit_holds_during_fault_and_stop_then_clears(self):
        for fault in (False, True):
            with self.subTest(fault=fault):
                mgr = PlcManager(PlcConfig())
                mgr.connect()
                mgr.apply_state(state(True))
                s = state(fault=fault)
                s.running = fault
                s.roi_devices.pop("ROI_001")
                mgr.apply_state(s)
                self.assertEqual(mgr.memory_snapshot()["M200"], 1)
                s.running, s.fault = True, False
                mgr.apply_state(s)
                self.assertEqual(mgr.memory_snapshot()["M200"], 0)

    def test_reconnect_resyncs_all_roi_bits(self):
        mgr = PlcManager(PlcConfig())
        mgr.connect()
        mgr.apply_state(state(True, True))
        mgr.disconnect()
        mgr.connect()
        self.assertEqual(dict(mgr.resync()), {"M200": 1, "M201": 1})

    def test_failed_retirement_is_retried_after_reconnect(self):
        mgr = PlcManager(PlcConfig())
        mgr.connect()
        mgr.apply_state(state(True))
        changed = state()
        changed.roi_devices.pop("ROI_001")
        original = mgr.driver.write_bit
        def fail_retired(device, value):
            if device == "M200":
                raise PlcError("connection lost")
            return original(device, value)
        with patch.object(mgr.driver, "write_bit", side_effect=fail_retired), self.assertRaises(PlcError):
            mgr.apply_state(changed)
        mgr.connect()
        mgr.resync()
        self.assertEqual(mgr.memory_snapshot()["M200"], 0)

    def test_cannot_assign_heartbeat_bit_even_with_leading_zeros(self):
        cfg = PlcConfig()
        cfg.mapping.device_heartbeat = "M0110"
        with self.assertRaises(ValueError):
            validate_roi_devices({"ROI_001": "M110"}, cfg)


class DetectionTests(unittest.TestCase):
    def test_debounce_is_independent_for_each_roi(self):
        tracker = OccupancyTracker(DebounceConfig(on_delay_ms=200, off_delay_ms=300, min_detection_frames=2))
        tracker.update({"A": 1, "B": 0}, 0.)
        tracker.update({"A": 1, "B": 0}, .21)
        self.assertEqual(tracker.roi_occupied(), {"A": True, "B": False})
        tracker.update({"A": 0, "B": 1}, .3)
        tracker.update({"A": 0, "B": 1}, .51)
        self.assertEqual(tracker.roi_occupied(), {"A": True, "B": True})
        tracker.update({"A": 0, "B": 1}, .61)
        self.assertEqual(tracker.roi_occupied(), {"A": False, "B": True})

    def test_pipeline_to_plc_with_overlapping_rois_and_multiple_cameras(self):
        mgr = RoiManager()
        a = mgr.create(RoiType.INCLUDE, LEFT, plc_device="M200", camera=0)
        b = mgr.create(RoiType.INCLUDE, LEFT, plc_device="M201", camera=0)
        c = mgr.create(RoiType.INCLUDE, RIGHT, plc_device="M202", camera=1)
        logic = LogicConfig(on_delay_ms=0, off_delay_ms=0, min_detection_frames=1)
        pipes = [ProcessingPipeline(mgr, logic, camera=i) for i in range(2)]
        frame = Frame(np.zeros((1000, 1000, 3), dtype=np.uint8), 0, 0.)
        occupied = {}
        for pipe in pipes:
            occupied.update(pipe.process(frame, [person(.25)], 1.).roi_occupied)
        s = PlcOutputState(running=True, roi_occupied=occupied,
                           roi_devices={r.id: r.plc_device for r in (a, b, c)})
        self.assertEqual(PlcOutputMapper.map(s, PlcConfig()), {"M200": 1, "M201": 1, "M202": 0})

    def test_disabled_roi_drops_out_of_detection(self):
        mgr = RoiManager()
        a = mgr.create(RoiType.INCLUDE, LEFT, plc_device="M200")
        mgr.create(RoiType.INCLUDE, RIGHT, plc_device="M201")
        mgr.set_enabled(a.id, False)
        result = RoiProcessor(ContainmentMode.FOOT_POINT).evaluate([person(.25)], 1000, 1000, mgr.all())
        self.assertNotIn(a.id, result.roi_counts)
        self.assertEqual(result.in_roi, 0)


if __name__ == "__main__":
    unittest.main()
