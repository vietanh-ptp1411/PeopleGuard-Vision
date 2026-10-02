"""One watched ROI, one stable ID and one independently configured PLC bit."""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...logic.occupancy_state_machine import OccupancyState
from ...config.schemas import PlcConfig
from ...plc.roi_mapping import normalize_roi_device, validate_roi_devices
from ...roi.roi_model import Roi
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, COLOR_WARN
from .form_helpers import page_header

COL_ID, COL_CAM, COL_NAME, COL_ON, COL_PLC, COL_PTS, COL_STATE = range(7)


class RoiPanel(QWidget):
    add_include_requested = Signal()     # a new watched ROI
    finish_requested = Signal()
    cancel_requested = Signal()
    edit_mode_toggled = Signal(bool)
    delete_requested = Signal(str)
    clear_requested = Signal()
    save_requested = Signal()
    selection_changed = Signal(str)
    fields_changed = Signal(str, str, str, bool, int)   # id, name, plc_device, enabled, camera
    target_camera_changed = Signal(int)                      # draw the next zone on this camera

    COLS = ("ID", "Cam", "Name", "On", "PLC bit", "Pts", "State")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rois: List[Roi] = []
        self._selected = ""
        self._plc_config = PlcConfig()
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

        lay.addWidget(page_header("Zones", "Mỗi ROI có ID và bit PLC riêng: có người = ON, hết người = OFF"))
        # Which camera the next zone goes on. This used to be a sentence telling you to
        # click another camera's tile - on a page where the picture is zoomed to one camera
        # and there is no other tile to click. Getting to camera 2 meant Overview, un-zoom,
        # click camera 2, back to Zones. Now it is a box on this page.
        self.row_target = QWidget()
        tl = QHBoxLayout(self.row_target)
        tl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Vẽ trên camera:")
        lbl.setStyleSheet(f"color: {COLOR_OK}; font-weight: 600;")
        self.cmb_target = QComboBox()
        self.cmb_target.setToolTip("Camera sẽ được phóng to và vùng mới sẽ thuộc về nó")
        self.cmb_target.currentIndexChanged.connect(self._on_target_changed)
        tl.addWidget(lbl)
        tl.addWidget(self.cmb_target, 1)
        self.row_target.hide()
        lay.addWidget(self.row_target)

        g_act = QGroupBox("ROI EDITOR")
        gl = QGridLayout(g_act)
        self.btn_add = QPushButton("+ ROI")
        self.btn_add.setProperty("class", "primary")
        self.btn_add.setToolTip("Vẽ ROI, sau đó chọn vùng và gán bit PLC riêng")
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
        # One creation action, followed by drawing and list controls.
        for b in (self.btn_add, self.btn_finish, self.btn_cancel,
                  self.btn_edit, self.btn_delete, self.btn_clear, self.btn_save):
            b.setProperty("size", "sm")
        gl.addWidget(self.btn_add, 0, 0, 1, 3)
        gl.addWidget(self.btn_finish, 1, 0)
        gl.addWidget(self.btn_cancel, 1, 1)
        gl.addWidget(self.btn_edit, 1, 2)
        gl.addWidget(self.btn_delete, 2, 0)
        gl.addWidget(self.btn_clear, 2, 1)
        gl.addWidget(self.btn_save, 2, 2)
        self.lbl_hint = QLabel("Click trái trên hình để thêm điểm, double-click / Enter để đóng polygon.")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")
        gl.addWidget(self.lbl_hint, 3, 0, 1, 3)
        lay.addWidget(g_act)

        g_list = QGroupBox("ROI LIST")
        ll = QVBoxLayout(g_list)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(len(self.COLS)):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch if col == COL_NAME else QHeaderView.ResizeMode.ResizeToContents)
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
        self.edt_plc.setPlaceholderText("Ví dụ M200 — bit riêng, không trùng ROI khác")
        self.edt_plc.textChanged.connect(self._validate_plc)
        self.chk_enabled = QCheckBox("Enabled")
        self.cmb_camera = QComboBox()
        self.cmb_camera.setToolTip("Chuyển vùng này sang camera khác mà không phải vẽ lại")
        self.btn_apply = QPushButton("Apply to ROI")
        f.addRow("ID", self.lbl_id)
        f.addRow("Name", self.edt_name)
        f.addRow("Camera", self.cmb_camera)
        f.addRow("PLC bit", self.edt_plc)
        f.addRow(self.chk_enabled)
        f.addRow(self.btn_apply)
        lay.addWidget(g_fields)
        lay.addStretch(1)

        self.btn_add.clicked.connect(self.add_include_requested)
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
        new_ids = [r.id for r in rois if r.id not in {old.id for old in self._rois}]
        if new_ids:
            self._selected = new_ids[-1]
        self._rois = list(rois)
        states = states or {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(rois))
        for row, r in enumerate(rois):
            st = states.get(r.id)
            vals = [r.id, str(int(getattr(r, "camera", 0)) + 1), r.name,
                    "Y" if r.enabled else "N", r.plc_device or ("Chưa gán" if r.is_include else "-"), str(len(r.points)),
                    (st.value if st else ("-" if r.is_include else "n/a"))]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if col in (COL_CAM, COL_ON, COL_PTS):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == COL_STATE and st is not None:
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
        if new_ids:
            self.selection_changed.emit(self._selected)

    def update_states(self, states: Dict[str, OccupancyState]) -> None:
        for row in range(self.table.rowCount()):
            rid_item = self.table.item(row, COL_ID)
            if rid_item is None:
                continue
            st = states.get(rid_item.text())
            it = self.table.item(row, COL_STATE)
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
            if self.table.item(row, COL_ID).text() == roi_id:
                self.table.selectRow(row)
                break
        self.table.blockSignals(False)
        self._fill_fields()

    def set_cameras(self, labels) -> None:
        """The cameras a zone can be moved between."""
        current = self.cmb_camera.currentIndex()
        self.cmb_camera.blockSignals(True)
        self.cmb_camera.clear()
        for i, name in enumerate(labels):
            self.cmb_camera.addItem(name, i)
        self.cmb_camera.setCurrentIndex(min(max(0, current), self.cmb_camera.count() - 1))
        self.cmb_camera.blockSignals(False)
        self.cmb_camera.setEnabled(len(labels) > 1)

    def set_target_camera(self, index: int, labels) -> None:
        """Show which camera a new zone would be drawn on, when there is a choice."""
        labels = list(labels)
        several = len(labels) > 1
        self.row_target.setVisible(several)
        if not several:
            return
        self.cmb_target.blockSignals(True)
        if [self.cmb_target.itemText(i) for i in range(self.cmb_target.count())] != labels:
            self.cmb_target.clear()
            for i, name in enumerate(labels):
                self.cmb_target.addItem(name, i)
        self.cmb_target.setCurrentIndex(max(0, min(int(index), len(labels) - 1)))
        self.cmb_target.blockSignals(False)

    def _on_target_changed(self, index: int) -> None:
        if index >= 0:
            self.target_camera_changed.emit(int(index))

    def set_mode(self, mode: str) -> None:
        drawing = mode == "drawing"
        self.btn_finish.setEnabled(drawing)
        self.btn_cancel.setEnabled(drawing)
        self.btn_add.setEnabled(not drawing)
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

    def set_notice(self, text: str) -> None:
        """A one-off message from the picture - a refused double-click, a cancelled draw.

        Written into the same line as the mode instructions rather than a line of its own:
        both answer "what is going on with this drawing", and the next set_mode replaces
        it, so the message lasts exactly as long as the situation it describes.
        """
        self.lbl_hint.setText(text)
        self.lbl_hint.setStyleSheet(f"color: {COLOR_WARN}; font-size: 9pt;")

    def set_dirty(self, dirty: bool) -> None:
        self.btn_save.setText("Save ROI *" if dirty else "Save ROI")

    # ------------------------------------------------------------------ internal
    def _table_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        rid = self.table.item(rows[0].row(), COL_ID).text() if rows else ""
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
        for w in (self.edt_name, self.edt_plc, self.chk_enabled, self.btn_apply, self.btn_delete,
                  self.cmb_camera):
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
        cam = int(getattr(r, "camera", 0))
        if 0 <= cam < self.cmb_camera.count():
            self.cmb_camera.blockSignals(True)
            self.cmb_camera.setCurrentIndex(cam)
            self.cmb_camera.blockSignals(False)

    def set_plc_config(self, cfg: PlcConfig) -> None:
        self._plc_config = cfg
        self._validate_plc()

    def _validated_device(self) -> str:
        text = self.edt_plc.text().strip()
        if not text:
            return ""
        device = normalize_roi_device(text, self._plc_config)
        devices = {r.id: r.plc_device for r in self._rois if r.is_include and r.id != self._selected}
        devices[self._selected] = device
        validate_roi_devices(devices, self._plc_config)
        return device

    def _validate_plc(self) -> None:
        try:
            self._validated_device()
            ok = True
        except ValueError:
            ok = False
        self.edt_plc.setStyleSheet("" if ok else f"border: 1px solid {COLOR_ERROR};")

    def _apply_fields(self) -> None:
        r = self._current()
        if r is None:
            return
        try:
            plc = self._validated_device() if r.is_include else ""
            if r.is_include and self.chk_enabled.isChecked() and not plc:
                raise ValueError("Hãy nhập bit PLC riêng cho ROI này, ví dụ M200")
        except ValueError as exc:
            self.lbl_hint.setText(str(exc))
            self.lbl_hint.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 9pt;")
            return
        self.fields_changed.emit(r.id, self.edt_name.text().strip() or r.name,
                                 plc, self.chk_enabled.isChecked(),
                                 max(0, self.cmb_camera.currentIndex()))
