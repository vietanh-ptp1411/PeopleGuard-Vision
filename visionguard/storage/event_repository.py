"""Event history in SQLite (events/events.db) with CSV export."""
from __future__ import annotations

import csv
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("STORAGE")


class EventType:
    PERSON_ENTERED = "PERSON_ENTERED"
    PERSON_LEFT = "PERSON_LEFT"
    AREA_OCCUPIED = "AREA_OCCUPIED"
    AREA_CLEAR = "AREA_CLEAR"
    FAULT = "FAULT"
    FAULT_CLEARED = "FAULT_CLEARED"
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    CAMERA = "CAMERA"
    PLC = "PLC"
    AI = "AI"


@dataclass
class EventRecord:
    id: int
    timestamp: str
    event_type: str
    roi_id: str
    roi_name: str
    details: str
    snapshot_path: str


class EventRepository:
    """Thread-safe (single connection guarded by a lock). Never raises to callers."""

    def __init__(self, db_path: Path | str = "events/events.db") -> None:
        self.path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       timestamp TEXT NOT NULL,
                       event_type TEXT NOT NULL,
                       roi_id TEXT DEFAULT '',
                       roi_name TEXT DEFAULT '',
                       details TEXT DEFAULT '',
                       snapshot_path TEXT DEFAULT ''
                   )"""
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp)")
            self._conn.commit()
        except sqlite3.Error as exc:
            log.error("Cannot open event database %s: %s", self.path, exc)
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    def add(self, event_type: str, roi_id: str = "", roi_name: str = "", details: str = "", snapshot_path: str = "") -> Optional[EventRecord]:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if self._conn is None:
            return None
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO events(timestamp, event_type, roi_id, roi_name, details, snapshot_path) VALUES (?,?,?,?,?,?)",
                    (ts, event_type, roi_id, roi_name, details, snapshot_path),
                )
                self._conn.commit()
                return EventRecord(cur.lastrowid or 0, ts, event_type, roi_id, roi_name, details, snapshot_path)
        except sqlite3.Error as exc:
            log.error("Cannot store event: %s", exc)
            return None

    def recent(self, limit: int = 200) -> List[EventRecord]:
        if self._conn is None:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id, timestamp, event_type, roi_id, roi_name, details, snapshot_path FROM events ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            return [EventRecord(*r) for r in rows]
        except sqlite3.Error as exc:
            log.error("Cannot read events: %s", exc)
            return []

    def count(self) -> int:
        if self._conn is None:
            return 0
        try:
            with self._lock:
                return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        except sqlite3.Error:
            return 0

    def prune(self, keep_days: int, max_rows: int = 0) -> int:
        """Drop rows older than keep_days, then anything past max_rows. Returns rows gone."""
        if not self.available:
            return 0
        removed = 0
        with self._lock:
            try:
                if keep_days and keep_days > 0:
                    cur = self._conn.execute(
                        "DELETE FROM events WHERE timestamp < datetime('now', 'localtime', ?)",
                        (f"-{int(keep_days)} days",))
                    removed += cur.rowcount or 0
                if max_rows and max_rows > 0:
                    cur = self._conn.execute(
                        "DELETE FROM events WHERE id NOT IN "
                        "(SELECT id FROM events ORDER BY id DESC LIMIT ?)", (int(max_rows),))
                    removed += cur.rowcount or 0
                self._conn.commit()
                if removed:
                    self._conn.execute("VACUUM")     # give the space back to the disk
            except Exception as exc:
                log.error("Cannot prune the event history: %s", exc)
                return 0
        return removed

    def clear(self) -> None:
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute("DELETE FROM events")
                self._conn.commit()
        except sqlite3.Error as exc:
            log.error("Cannot clear events: %s", exc)

    def export_csv(self, path: Path | str, limit: int = 100000) -> bool:
        records = self.recent(limit)
        try:
            with Path(path).open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["id", "timestamp", "event_type", "roi_id", "roi_name", "details", "snapshot_path"])
                for r in reversed(records):
                    w.writerow([r.id, r.timestamp, r.event_type, r.roi_id, r.roi_name, r.details, r.snapshot_path])
            return True
        except OSError as exc:
            log.error("CSV export failed: %s", exc)
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
