"""Real media output, reconfiguration and bounded History/preview regressions."""
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from visionguard.app.widgets.events_widget import EventsWidget
from visionguard.app.widgets.video_view import VideoView
from visionguard.camera.base_camera import Frame
from visionguard.config.schemas import ClipConfig, SnapshotConfig, VisualizationConfig
from visionguard.storage.clip_recorder import ClipRecorder
from visionguard.storage.event_repository import EventRecord, EventRepository
from visionguard.storage.event_worker import EventWorker
from visionguard.storage.snapshot_saver import SnapshotSaver
from visionguard.utils.qt_image import bgr_to_qimage


class MediaHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def until(self, predicate, timeout=3):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            QTest.qWait(2)
        self.assertTrue(predicate(), "condition timed out")

    @staticmethod
    def record(event_id):
        return EventRecord(event_id, "2026-10-06 12:00:00.000", "PERSON_ENTERED",
                           "entry", "Gate", f"event-{event_id}", "")

    def track_frames(self, recorder):
        done = threading.Event()
        original = recorder._tick

        def tracked(image, stamp):
            try:
                return original(image, stamp)
            finally:
                if image is not None:
                    done.set()

        recorder._tick = tracked
        return done

    def submit_and_wait(self, recorder, image, stamp, done):
        done.clear()
        previous_sample = recorder._next_sample
        recorder.submit(image, stamp)
        if recorder._next_sample != previous_sample:
            self.assertTrue(done.wait(2), "recorder did not process the accepted frame")

    def test_encoded_clip_duration_tracks_source_time_at_different_fps(self):
        for source_fps in (5, 15, 30):
            for output_fps in (0, 12):
                with self.subTest(source_fps=source_fps, output_fps=output_fps):
                    with tempfile.TemporaryDirectory() as folder:
                        config = ClipConfig(enabled=True, directory=folder, scale=1,
                                            fps=output_fps, capture_fps=15)
                        recorder = ClipRecorder(0, "gate", config)
                        done = self.track_frames(recorder)
                        recorder.set_occupied(True, "entry")
                        image = np.zeros((64, 96, 3), np.uint8)
                        start = time.monotonic()
                        try:
                            # Simulated monotonic source timestamps avoid a wall-clock
                            # sleep while exercising the real sampler, encoder and file.
                            for index in range(2 * source_fps + 1):
                                self.submit_and_wait(recorder, image,
                                                     start + index / source_fps, done)
                        finally:
                            self.assertTrue(recorder.stop())
                        paths = list(Path(folder).rglob("*.mp4"))
                        self.assertEqual(len(paths), 1)
                        capture = cv2.VideoCapture(str(paths[0]))
                        try:
                            self.assertTrue(capture.isOpened())
                            fps = capture.get(cv2.CAP_PROP_FPS)
                            decoded = 0
                            while capture.read()[0]:
                                decoded += 1
                            self.assertGreater(decoded, 0)
                            self.assertAlmostEqual(decoded / fps, 2.0, delta=0.22)
                        finally:
                            capture.release()

    def test_clip_reconfigure_releases_preroll_without_new_frames(self):
        config = ClipConfig(enabled=True, max_buffer_mb=8, scale=0.5, pre_roll_s=10)
        recorder = ClipRecorder(0, "gate", config)
        done = self.track_frames(recorder)
        image = np.zeros((360, 640, 3), np.uint8)
        start = time.monotonic()
        try:
            for index in range(12):
                self.submit_and_wait(recorder, image, start + index * 0.1, done)
            self.assertGreater(recorder.buffer_bytes, 1024 ** 2)
            recorder.set_config(replace(config, max_buffer_mb=1))
            self.until(lambda: recorder.buffer_bytes <= 1024 ** 2)
            recorder.set_config(replace(config, enabled=False, max_buffer_mb=1))
            self.until(lambda: recorder.buffer_bytes == 0)
        finally:
            self.assertTrue(recorder.stop())

    def test_clip_resolution_change_creates_readable_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ClipConfig(enabled=True, directory=folder, scale=1)
            recorder = ClipRecorder(0, "gate", config)
            done = self.track_frames(recorder)
            recorder.set_occupied(True)
            start = time.monotonic()
            try:
                for index, shape in enumerate(((64, 96, 3), (96, 128, 3))):
                    self.submit_and_wait(recorder, np.zeros(shape, np.uint8),
                                         start + index * 0.1, done)
            finally:
                self.assertTrue(recorder.stop())
            paths = list(Path(folder).rglob("*.mp4"))
            self.assertEqual(len(paths), 2)
            shapes = []
            for path in paths:
                capture = cv2.VideoCapture(str(path))
                try:
                    ok, frame = capture.read()
                    self.assertTrue(ok)
                    shapes.append(frame.shape)
                finally:
                    capture.release()
            self.assertEqual(sorted(shapes), [(64, 96, 3), (96, 128, 3)])

    def test_clip_inflight_resize_keeps_original_memory_reservation(self):
        config = ClipConfig(enabled=True, max_buffer_mb=1, scale=0.25)
        recorder = ClipRecorder(0, "gate", config)
        entered, release = threading.Event(), threading.Event()
        sizes = []
        original = recorder._prepare

        def blocked(image):
            entered.set()
            release.wait(3)
            prepared = original(image)
            sizes.append(prepared.shape)
            return prepared

        recorder._prepare = blocked
        try:
            image = np.zeros((360, 640, 3), np.uint8)
            recorder.submit(image)
            self.assertTrue(entered.wait(1))
            recorder.set_config(replace(config, scale=1.0))
            release.set()
            self.until(lambda: recorder.buffer_bytes == 0)
            self.assertEqual(sizes, [(90, 160, 3)])
            self.assertLessEqual(recorder.high_water_bytes, 1024 ** 2)
            recorder.submit(image)
            self.assertEqual(recorder.buffer_bytes, 0)
            self.assertEqual(recorder.dropped, 1)
        finally:
            release.set()
            self.assertTrue(recorder.stop())

    def test_clip_disable_finalizes_open_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ClipConfig(enabled=True, directory=folder, scale=1)
            recorder = ClipRecorder(0, "gate", config)
            done = self.track_frames(recorder)
            recorder.set_occupied(True)
            try:
                self.submit_and_wait(recorder, np.zeros((64, 96, 3), np.uint8),
                                     time.monotonic(), done)
                self.assertIsNotNone(recorder._writer)
                recorder.set_config(replace(config, enabled=False))
                self.until(lambda: recorder._writer is None and recorder.buffer_bytes == 0)
                self.assertTrue(list(Path(folder).rglob("*.mp4")))
            finally:
                self.assertTrue(recorder.stop())

    def test_snapshot_encode_failure_notifies_and_releases_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            saver = SnapshotSaver(SnapshotConfig(directory=folder))
            failed, notified = [], threading.Event()

            def failure(path):
                failed.append(path)
                notified.set()

            saver.on_failure = failure
            try:
                with patch("visionguard.storage.snapshot_saver.cv2.imencode", return_value=(False, None)):
                    path = saver.save(np.zeros((32, 32, 3), np.uint8), "entry")
                    self.assertTrue(notified.wait(2))
                self.assertEqual(failed, [str(path)])
                self.assertFalse(path.exists())
                self.assertEqual(saver._pending_bytes, 0)
                self.assertEqual(saver._pending_jobs, 0)
            finally:
                self.assertTrue(saver.shutdown())

    def test_snapshot_filesystem_failure_notifies_and_releases_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / "not-a-directory"
            blocked.write_text("occupied", encoding="utf-8")
            saver = SnapshotSaver(SnapshotConfig(directory=str(blocked)))
            failed = threading.Event()
            saver.on_failure = lambda _: failed.set()
            try:
                self.assertIsNotNone(saver.save(np.zeros((32, 32, 3), np.uint8), "entry"))
                self.assertTrue(failed.wait(2))
                self.assertEqual(saver._pending_bytes, 0)
                self.assertEqual(saver._pending_jobs, 0)
            finally:
                self.assertTrue(saver.shutdown())

    def test_hidden_preview_keeps_only_latest_frame_and_converts_on_show(self):
        view = VideoView(VisualizationConfig())
        try:
            with patch("visionguard.app.widgets.video_view.bgr_to_qimage", wraps=bgr_to_qimage) as convert:
                for index in range(10):
                    image = np.full((64, 96, 3), index, np.uint8)
                    view.set_frame(Frame(image, index, time.time()))
                self.assertIs(view._pending_image, image)
                convert.assert_not_called()
                view.show()
                self.app.processEvents()
                self.assertEqual(convert.call_count, 1)
                self.assertIsNone(view._pending_image)
                self.assertEqual(view._image.pixelColor(0, 0).red(), 9)
                view.hide()
                self.assertFalse(view._timer.isActive())
        finally:
            view.close()
            view.deleteLater()
            self.app.processEvents()

    def test_history_live_insert_does_not_skip_displaced_row(self):
        widget = EventsWidget()
        try:
            widget.show()
            self.app.processEvents()
            widget.set_events([self.record(i) for i in range(1000, 500, -1)], 1000)
            widget.add_event(self.record(1001))
            requested = []
            widget.older_requested.connect(requested.append)
            widget.btn_older.click()
            self.assertEqual(requested, [502])
            self.assertEqual(widget.table.rowCount(), 500)
            self.assertEqual(widget.table.item(499, 0).data(Qt.ItemDataRole.UserRole), 502)
        finally:
            widget.close()
            widget.deleteLater()

    def test_history_live_growth_enables_paging_and_older_page_stays_stable(self):
        widget = EventsWidget()
        try:
            widget.show()
            self.app.processEvents()
            widget.set_events([self.record(i) for i in range(499, 0, -1)], 499)
            self.assertFalse(widget.btn_older.isEnabled())
            widget.add_event(self.record(500))
            widget.add_event(self.record(501))
            self.assertTrue(widget.btn_older.isEnabled())
            self.assertEqual(widget._oldest_id, 2)
            widget.set_events([self.record(1)], 501, before_id=2)
            widget.add_event(self.record(502))
            self.assertEqual(widget.table.rowCount(), 1)
            self.assertEqual(widget.table.item(0, 4).text(), "event-1")
        finally:
            widget.close()
            widget.deleteLater()

    def test_history_worker_pages_without_gaps_during_new_event_inserts(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "events.db"
            repository = EventRepository(db)
            repository.add_batch([self.record(i) for i in range(1, 1202)])
            repository.close()
            worker = EventWorker(db)
            pages = []
            worker.history_ready.connect(lambda rows, total, before: pages.append((rows, total, before)))
            worker.start()
            try:
                worker.request_recent()
                self.until(lambda: len(pages) == 1)
                worker.add("PERSON_LEFT")
                worker.request_recent(pages[-1][0][-1].id)
                self.until(lambda: len(pages) == 2)
                worker.request_recent(pages[-1][0][-1].id)
                self.until(lambda: len(pages) == 3)
                self.assertEqual([len(page[0]) for page in pages], [500, 500, 201])
                ids = [record.id for page in pages for record in page[0]]
                self.assertEqual(ids, list(range(1201, 0, -1)))
                self.assertEqual(pages[-1][1], 1202)
            finally:
                worker.stop_worker()
                self.assertTrue(worker.wait(3000))


if __name__ == "__main__":
    unittest.main()
