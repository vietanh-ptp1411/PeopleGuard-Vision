"""Bounded asynchronous event storage; SQLite belongs to this worker only."""
from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from .event_repository import EventRecord, EventRepository

log = logging.getLogger("STORAGE")


class EventWorker(QThread):
    saved = Signal(object)
    history_ready = Signal(object, int, int)  # rows, total, requested before_id
    exported = Signal(bool, str)
    failed = Signal(str)
    cleared = Signal()
    recovered = Signal()

    def __init__(self, path, parent=None, capacity: int = 1024) -> None:
        super().__init__(parent)
        self.path = path
        self._queue = queue.Queue(maxsize=capacity)
        self._accepting = True
        self._lock = threading.Lock()
        self._last_error = ""
        self.high_water = 0
        self.rejected = 0

    def _error(self, message: str) -> None:
        if message != self._last_error:
            self._last_error = message
            log.error(message)
            self.failed.emit(message)

    @property
    def last_error(self) -> str:
        return self._last_error

    def _success(self) -> None:
        if self._last_error:
            self._last_error = ""
            self.recovered.emit()

    def _put(self, name, value=None) -> bool:
        with self._lock:
            if not self._accepting:
                return False
            try:
                self._queue.put_nowait((name, value))
                self.high_water = max(self.high_water, self._queue.qsize())
                return True
            except queue.Full:
                self.rejected += 1
        self._error("Hàng đợi lưu trữ đã đầy; không lưu được yêu cầu. Kiểm tra ổ đĩa và nhật ký.")
        return False

    def add(self, event_type, roi_id="", roi_name="", details="", snapshot_path="") -> bool:
        record = EventRecord(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                             event_type, roi_id, roi_name, details, snapshot_path)
        return self._put("add", record)

    def request_recent(self, before_id: int = 0) -> None:
        self._put("recent", max(0, int(before_id)))

    def request_export(self, path: str) -> None:
        self._put("export", path)

    def request_clear(self) -> None:
        self._put("clear")

    def set_path(self, path: str) -> None:
        self._put("path", path)

    def clear_snapshot_path(self, path: str) -> None:
        self._put("snapshot_failed", path)

    def prune(self, keep_days: int, max_rows: int = 0) -> int:
        self._put("prune", (keep_days, max_rows))
        return 0

    def stop_worker(self) -> None:
        with self._lock:
            self._accepting = False

    def run(self) -> None:
        repository = EventRepository(self.path)
        pending = None
        failed_snapshots = deque(maxlen=1024)
        try:
            if not repository.available:
                self._error("Không mở được cơ sở dữ liệu sự kiện: " + str(self.path))
            while self._accepting or pending is not None or not self._queue.empty():
                try:
                    name, value = pending or self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                pending = None
                try:
                    if name == "add":
                        batch = [value]
                        while len(batch) < 32:
                            try:
                                next_command = self._queue.get_nowait()
                            except queue.Empty:
                                break
                            if next_command[0] != "add":
                                pending = next_command
                                break
                            batch.append(next_command[1])
                        for record in batch:
                            if record.snapshot_path in failed_snapshots:
                                record.snapshot_path = ""
                        saved = repository.add_batch(batch)
                        if len(saved) != len(batch):
                            self._error(f"Không lưu được {len(batch)} sự kiện. Kiểm tra ổ đĩa/SQLite.")
                        else:
                            self._success()
                            for record in saved:
                                self.saved.emit(record)
                    elif name == "recent":
                        self.history_ready.emit(repository.recent(500, value), repository.count(), value)
                    elif name == "export":
                        self.exported.emit(repository.export_csv(value, limit=-1), value)
                    elif name == "clear":
                        if repository.clear():
                            self.cleared.emit()
                        else:
                            self._error("Không xóa được lịch sử. Kiểm tra ổ đĩa/SQLite.")
                    elif name == "prune":
                        repository.prune(*value)
                    elif name == "snapshot_failed":
                        failed_snapshots.append(value)
                        repository.clear_snapshot_path(value)
                    elif name == "path":
                        replacement = EventRepository(value)
                        if replacement.available:
                            repository.close()
                            repository = replacement
                            self.path = value
                            self._success()
                            self.history_ready.emit(repository.recent(500), repository.count(), 0)
                        else:
                            self._error("Không mở được cơ sở dữ liệu mới: " + str(value))
                except Exception as exc:
                    self._error(f"Lỗi lưu trữ: {exc}")
        finally:
            repository.close()
