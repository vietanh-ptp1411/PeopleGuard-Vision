"""ROI tab: list of ROIs, per-ROI fields (name / PLC device / enabled) and editor actions."""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...logic.occupancy_state_machine import OccupancyState
from ...plc.device_address import is_valid_device
from ...roi.roi_model import Roi
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, COLOR_WARN
from .form_helpers import page_header


class RoiPanel(QWidget):
    add_include_requested = Signal()
    add_exclude_requested = Signal()
    finish_requested = Signal()
    cancel_requested = Signal()
    edit_mode_toggled = Signal(bool)
    delete_requested = Signal(str)
    clear_requested = Signal()
    save_requested = Signal()
    selection_changed = Signal(str)
    fields_changed = Signal(str, str, str, bool)   # id, name, plc_device, enabled

    COLS = ("ID", "Cam", "Name", "Type", "On", "PLC", "Pts", "State")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rois: List[Roi] = []
        self._selected = ""
        self._build()

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

        lay.addWidget(page_header("Zones", "Vẽ polygon: người trong vùng sẽ bật bit PLC"))
        self.lbl_target = QLabel("")
        self.lbl_target.setWordWrap(True)
        self.lbl_target.setStyleSheet(f"color: {COLOR_OK}; font-weight: 600;")
        self.lbl_target.hide()
        lay.addWidget(self.lbl_target)

        g_act = QGroupBox("ROI EDITOR")
        gl = QGridLayout(g_act)
        self.btn_add = QPushButton("+ Zone")
        self.btn_add.setProperty("class", "primary")
        self.btn_add_ex = QPushButton("+ Exclusion")
        self.btn_finish = QPushButton("Finish")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_edit = QPushButton("Edit")
        self.btn_edit.setCheckable(True)
        self.btn_edit.setToolTip("Kéo đỉnh để sửa vùng; Shift+click cạnh để thêm đỉnh")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setProperty("class", "dangerline")
        self.btn_clear = QPushButton("Clear all")
        self.btn_save = QPushButton("Save")
        self.btn_save.setProperty("class", "success")
        # Two tight rows beat one column of full width buttons: the same actions read as a
        # toolbar instead of a menu, and the ROI list stays above the fold.
        for b in (self.btn_add, self.btn_add_ex, self.btn_finish, self.btn_cancel,
                  self.btn_edit, self.btn_delete, self.btn_clear, self.btn_save):
            b.setProperty("size", "sm")
        gl.addWidget(self.btn_add, 0, 0)
        gl.addWidget(self.btn_add_ex, 0, 1)
        gl.addWidget(self.btn_finish, 0, 2)
        gl.addWidget(self.btn_cancel, 0, 3)
        gl.addWidget(self.btn_edit, 1, 0)
        gl.addWidget(self.btn_delete, 1, 1)
        gl.addWidget(self.btn_clear, 1, 2)
        gl.addWidget(self.btn_save, 1, 3)
        self.lbl_hint = QLabel("Click trái trên hình để thêm điểm, double-click / Enter để đóng polygon.")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")
        gl.addWidget(self.lbl_hint, 2, 0, 1, 4)
        lay.addWidget(g_act)

        g_list = QGroupBox("ROI LIST")
        ll = QVBoxLayout(g_list)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(len(self.COLS)):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch if col == 2 else QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(160)
        self.table.itemSelectionChanged.connect(self._table_selection)
        ll.addWidget(self.table)
        lay.addWidget(g_list)

        g_fields = QGroupBox("SELECTED ROI")
        f = QFormLayout(g_fields)
        self.lbl_id = QLabel("-")
        self.edt_name = QLineEdit()
        self.edt_plc = QLineEdit()
        self.edt_plc.setPlaceholderText("ví dụ M200 - không bắt buộc, chỉ cho vùng include")
        self.edt_plc.textChanged.connect(self._validate_plc)
        self.chk_enabled = QCheckBox("Enabled")
        self.btn_apply = QPushButton("Apply to ROI")
        f.addRow("ID", self.lbl_id)
        f.addRow("Name", self.edt_name)
        f.addRow("PLC Device", self.edt_plc)
        f.addRow(self.chk_enabled)
        f.addRow(self.btn_apply)
        lay.addWidget(g_fields)
        lay.addStretch(1)

        self.btn_add.clicked.connect(self.add_include_requested)
        self.btn_add_ex.clicked.connect(self.add_exclude_requested)
        self.btn_finish.clicked.connect(self.finish_requested)
        self.btn_cancel.clicked.connect(self.cancel_requested)
        self.btn_edit.toggled.connect(self.edit_mode_toggled)
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self._selected) if self._selected else None)
        self.btn_clear.clicked.connect(self.clear_requested)
        self.btn_save.clicked.connect(self.save_requested)
        self.btn_apply.clicked.connect(self._apply_fields)
        self.set_mode("none")

    # ------------------------------------------------------------------ external updates
    def set_rois(self, rois: List[Roi], states: Dict[str, OccupancyState] | None = None) -> None:
        self._rois = list(rois)
        states = states or {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(rois))
        for row, r in enumerate(rois):
            st = states.get(r.id)
            vals = [r.id, str(int(getattr(r, "camera", 0)) + 1), r.name, r.type.label,
                    "Y" if r.enabled else "N", r.plc_device or "-", str(len(r.points)),
                    (st.value if st else ("-" if r.is_include else "n/a"))]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if col in (1, 4, 6):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 7 and st is not None:
                    it.setForeground(QColor(COLOR_ERROR if st.occupied else COLOR_OK))
                if not r.enabled:
                    it.setForeground(QColor(COLOR_TEXT_MUTED))
                self.table.setItem(row, col, it)
            if r.id == self._selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        if self._selected and not any(r.id == self._selected for r in rois):
            self._selected = ""
        self._fill_fields()

    def update_states(self, states: Dict[str, OccupancyState]) -> None:
        for row in range(self.table.rowCount()):
            rid_item = self.table.item(row, 0)
            if rid_item is None:
                continue
            st = states.get(rid_item.text())
            it = self.table.item(row, 6)
            if it is not None and st is not None and it.text() != st.value:
                it.setText(st.value)
                it.setForeground(QColor(COLOR_ERROR if st.occupied else COLOR_OK))

    def select(self, roi_id: str) -> None:
        if roi_id == self._selected:
            return
        self._selected = roi_id
        self.table.blockSignals(True)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() == roi_id:
                self.table.selectRow(row)
                break
        self.table.blockSignals(False)
        self._fill_fields()

    def set_camera_context(self, label: str, several: bool) -> None:
        """Name the camera a new zone would be drawn on, when there is a choice."""
        self.lbl_target.setVisible(several)
        if several:
            self.lbl_target.setText(f"Vùng mới sẽ thuộc về: {label}  —  bấm vào ô hình của camera "
                                    f"khác để đổi")

    def set_mode(self, mode: str) -> None:
        drawing = mode == "drawing"
        self.btn_finish.setEnabled(drawing)
        self.btn_cancel.setEnabled(drawing)
        self.btn_add.setEnabled(not drawing)
        self.btn_add_ex.setEnabled(not drawing)
        if mode != "edit" and self.btn_edit.isChecked():
            self.btn_edit.blockSignals(True)
            self.btn_edit.setChecked(False)
            self.btn_edit.blockSignals(False)
        if drawing:
            self.lbl_hint.setText("ĐANG VẼ: click trái thêm điểm, click phải hoàn tác, double-click / Enter / 'Finish polygon' để đóng, Esc để huỷ.")
            self.lbl_hint.setStyleSheet(f"color: {COLOR_WARN}; font-size: 9pt;")
        elif mode == "edit":
            self.lbl_hint.setText("ĐANG SỬA: click để chọn vùng, kéo đỉnh để di chuyển, Shift+click cạnh để thêm đỉnh, click phải lên đỉnh để xoá, kéo bên trong để dời cả vùng.")
            self.lbl_hint.setStyleSheet(f"color: {COLOR_OK}; font-size: 9pt;")
        else:
            self.lbl_hint.setText("Click trái trên hình để thêm điểm, double-click / Enter để đóng polygon.")
            self.lbl_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")

    def set_dirty(self, dirty: bool) -> None:
        self.btn_save.setText("Save ROI *" if dirty else "Save ROI")

    # ------------------------------------------------------------------ internal
    def _table_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        rid = self.table.item(rows[0].row(), 0).text() if rows else ""
        if rid != self._selected:
            self._selected = rid
            self._fill_fields()
            self.selection_changed.emit(rid)

    def _current(self) -> Roi | None:
        for r in self._rois:
            if r.id == self._selected:
                return r
        return None

    def _fill_fields(self) -> None:
        r = self._current()
        enabled = r is not None
        for w in (self.edt_name, self.edt_plc, self.chk_enabled, self.btn_apply, self.btn_delete):
            w.setEnabled(enabled)
        if r is None:
            self.lbl_id.setText("-")
            self.edt_name.clear()
            self.edt_plc.clear()
            self.chk_enabled.setChecked(False)
            return
        self.lbl_id.setText(f"{r.id}  ({r.type.label})")
        self.edt_name.setText(r.name)
        self.edt_plc.setText(r.plc_device)
        self.edt_plc.setEnabled(r.is_include)
        self.chk_enabled.setChecked(r.enabled)

    def _validate_plc(self) -> None:
        txt = self.edt_plc.text().strip()
        ok = (not txt) or is_valid_device(txt)
        self.edt_plc.setStyleSheet("" if ok else f"border: 1px solid {COLOR_ERROR};")

    def _apply_fields(self) -> None:
        r = self._current()
        if r is None:
            return
        plc = self.edt_plc.text().strip().upper()
        if plc and not is_valid_device(plc):
            self.lbl_hint.setText(f"Địa chỉ PLC '{plc}' không hợp lệ (ví dụ: M200, D110)")
            self.lbl_hint.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 9pt;")
            return
        self.fields_changed.emit(r.id, self.edt_name.text().strip() or r.name, plc if r.is_include else "", self.chk_enabled.isChecked())
