"""Model intent ordering and occupancy delivery across a busy UI."""
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from visionguard.app.controllers.system_controller import SystemController
from visionguard.camera.base_camera import Frame
from visionguard.config.config_manager import ConfigManager
from visionguard.config.schemas import DetectionMode, DetectorConfig, LogicConfig
from visionguard.logic.pipeline import ProcessingPipeline
from visionguard.roi.roi_manager import RoiManager
from visionguard.roi.roi_model import RoiType
from visionguard.storage.event_repository import EventType
from visionguard.vision.detection import BBox, Detection
from visionguard.workers.frame_buffer import LatestFrameBuffer
from visionguard.workers.inference_worker import InferenceWorker


class ModelIntentTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = ProcessingPipeline(RoiManager(), LogicConfig())
        self.calls = []
        self.loaded = None
        detector = SimpleNamespace(load=self.load, unload=self.unload)
        self.worker = InferenceWorker(detector, LatestFrameBuffer(), self.pipeline)

    def tearDown(self):
        self.pipeline.close()

    def load(self, config):
        self.calls.append(config.model_path)
        self.loaded = config.model_path

    def unload(self):
        self.loaded = None

    def test_load_unload_load_before_dispatch_keeps_latest_model(self):
        self.worker.request_load_model(DetectorConfig(model_path="first.pt"))
        self.worker.request_unload()
        self.worker.request_load_model(DetectorConfig(model_path="latest.pt"))
        self.worker._drain()
        self.assertEqual(self.calls, ["latest.pt"])
        self.assertEqual(self.loaded, "latest.pt")

    def test_load_unload_same_load_before_dispatch_keeps_model(self):
        config = DetectorConfig(model_path="same.pt")
        self.worker.request_load_model(config)
        self.worker.request_unload()
        self.worker.request_load_model(config)
        self.worker._drain()
        self.assertEqual(self.calls, ["same.pt"])
        self.assertEqual(self.loaded, "same.pt")

    def test_latest_unload_cancels_all_pending_loads(self):
        for path in ("first.pt", "second.pt"):
            self.worker.request_load_model(DetectorConfig(model_path=path))
            self.worker.request_unload()
        self.worker._drain()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.loaded)

    def test_unload_requested_during_load_releases_model(self):
        def load_then_cancel(config):
            self.load(config)
            self.worker.request_unload()
        self.worker._detector.load = load_then_cancel
        self.worker.request_load_model(DetectorConfig(model_path="cancelled.pt"))
        self.worker._drain()
        self.assertEqual(self.calls, ["cancelled.pt"])
        self.assertIsNone(self.loaded)


class ResultDeliveryTests(unittest.TestCase):
    def test_pending_enter_transition_is_delivered_before_clear(self):
        manager = RoiManager()
        roi = manager.create(RoiType.INCLUDE, [(0, 0), (1, 0), (1, 1), (0, 1)])
        pipeline = ProcessingPipeline(manager, LogicConfig(on_delay_ms=0, off_delay_ms=0,
                                                           min_detection_frames=1))
        person = Detection(BBox(2, 2, 8, 8), 0.9, 0, "person")
        detections = [person]
        detector = SimpleNamespace(is_loaded=lambda: True, detect=lambda image: detections,
                                   unload=lambda: None)
        buffer = LatestFrameBuffer()
        worker = InferenceWorker(detector, buffer, pipeline)
        results = []
        worker.result_ready.connect(results.append)
        try:
            buffer.put(Frame(np.zeros((16, 16, 3), np.uint8), 1, time.time()))
            worker._step()
            self.assertEqual(len(results), 1)
            self.assertTrue(any(t.roi_id == roi.id and t.became_occupied
                                for t in results[0].transitions))
            detections = []
            for frame_id in range(2, 5):
                buffer.put(Frame(np.zeros((16, 16, 3), np.uint8), frame_id, time.time()))
                worker._step()
            self.assertEqual(len(results), 1)
            self.assertTrue(pipeline.area_occupied)
            worker.result_delivered(0, results[0].source_token)
            worker._step()
            self.assertEqual(len(results), 2)
            self.assertEqual(results[1].frame.frame_id, 4)
            self.assertTrue(any(t.roi_id == roi.id and t.became_clear
                                for t in results[1].transitions))
        finally:
            pipeline.close()


class ControllerModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cm = ConfigManager(Path(self.temp.name) / "config")
        settings = cm.settings
        settings.camera.detection_mode = "pc_yolo"
        settings.camera.camera_type = "video"
        settings.camera.count = 1
        settings.ai.detector.auto_load_on_start = False
        settings.app.events_db_path = str(Path(self.temp.name) / "events.db")
        settings.app.snapshot.enabled = settings.app.clip.enabled = settings.app.autostart = False
        settings.plc.simulation_mode = True
        cm.save_all()
        # Dispatch inference commands manually to reproduce a mode switch before the
        # busy inference thread can handle the earlier unload. Other workers stay idle.
        for target in (
            "visionguard.workers.inference_worker.InferenceWorker.start",
            "visionguard.workers.camera_worker.CameraWorker.request_start",
            "visionguard.workers.camera_event_worker.CameraEventWorker.request_connect",
            "visionguard.app.controllers.system_controller.housekeeping.run_in_background",
        ):
            mock = patch(target)
            mock.start()
            self.addCleanup(mock.stop)
        self.ctrl = SystemController(cm)
        self.addCleanup(self.close_controller)
        self.loaded = True
        self.loads = 0

        def load(config):
            self.loaded = True
            self.loads += 1
            return SimpleNamespace(summary=lambda: "test model")

        def unload():
            self.loaded = False

        self.ctrl.inference_worker._detector = SimpleNamespace(
            load=load, unload=unload, is_loaded=lambda: self.loaded)
        self.ctrl._model_loaded = True
        self.ctrl.start_system()
        self.dispatch()

    def close_controller(self):
        self.ctrl.shutdown()
        self.ctrl.deleteLater()
        self.app.processEvents()

    def dispatch(self):
        worker = self.ctrl.inference_worker
        for _ in range(10):
            if worker._commands.empty():
                break
            worker._drain()
        self.assertTrue(worker._commands.empty(), "model intents failed to settle")

    def test_fast_pc_ai_pc_switch_restarts_detection(self):
        self.ctrl.set_detection_mode(DetectionMode.AI_CAMERA)
        self.assertFalse(self.ctrl._model_loaded)
        self.ctrl.set_detection_mode(DetectionMode.PC_YOLO)
        self.dispatch()
        self.assertEqual(self.loads, 1)
        self.assertTrue(self.loaded)
        self.assertTrue(self.ctrl.running)
        self.assertTrue(self.ctrl._model_loaded)
        self.assertTrue(self.ctrl._detecting)
        self.assertFalse(self.ctrl._start_detection_when_loaded)

    def test_delayed_unload_notification_recovers_running_pc_mode(self):
        # Simulates the queued model_unloaded signal arriving after PC mode resumes.
        self.loaded = False
        self.ctrl._on_model_unloaded()
        self.dispatch()
        self.assertEqual(self.loads, 1)
        self.assertTrue(self.loaded)
        self.assertTrue(self.ctrl._detecting)

    def test_result_started_before_restart_is_not_accepted_after_restart(self):
        worker = self.ctrl.inference_worker
        entered, release = threading.Event(), threading.Event()

        def delayed_detect(image):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test detector timed out")
            return []

        worker._detector.detect = delayed_detect
        received = []
        worker.result_ready.connect(received.append)
        self.ctrl.buffer.put(Frame(np.zeros((16, 16, 3), np.uint8), 1, time.time()))
        inference = threading.Thread(target=worker._step)
        inference.start()
        try:
            self.assertTrue(entered.wait(1))
            self.ctrl.stop_system()
            self.ctrl.start_system()
            restart = self.ctrl._start_time
        finally:
            release.set()
            inference.join(3)
        self.assertFalse(inference.is_alive())
        self.app.processEvents()
        self.assertEqual(len(received), 1)
        self.assertLessEqual(received[0].inference_started_at, restart)
        self.assertGreaterEqual(received[0].produced_at, restart)
        self.assertNotEqual(received[0].detection_generation, worker.requested_generation)
        self.assertEqual(self.ctrl._results, {})
        self.assertEqual(self.ctrl._result_times, {})
        self.assertEqual(worker._pending_results, {})
        self.dispatch()

    def test_first_transition_in_same_clock_tick_as_start_is_accepted(self):
        worker = self.ctrl.inference_worker
        self.ctrl.add_roi("include", [(0, 0), (1, 0), (1, 1), (0, 1)])
        roi = self.ctrl.roi_manager.all()[0]
        self.ctrl.update_roi_fields(roi.id, "entry", "M200", True)
        self.ctrl.pipeline.set_logic(LogicConfig(on_delay_ms=0, off_delay_ms=0,
                                                min_detection_frames=1))
        person = Detection(BBox(2, 2, 8, 8), 0.9, 0, "person")
        worker._detector.detect = lambda image: [person]
        worker.set_max_fps(0)
        fixed_tick = time.monotonic()
        with patch("visionguard.workers.inference_worker.time.monotonic", return_value=fixed_tick), \
                patch.object(self.ctrl, "_log_event") as log_event:
            self.ctrl.stop_system()
            self.ctrl.start_system()
            self.dispatch()
            self.ctrl.buffer.put(Frame(np.zeros((16, 16, 3), np.uint8), 1, time.time()))
            worker._step()
            result = self.ctrl._results[0]
            self.assertEqual(result.inference_started_at, self.ctrl._start_time)
            self.assertEqual(result.detection_generation, worker.requested_generation)
            self.assertTrue(result.area_occupied)
            self.assertTrue(any(call.args[0] == EventType.PERSON_ENTERED
                                for call in log_event.call_args_list))


if __name__ == "__main__":
    unittest.main()
