"""Event history tab (SQLite backed)."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...storage.event_repository import EventRecord
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_WARN
from .form_helpers import page_header


class EventsWidget(QWidget):
    refresh_requested = Signal()
    export_requested = Signal(str)
    clear_requested = Signal()
    older_requested = Signal(int)

    COLS = ("Time", "Event", "ROI", "Name", "Details", "Snapshot")
    MAX_ROWS = 500

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        lay.addWidget(page_header("History", "Nhật ký sự kiện lưu trong SQLite", self.lbl_count))

        head = QHBoxLayout()
        head.setSpacing(6)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_export = QPushButton("Export CSV")
        self.btn_clear = QPushButton("Clear history")
        self.btn_older = QPushButton("Cũ hơn")
        self._oldest_id = 0
        self._before_id = 0
        self.btn_clear.setProperty("class", "dangerline")
        for b in (self.btn_refresh, self.btn_older, self.btn_export, self.btn_clear):
            b.setProperty("size", "sm")
            head.addWidget(b)
        head.addStretch(1)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)

        self.btn_refresh.clicked.connect(self.refresh_requested)
        self.btn_export.clicked.connect(self._export)
        self.btn_clear.clicked.connect(self.clear_requested)
        self.btn_older.clicked.connect(lambda: self.older_requested.emit(self._oldest_id))
        self.btn_older.setEnabled(False)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Xuất file CSV", "events/events.csv", "CSV (*.csv)")
        if path:
            self.export_requested.emit(path)

    def set_events(self, records: List[EventRecord], total: int | None = None, before_id: int = 0) -> None:
        self._before_id = before_id
        visible_records = records[: self.MAX_ROWS]
        self._oldest_id = visible_records[-1].id if visible_records else 0
        self.btn_older.setEnabled(len(records) >= self.MAX_ROWS)
        self.table.setRowCount(0)
        for rec in visible_records:
            self._append_row(rec)
        self.lbl_count.setText(f"{total if total is not None else len(records)} event(s)")
        self.table.resizeColumnsToContents()

    def add_event(self, rec: EventRecord) -> None:
        if self._before_id or not self.isVisible():
            return
        self.table.insertRow(0)
        self._fill_row(0, rec)
        while self.table.rowCount() > self.MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)
        # Live inserts displace the oldest visible event. Page from the new last row
        # so the displaced event remains available when the user clicks Older.
        self._oldest_id = self.table.item(self.table.rowCount() - 1, 0).data(Qt.ItemDataRole.UserRole)
        self.btn_older.setEnabled(self.table.rowCount() >= self.MAX_ROWS)
        try:
            n = int(self.lbl_count.text().split()[0]) + 1
        except (ValueError, IndexError):
            n = self.table.rowCount()
        self.lbl_count.setText(f"{n} event(s)")

    def _append_row(self, rec: EventRecord) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._fill_row(row, rec)

    def _fill_row(self, row: int, rec: EventRecord) -> None:
        vals = (rec.timestamp[11:] if len(rec.timestamp) > 11 else rec.timestamp, rec.event_type, rec.roi_id, rec.roi_name,
                rec.details, rec.snapshot_path)
        for col, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            if col == 0:
                it.setData(Qt.ItemDataRole.UserRole, rec.id)
            if col == 1:
                if "OCCUPIED" in v or "ENTERED" in v:
                    it.setForeground(QColor(COLOR_ERROR))
                elif "CLEAR" in v or "LEFT" in v:
                    it.setForeground(QColor(COLOR_OK))
                elif "FAULT" in v:
                    it.setForeground(QColor(COLOR_WARN))
            self.table.setItem(row, col, it)
