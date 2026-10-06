"""Storage tab: where everything this system writes goes, at what quality, and for how long.

Snapshots, clips, the event database and the logs all land on disk. On a machine that runs
unattended for months the question that decides whether this system survives is not whether
those files get written - it is whether they will fill the disk. Fill it and the snapshots
stop, the event history stops, and the pre-flight check refuses to start: the evidence trail
dies quietly at the exact moment somebody needs it.

So the three retention rules live on one page, next to the free space of the drive they are
about to consume. A retention setting nobody can find is a retention setting nobody sets.
"""
from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

from ...config.schemas import AppConfig
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM
from .form_helpers import AdvancedSection, advanced_checkbox, hint, page_header

#: Below this the system is one busy day away from writing nothing at all. The pre-flight
#: check fails at 5 GB, so warn before it does rather than after.
LOW_DISK_GB = 10.0


def _free_gb(folder: str) -> float | None:
    """Free space on the drive holding `folder`, walking up to the nearest parent that exists.

    A folder that has not been created yet is the normal case here - somebody is typing a
    path into the box - and it still sits on a drive whose free space we can report.
    """
    path = Path(folder or ".").absolute()
    for candidate in (path, *path.parents):
        try:
            return shutil.disk_usage(candidate).free / 1e9
        except OSError:
            continue
    return None


class StorageConfigWidget(QWidget):
    config_applied = Signal(object)        # AppConfig
    open_folder_requested = Signal(str)    # folder the user asked to see

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = deepcopy(config)
        self.advanced = AdvancedSection()
        self._build()
        self.set_config(config)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setSpacing(8)

        self.chk_advanced = advanced_checkbox(self.advanced)
        lay.addWidget(page_header("Lưu trữ", "Lưu vào đâu, chất lượng nào, giữ bao nhiêu ngày",
                                  self.chk_advanced))
        lay.addWidget(self._build_where())
        lay.addWidget(self._build_snapshot())
        lay.addWidget(self._build_clip())
        lay.addWidget(self._build_retention())
        lay.addStretch(1)

    def _folder_row(self, form: QFormLayout, label: str, tip: str) -> QLineEdit:
        """A path box with a Browse button and an Open button, as one form row."""
        edit = QLineEdit()
        edit.setToolTip(tip)
        browse = QPushButton("Chọn…")
        browse.setProperty("size", "sm")
        browse.clicked.connect(lambda: self._pick_folder(edit))
        show = QPushButton("Mở")
        show.setProperty("size", "sm")
        show.clicked.connect(lambda: self.open_folder_requested.emit(edit.text().strip()))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit, 1)
        h.addWidget(browse)
        h.addWidget(show)
        form.addRow(label, row)
        edit.editingFinished.connect(self._apply)
        return edit

    def _build_where(self) -> QGroupBox:
        box = QGroupBox("NƠI LƯU")
        form = QFormLayout(box)
        self.edt_snap_dir = self._folder_row(
            form, "Ảnh sự kiện", "Ảnh chụp lúc phát hiện người, xếp theo ngày")
        self.edt_clip_dir = self._folder_row(
            form, "Video clip", "Mỗi camera một file, xếp theo ngày và tên camera")
        self.edt_log_dir = self._folder_row(form, "Nhật ký", "File log của phần mềm")
        self.edt_db = QLineEdit()
        self.edt_db.setToolTip("File SQLite chứa lịch sử sự kiện")
        self.edt_db.editingFinished.connect(self._apply)
        form.addRow("Sổ sự kiện", self.edt_db)
        self.advanced.rows(form, self.edt_log_dir.parentWidget(), self.edt_db)

        self.lbl_free = QLabel("")
        self.lbl_free.setWordWrap(True)
        form.addRow("", self.lbl_free)
        form.addRow("", hint("Nên để ở ổ KHÁC ổ cài phần mềm. Đĩa đầy thì ảnh, video và cả "
                             "sổ sự kiện đều ngừng ghi — mất bằng chứng đúng lúc cần nhất."))
        return box

    def _build_snapshot(self) -> QGroupBox:
        box = QGroupBox("ẢNH SỰ KIỆN")
        form = QFormLayout(box)
        self.chk_snap = QCheckBox("Chụp ảnh khi có người")
        self.chk_snap.setToolTip("Một ảnh cho mỗi lần có người vào vùng, không phải mỗi khung hình")
        self.chk_snap.toggled.connect(self._apply)
        form.addRow("", self.chk_snap)

        self.spn_quality = QSpinBox()
        self.spn_quality.setRange(40, 100)
        self.spn_quality.setSuffix(" %")
        self.spn_quality.setToolTip("Chất lượng nén JPEG. 90 nét nhưng nặng; 80 nhẹ hơn ~40% "
                                    "mà mắt thường khó phân biệt")
        self.spn_quality.editingFinished.connect(self._apply)
        form.addRow("Chất lượng", self.spn_quality)

        self.spn_snap_days = QSpinBox()
        self.spn_snap_days.setRange(0, 3650)
        self.spn_snap_days.setSuffix(" ngày")
        self.spn_snap_days.setToolTip("Tự xoá ảnh cũ hơn mốc này. 0 = giữ mãi")
        self.spn_snap_days.editingFinished.connect(self._apply)
        form.addRow("Xoá sau", self.spn_snap_days)
        form.addRow("", hint("Một ảnh cho mỗi lần vào vùng, không phải mỗi khung hình."))
        return box

    def _build_clip(self) -> QGroupBox:
        box = QGroupBox("VIDEO CLIP")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.chk_clip = QCheckBox("Quay video khi có người")
        self.chk_clip.setToolTip("Mỗi camera một file riêng, xếp theo ngày và tên camera")
        self.chk_clip.toggled.connect(self._apply)
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
        self.spn_scale = QDoubleSpinBox()
        self.spn_scale.setRange(0.25, 1.0)
        self.spn_scale.setSingleStep(0.25)
        self.spn_scale.setDecimals(2)
        self.spn_scale.setToolTip("1.00 = cỡ gốc. 0.50 giảm một nửa: file nhẹ và tốn ít RAM đệm hơn")
        self.spn_clip_days = QSpinBox()
        self.spn_clip_days.setRange(0, 365)
        self.spn_clip_days.setSuffix(" ngày")
        self.spn_clip_days.setToolTip("Tự xoá clip cũ hơn mốc này. 0 = giữ mãi")
        self.spn_cap = QDoubleSpinBox()
        self.spn_cap.setRange(0.0, 10000.0)
        self.spn_cap.setSingleStep(5.0)
        self.spn_cap.setSuffix(" GB")
        self.spn_cap.setToolTip("Trần cứng cho cả thư mục video, xoá file cũ nhất khi chạm trần. "
                                "0 = không giới hạn")
        self.spn_max_dur = QDoubleSpinBox()
        self.spn_max_dur.setRange(5.0, 3600.0)
        self.spn_max_dur.setSingleStep(10.0)
        self.spn_max_dur.setSuffix(" s")
        self.spn_max_dur.setToolTip("Một người đứng yên trong vùng cả ca không được phép "
                                    "thành một file dài vô tận")
        self.spn_clip_fps = QDoubleSpinBox()
        self.spn_clip_fps.setRange(1, 60)
        self.spn_clip_fps.setSuffix(" fps")
        self.spn_clip_width = QSpinBox()
        self.spn_clip_width.setRange(160, 3840)
        self.spn_clip_width.setSingleStep(160)
        self.spn_clip_width.setSuffix(" px")
        self.spn_clip_memory = QSpinBox()
        self.spn_clip_memory.setRange(8, 256)
        self.spn_clip_memory.setSuffix(" MiB")
        self.spn_clip_memory.setToolTip("Trần bộ đệm mỗi camera; cả nhóm tối đa 256 MiB. "
                                      "Đoạn trước sự kiện có thể ngắn hơn khi chạm trần.")

        # One pair per row, not two. Two label-field pairs side by side is four columns,
        # which needed more width than the panel has - the same mistake that put a
        # horizontal scroll bar under the PLC tab.
        pairs = (("Trước", self.spn_pre), ("Sau", self.spn_post),
                 ("Cỡ hình", self.spn_scale), ("Xoá sau", self.spn_clip_days),
                 ("Tối đa", self.spn_cap), ("Dài nhất", self.spn_max_dur),
                 ("FPS ghi", self.spn_clip_fps), ("Rộng tối đa", self.spn_clip_width),
                 ("Bộ đệm / camera", self.spn_clip_memory))
        for i, (text, field) in enumerate(pairs):
            grid.addWidget(QLabel(text), 1 + i, 0)
            grid.addWidget(field, 1 + i, 1)
            field.editingFinished.connect(self._apply)
        grid.setColumnStretch(1, 1)

        self.lbl_clip_cost = QLabel("")
        self.lbl_clip_cost.setWordWrap(True)
        self.lbl_clip_cost.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")
        grid.addWidget(self.lbl_clip_cost, 1 + len(pairs), 0, 1, 2)
        grid.addWidget(hint("'Trước' giữ lại đoạn ngay trước lúc phát hiện — không có nó thì "
                            "cảnh người bước vào luôn bị mất."), 2 + len(pairs), 0, 1, 2)
        return box

    def _build_retention(self) -> QGroupBox:
        box = QGroupBox("LỊCH SỬ & NHẬT KÝ")
        form = QFormLayout(box)
        self.spn_event_days = QSpinBox()
        self.spn_event_days.setRange(0, 3650)
        self.spn_event_days.setSuffix(" ngày")
        self.spn_event_days.setToolTip("Xoá dòng sự kiện cũ hơn mốc này khỏi sổ. 0 = giữ mãi")
        self.spn_log_days = QSpinBox()
        self.spn_log_days.setRange(0, 3650)
        self.spn_log_days.setSuffix(" ngày")
        self.spn_max_rows = QSpinBox()
        self.spn_max_rows.setRange(0, 10_000_000)
        self.spn_max_rows.setSingleStep(10000)
        self.spn_max_rows.setGroupSeparatorShown(True)
        self.spn_max_rows.setToolTip("Trần cứng số dòng, bất kể tuổi. 0 = không giới hạn")
        self.spn_interval = QDoubleSpinBox()
        self.spn_interval.setRange(0.25, 168.0)
        self.spn_interval.setSingleStep(1.0)
        self.spn_interval.setSuffix(" giờ")
        self.spn_interval.setToolTip("Bao lâu chạy dọn dẹp một lần khi phần mềm đang mở")

        form.addRow("Sự kiện", self.spn_event_days)
        form.addRow("Nhật ký", self.spn_log_days)
        form.addRow("Trần số dòng", self.spn_max_rows)
        form.addRow("Dọn mỗi", self.spn_interval)
        for field in (self.spn_event_days, self.spn_log_days, self.spn_max_rows, self.spn_interval):
            field.editingFinished.connect(self._apply)
        self.advanced.rows(form, self.spn_max_rows, self.spn_interval)
        form.addRow("", hint("Dọn dẹp chạy nền theo chu kỳ trên, và một lần ngay khi đổi cài đặt."))
        return box

    # ------------------------------------------------------------------ helpers
    def _pick_folder(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or "."
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu", start)
        if folder:
            edit.setText(folder)
            self._apply()

    def _refresh_estimates(self) -> None:
        free = _free_gb(self.edt_snap_dir.text().strip() or ".")
        if free is None:
            self.lbl_free.setText("Không đọc được dung lượng ổ đĩa")
            self.lbl_free.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 9pt;")
        else:
            low = free < LOW_DISK_GB
            self.lbl_free.setText(f"Còn trống {free:.1f} GB" + (" — sắp đầy" if low else ""))
            self.lbl_free.setStyleSheet(
                f"color: {COLOR_ERROR if low else COLOR_OK}; font-size: 9pt;")
        if self.chk_clip.isChecked():
            window = self.spn_pre.value() + self.spn_post.value()
            self.lbl_clip_cost.setText(
                f"Mỗi lần có người quay khoảng {window:.1f}s, cắt ở {self.spn_max_dur.value():.0f}s.")
        else:
            self.lbl_clip_cost.setText("Đang tắt — chỉ có ảnh tĩnh làm bằng chứng.")

    # ------------------------------------------------------------------ config <-> widgets
    def set_config(self, cfg: AppConfig) -> None:
        self._config = deepcopy(cfg)
        widgets = (
            (self.edt_snap_dir, cfg.snapshot.directory), (self.edt_clip_dir, cfg.clip.directory),
            (self.edt_log_dir, cfg.retention.log_dir), (self.edt_db, cfg.events_db_path),
            (self.chk_snap, cfg.snapshot.enabled), (self.spn_quality, cfg.snapshot.jpeg_quality),
            (self.spn_snap_days, cfg.retention.snapshot_days),
            (self.chk_clip, cfg.clip.enabled), (self.spn_pre, cfg.clip.pre_roll_s),
            (self.spn_post, cfg.clip.post_roll_s), (self.spn_scale, cfg.clip.scale),
            (self.spn_clip_days, cfg.clip.retention_days), (self.spn_cap, cfg.clip.max_total_gb),
            (self.spn_max_dur, cfg.clip.max_duration_s),
            (self.spn_clip_fps, cfg.clip.fps or cfg.clip.capture_fps),
            (self.spn_clip_width, cfg.clip.max_width), (self.spn_clip_memory, cfg.clip.max_buffer_mb),
            (self.spn_event_days, cfg.retention.event_days), (self.spn_log_days, cfg.retention.log_days),
            (self.spn_max_rows, cfg.retention.event_max_rows),
            (self.spn_interval, cfg.retention.interval_hours),
        )
        for widget, value in widgets:
            widget.blockSignals(True)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            else:
                widget.setValue(value)
            widget.blockSignals(False)
        self._refresh_estimates()
        self.advanced.apply()

    def get_config(self) -> AppConfig:
        cfg = deepcopy(self._config)
        cfg.events_db_path = self.edt_db.text().strip() or "events/events.db"
        snap = cfg.snapshot
        snap.directory = self.edt_snap_dir.text().strip() or "events"
        snap.enabled = self.chk_snap.isChecked()
        snap.jpeg_quality = self.spn_quality.value()
        clip = cfg.clip
        clip.directory = self.edt_clip_dir.text().strip() or "events/clips"
        clip.enabled = self.chk_clip.isChecked()
        clip.pre_roll_s = self.spn_pre.value()
        clip.post_roll_s = self.spn_post.value()
        clip.scale = self.spn_scale.value()
        clip.retention_days = self.spn_clip_days.value()
        clip.max_total_gb = self.spn_cap.value()
        clip.max_duration_s = self.spn_max_dur.value()
        clip.fps = self.spn_clip_fps.value()
        clip.capture_fps = self.spn_clip_fps.value()
        clip.max_width = self.spn_clip_width.value()
        clip.max_buffer_mb = self.spn_clip_memory.value()
        ret = cfg.retention
        ret.log_dir = self.edt_log_dir.text().strip() or "logs"
        ret.snapshot_days = self.spn_snap_days.value()
        ret.event_days = self.spn_event_days.value()
        ret.log_days = self.spn_log_days.value()
        ret.event_max_rows = self.spn_max_rows.value()
        ret.interval_hours = self.spn_interval.value()
        return cfg

    def _apply(self) -> None:
        self._config = self.get_config()
        self._refresh_estimates()
        self.config_applied.emit(deepcopy(self._config))
