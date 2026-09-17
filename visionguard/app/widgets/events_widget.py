"""Event history tab (SQLite backed)."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from copy import deepcopy

from ...config.schemas import ClipConfig
from ...storage.event_repository import EventRecord
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_WARN
from .form_helpers import hint, page_header


class EventsWidget(QWidget):
    clip_config_changed = Signal(object)
    open_clips_requested = Signal()
    refresh_requested = Signal()
    export_requested = Signal(str)
    clear_requested = Signal()

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
        self.btn_clear.setProperty("class", "dangerline")
        for b in (self.btn_refresh, self.btn_export, self.btn_clear):
            b.setProperty("size", "sm")
            head.addWidget(b)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(self._build_clips())
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

    def _build_clips(self) -> QGroupBox:
        """Record the stretch of video where someone was in a zone, one file per camera.

        Two columns rather than a column of six rows: stacked, this box alone pushed the
        window's minimum height past a 1080p screen.
        """
        self._clip = ClipConfig()
        box = QGroupBox("VIDEO CLIPS")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.chk_clip = QCheckBox("Ghi video khi có người")
        self.chk_clip.setToolTip("Mỗi camera một file riêng, xếp theo ngày và tên camera")
        grid.addWidget(self.chk_clip, 0, 0, 1, 4)

        self.spn_pre = QDoubleSpinBox()
        self.spn_pre.setRange(0.0, 15.0)
        self.spn_pre.setSingleStep(0.5)
        self.spn_pre.setSuffix(" s")
        self.spn_pre.setToolTip("Giữ lại bấy nhiêu giây TRƯỚC lúc phát hiện, để thấy người bước vào")
        self.spn_post = QDoubleSpinBox()
        self.spn_post.setRange(0.0, 30.0)
        self.spn_post.setSingleStep(0.5)
        self.spn_post.setSuffix(" s")
        self.spn_post.setToolTip("Quay thêm bấy nhiêu giây sau khi vùng đã trống")
        self.spn_keep = QSpinBox()
        self.spn_keep.setRange(0, 365)
        self.spn_keep.setSuffix(" ngày")
        self.spn_keep.setToolTip("Tự xoá file cũ hơn mốc này. 0 = giữ mãi")
        self.spn_scale = QDoubleSpinBox()
        self.spn_scale.setRange(0.25, 1.0)
        self.spn_scale.setSingleStep(0.25)
        self.spn_scale.setDecimals(2)
        self.spn_scale.setToolTip("1.00 = cỡ gốc. 0.50 giảm một nửa: file nhẹ và tốn ít RAM đệm hơn")

        for col, (text, field) in enumerate(((("Trước"), self.spn_pre), ("Sau", self.spn_post))):
            grid.addWidget(QLabel(text), 1, col * 2)
            grid.addWidget(field, 1, col * 2 + 1)
        for col, (text, field) in enumerate((("Giữ", self.spn_keep), ("Cỡ hình", self.spn_scale))):
            grid.addWidget(QLabel(text), 2, col * 2)
            grid.addWidget(field, 2, col * 2 + 1)

        self.btn_clips = QPushButton("Mở thư mục video")
        self.btn_clips.setProperty("size", "sm")
        grid.addWidget(self.btn_clips, 3, 0, 1, 4)

        note = hint("'Trước' giữ lại đoạn ngay trước lúc phát hiện — không có nó thì cảnh "
                    "người bước vào luôn bị mất.")
        note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        grid.addWidget(note, 4, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.chk_clip.toggled.connect(self._emit_clip)
        for w in (self.spn_pre, self.spn_post, self.spn_scale):
            w.editingFinished.connect(self._emit_clip)
        self.spn_keep.editingFinished.connect(self._emit_clip)
        self.btn_clips.clicked.connect(self.open_clips_requested)
        return box

    def set_clip_config(self, cfg: ClipConfig) -> None:
        self._clip = deepcopy(cfg)
        for w, v in ((self.chk_clip, cfg.enabled), (self.spn_pre, cfg.pre_roll_s),
                     (self.spn_post, cfg.post_roll_s), (self.spn_keep, cfg.retention_days),
                     (self.spn_scale, cfg.scale)):
            w.blockSignals(True)
            w.setChecked(bool(v)) if isinstance(w, QCheckBox) else w.setValue(v)
            w.blockSignals(False)

    def get_clip_config(self) -> ClipConfig:
        cfg = deepcopy(self._clip)
        cfg.enabled = self.chk_clip.isChecked()
        cfg.pre_roll_s = self.spn_pre.value()
        cfg.post_roll_s = self.spn_post.value()
        cfg.retention_days = self.spn_keep.value()
        cfg.scale = self.spn_scale.value()
        return cfg

    def _emit_clip(self) -> None:
        self._clip = self.get_clip_config()
        self.clip_config_changed.emit(deepcopy(self._clip))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Xuất file CSV", "events/events.csv", "CSV (*.csv)")
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
