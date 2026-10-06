"""Regression tests for stale cameras, bounded memory and slow storage (no hardware)."""
import gc
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from visionguard.app.controllers.system_controller import SystemController
from visionguard.app.widgets.video_view import VideoView
from visionguard.camera.base_camera import Frame
from visionguard.config.config_manager import ConfigManager
from visionguard.config.schemas import (ClipConfig, DetectorConfig, LogicConfig, SnapshotConfig,
                                        VisualizationConfig)
from visionguard.logic.pipeline import ProcessingPipeline
from visionguard.roi.roi_manager import RoiManager
from visionguard.storage.clip_recorder import ClipRecorder
from visionguard.storage.event_repository import EventRepository
from visionguard.storage.event_worker import EventWorker
from visionguard.storage.snapshot_saver import SnapshotSaver
from visionguard.workers.camera_event_worker import EventChannelState
from visionguard.workers.camera_worker import CameraState
from visionguard.workers.frame_buffer import LatestFrameBuffer
from visionguard.workers.inference_worker import InferenceWorker


class QtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def until(self, predicate, timeout=3):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertTrue(predicate(), "condition timed out")


class LifecycleTests(QtTest):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cm = ConfigManager(Path(self.temp.name) / "config")
        s = self.cm.settings
        s.camera.detection_mode = "pc_yolo"
        s.camera.camera_type = "video"
        s.camera.count = 2
        s.ai.detector.auto_load_on_start = False
        s.app.events_db_path = str(Path(self.temp.name) / "events.db")
        s.app.snapshot.enabled = s.app.clip.enabled = s.app.autostart = False
        s.plc.simulation_mode = True
        self.cm.save_all()
        with patch("visionguard.app.controllers.system_controller.housekeeping.run_in_background"):
            self.ctrl = SystemController(self.cm)
        self.ctrl.add_roi("include", [(0., 0.), (1., 0.), (1., 1.), (0., 1.)])
        roi = self.ctrl.roi_manager.all()[0]
        self.ctrl.update_roi_fields(roi.id, "entry", "M200", True)

    def tearDown(self):
        self.ctrl.shutdown()
        self.ctrl.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def healthy(self):
        c = self.ctrl
        c._system_running = True
        c._start_time = time.monotonic() - 20
        c._camera_states = {0: CameraState.STREAMING, 1: CameraState.STREAMING}
        c._camera_state = CameraState.STREAMING
        c._plc_connected = c._model_loaded = c._detecting = True
        c._result_times = {0: time.monotonic(), 1: time.monotonic()}

    def test_one_fresh_camera_cannot_mask_stale_or_missing_camera(self):
        self.healthy()
        c = self.ctrl
        c._result_times[0] -= 99
        c._recompute()
        self.assertEqual(c._system, "FAULT")
        self.assertTrue(c._last_output.fault)
        self.assertIn("camera 1", c._fault_reason)
        c._result_times[0] = time.monotonic()
        c._recompute()
        self.assertEqual(c._system, "RUNNING")
        del c._result_times[1]
        c._recompute()
        self.assertEqual(c._system, "FAULT")

    def test_secondary_event_channel_loss_faults_group(self):
        self.healthy()
        c = self.ctrl
        c.settings.camera.detection_mode = "ai_camera"
        c._event_channel = EventChannelState.ONLINE
        c._channel_states = {0: EventChannelState.ONLINE, 1: EventChannelState.DISCONNECTED}
        c._recompute()
        self.assertEqual(c._system, "FAULT")
        c._channel_states[1] = EventChannelState.ONLINE
        c._recompute()
        self.assertEqual(c._system, "RUNNING")

    def test_repeated_group_changes_release_listeners_and_workers(self):
        c = self.ctrl
        refs = []
        for _ in range(20):
            refs.append(weakref.ref(c.pipelines[1]))
            c.settings.camera.count = 1
            c._build_camera_group()
            c.settings.camera.count = 2
            c._build_camera_group()
        c.settings.camera.count = 1
        c._build_camera_group()
        self.until(lambda: len(c.inference_worker._sources) == 1)
        self.until(lambda: all(not w.isRunning() for w in c._retired_workers))
        self.until(lambda: (c._reap_workers() or not c._retired_workers))
        gc.collect()
        self.assertEqual(len(c.roi_manager._listeners), 2)  # controller and primary pipeline
        self.assertTrue(all(ref() is None for ref in refs))
        self.assertFalse(c._retired_workers)


class InferenceTests(QtTest):
    def test_unload_then_same_load_during_loading_keeps_latest_request(self):
        pipeline = ProcessingPipeline(RoiManager(), LogicConfig())
        calls = []
        loaded = [False]
        def load(config):
            calls.append(config)
            if len(calls) == 1:
                worker.request_unload()
                worker.request_load_model(config)
            loaded[0] = True
            return None
        detector = SimpleNamespace(load=load, unload=lambda: loaded.__setitem__(0, False))
        worker = InferenceWorker(detector, LatestFrameBuffer(), pipeline)
        worker.request_load_model(DetectorConfig())
        worker._drain()
        self.assertEqual(len(calls), 2)
        self.assertTrue(loaded[0])
        pipeline.close()

    def test_results_are_bounded_without_losing_transition_delivery(self):
        manager = RoiManager()
        pipeline = ProcessingPipeline(manager, LogicConfig())
        detector = SimpleNamespace(is_loaded=lambda: True, detect=lambda image: [], unload=lambda: None)
        buffer = LatestFrameBuffer()
        worker = InferenceWorker(detector, buffer, pipeline)
        received = []
        worker.result_ready.connect(received.append)
        try:
            for i in range(10):
                buffer.put(Frame(np.zeros((16, 16, 3), np.uint8), i, time.time()))
                worker._step()
            self.assertEqual(len(received), 1)
            worker.result_delivered(0, received[0].source_token)
            worker._step()
            self.assertEqual(len(received), 2)
            self.assertEqual(received[-1].frame.frame_id, 9)
        finally:
            pipeline.close()

    def test_duplicate_load_requests_collapse_and_unload_cancels_queued_load(self):
        pipeline = ProcessingPipeline(RoiManager(), LogicConfig())
        calls = []
        detector = SimpleNamespace(load=lambda cfg: calls.append(cfg), unload=lambda: None)
        worker = InferenceWorker(detector, LatestFrameBuffer(), pipeline)
        for _ in range(25):
            worker.request_load_model(DetectorConfig())
        self.assertEqual(worker._commands.qsize(), 1)
        worker.request_unload()
        worker._drain()
        self.assertEqual(calls, [])
        pipeline.close()


class StorageTests(QtTest):
    def test_snapshot_rejects_before_copy_and_releases_budget(self):
        entered, release = threading.Event(), threading.Event()
        saver = SnapshotSaver(SnapshotConfig(max_pending_mb=1, max_pending_jobs=2))
        def blocked(*args):
            entered.set()
            release.wait(3)
            return True
        saver._write = blocked
        try:
            image = np.zeros((256, 512, 3), np.uint8)
            first = saver.save(image, "R1")
            self.assertTrue(entered.wait(1))
            second = saver.save(image, "R1")
            self.assertNotEqual(first, second)
            self.assertIsNone(saver.save(image, "R1"))
            self.assertLessEqual(saver._pending_bytes, 1024 ** 2)
        finally:
            release.set()
            self.assertTrue(saver.shutdown())
        self.assertEqual(saver._pending_bytes, 0)

    def test_event_queue_is_nonblocking_and_flushes_before_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release = threading.Event(), threading.Event()
            original = EventRepository.add_batch
            def slow(repo, records):
                entered.set()
                release.wait(3)
                return original(repo, records)
            worker = EventWorker(Path(folder) / "events.db", capacity=3)
            with patch.object(EventRepository, "add_batch", slow):
                worker.start()
                worker.add("PERSON_ENTERED")
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                accepted = [worker.add("PERSON_LEFT") for _ in range(4)]
                self.assertLess(time.monotonic() - start, 0.2)
                self.assertEqual(accepted, [True, True, True, False])
                worker.stop_worker()
                release.set()
                self.assertTrue(worker.wait(3000))
            repo = EventRepository(Path(folder) / "events.db")
            self.assertEqual(repo.count(), 4)
            repo.close()

    def test_failed_snapshot_before_insert_does_not_leave_dead_path(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = EventWorker(Path(folder) / "events.db")
            worker.clear_snapshot_path("failed.jpg")
            worker.add("PERSON_ENTERED", snapshot_path="failed.jpg")
            worker.start()
            worker.stop_worker()
            self.assertTrue(worker.wait(3000))
            repo = EventRepository(Path(folder) / "events.db")
            self.assertEqual(repo.recent()[0].snapshot_path, "")
            repo.close()

    def test_clip_submit_does_not_resize_on_caller_and_bounds_memory(self):
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            cfg = ClipConfig(enabled=True, directory=folder, max_buffer_mb=2, scale=0.5)
            recorder = ClipRecorder(0, "test", cfg)
            original = recorder._prepare
            caller = threading.get_ident()
            threads = []
            def blocked(image):
                threads.append(threading.get_ident())
                entered.set()
                release.wait(3)
                return original(image)
            recorder._prepare = blocked
            try:
                image = np.zeros((360, 640, 3), np.uint8)
                stamp = time.monotonic()
                recorder.submit(image, stamp)
                self.assertTrue(entered.wait(1))
                for i in range(1, 20):
                    recorder.submit(image, stamp + i)
                self.assertLessEqual(recorder.buffer_bytes, 2 * 1024 ** 2)
                self.assertLessEqual(recorder.high_water_bytes, 2 * 1024 ** 2)
                self.assertTrue(recorder.dropped)
                self.assertNotIn(caller, threads)
            finally:
                release.set()
                self.assertTrue(recorder.stop())
            self.assertEqual(recorder.buffer_bytes, 0)

    def test_hidden_video_does_not_convert_frames(self):
        view = VideoView(VisualizationConfig())
        frame = Frame(np.zeros((100, 100, 3), np.uint8), 1, time.time())
        with patch("visionguard.app.widgets.video_view.bgr_to_qimage") as convert:
            for _ in range(10):
                view.set_frame(frame)
            convert.assert_not_called()
            self.assertTrue(view.has_image)
        view.close()
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
