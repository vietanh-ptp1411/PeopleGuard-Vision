"""Event history tab (SQLite backed)."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...storage.event_repository import EventRecord
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_WARN


class EventsWidget(QWidget):
    refresh_requested = Signal()
    export_requested = Signal(str)
    clear_requested = Signal()

    COLS = ("Time", "Event", "ROI", "Name", "Details", "Snapshot")
    MAX_ROWS = 500

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("EVENT HISTORY")
        title.setProperty("class", "title")
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        head.addWidget(self.lbl_count)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_export = QPushButton("Export CSV")
        self.btn_clear = QPushButton("Clear history")
        self.btn_clear.setProperty("class", "danger")
        head.addWidget(self.btn_refresh)
        head.addWidget(self.btn_export)
        head.addWidget(self.btn_clear)
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

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export events", "events/events.csv", "CSV (*.csv)")
        if path:
            self.export_requested.emit(path)

    def set_events(self, records: List[EventRecord], total: int | None = None) -> None:
        self.table.setRowCount(0)
        for rec in records[: self.MAX_ROWS]:
            self._append_row(rec)
        self.lbl_count.setText(f"{total if total is not None else len(records)} event(s)")
        self.table.resizeColumnsToContents()

    def add_event(self, rec: EventRecord) -> None:
        self.table.insertRow(0)
        self._fill_row(0, rec)
        while self.table.rowCount() > self.MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)
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
            if col == 1:
                if "OCCUPIED" in v or "ENTERED" in v:
                    it.setForeground(QColor(COLOR_ERROR))
                elif "CLEAR" in v or "LEFT" in v:
                    it.setForeground(QColor(COLOR_OK))
                elif "FAULT" in v:
                    it.setForeground(QColor(COLOR_WARN))
            self.table.setItem(row, col, it)
