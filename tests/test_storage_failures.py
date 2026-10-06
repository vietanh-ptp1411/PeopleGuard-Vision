"""Storage errors must be reported without killing the background writer."""
import tempfile
from pathlib import Path
import unittest

from PySide6.QtCore import Qt

from visionguard.storage.event_repository import EventRepository
from visionguard.storage.event_worker import EventWorker


class StorageFailureTests(unittest.TestCase):
    def test_invalid_parent_reports_error_and_worker_can_switch_to_valid_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            parent_file = root / "file-not-folder"
            parent_file.write_text("occupied", encoding="utf-8")
            bad = parent_file / "events.db"
            repo = EventRepository(bad)
            self.assertFalse(repo.available)
            self.assertFalse(repo.clear())
            repo.close()
            errors, recovered = [], []
            worker = EventWorker(bad)
            worker.failed.connect(errors.append, Qt.ConnectionType.DirectConnection)
            worker.recovered.connect(lambda: recovered.append(True), Qt.ConnectionType.DirectConnection)
            good = root / "valid.db"
            worker.set_path(str(good))
            worker.add("PERSON_ENTERED")
            worker.start()
            worker.stop_worker()
            self.assertTrue(worker.wait(3000))
            self.assertTrue(errors)
            self.assertTrue(recovered)
            repo = EventRepository(good)
            self.assertEqual(repo.count(), 1)
            repo.close()

    def test_failed_clear_does_not_emit_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root / "directory.db"
            bad.mkdir()
            worker = EventWorker(bad)
            success = []
            worker.cleared.connect(lambda: success.append(True), Qt.ConnectionType.DirectConnection)
            worker.request_clear()
            worker.start()
            worker.stop_worker()
            self.assertTrue(worker.wait(3000))
            self.assertFalse(success)
            self.assertTrue(worker.last_error)


if __name__ == "__main__":
    unittest.main()
