"""ROI selection and per-ROI PLC assignments, displayed in PLC > Devices."""
from __future__ import annotations

from typing import Dict, List
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
                               QGroupBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from ...config.schemas import PlcConfig
from ...logic.occupancy_state_machine import OccupancyState
from ...plc.roi_mapping import normalize_roi_device, validate_roi_devices
from ...roi.roi_model import Roi
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_TEXT_MUTED
from .form_helpers import hint

COL_ID, COL_CAM, COL_NAME, COL_PLC, COL_STATE = range(5)


class RoiDeviceWidget(QWidget):
    selection_changed = Signal(str)
    fields_changed = Signal(str, str, str, bool, int)
    save_requested = Signal()
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    zones_requested = Signal()

    COLS = ("ID", "Cam", "Name", "PLC bit", "State")

    def __init__(self, cfg: PlcConfig, parent=None) -> None:
        super().__init__(parent)
        self._rois: List[Roi] = []
        self._selected = ""
        self._plc_config = cfg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(hint("Chọn ROI trong danh sách, nhập bit PLC rồi Apply và Save ROI. Mỗi vùng dùng một bit riêng."))
        g_list = QGroupBox("ROI LIST")
        ll = QVBoxLayout(g_list)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(28)
        header.setStretchLastSection(False)
        for col in range(len(self.COLS)):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch if col == COL_NAME else QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(160)
        self.table.setMaximumHeight(200)
        self.table.itemSelectionChanged.connect(self._table_selection)
        self.table.cellClicked.connect(self._table_clicked)
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
        self.btn_save = QPushButton("Save ROI")
        self.btn_save.setProperty("class", "success")
        save_actions = QHBoxLayout()
        save_actions.addWidget(self.btn_apply)
        save_actions.addWidget(self.btn_save)
        f.addRow(save_actions)
        self.btn_edit = QPushButton("Sửa hình ROI")
        self.btn_edit.setToolTip("Mở Zones để chỉnh hình ROI đang chọn")
        self.btn_delete = QPushButton("Delete ROI")
        self.btn_delete.setProperty("class", "dangerline")
        edit_actions = QHBoxLayout()
        edit_actions.addWidget(self.btn_edit)
        edit_actions.addWidget(self.btn_delete)
        f.addRow(edit_actions)
        lay.addWidget(g_fields)

        self.lbl_hint = hint("Chưa có ROI: vào Zones để vẽ vùng trước.")
        lay.addWidget(self.lbl_hint)
        self.btn_zones = QPushButton("Vẽ ROI trong Zones")
        lay.addWidget(self.btn_zones)
        self.btn_apply.clicked.connect(self._apply_fields)
        self.btn_save.clicked.connect(self.save_requested)
        self.btn_zones.clicked.connect(self.zones_requested)
        self.btn_edit.clicked.connect(lambda: self.edit_requested.emit(self._selected) if self._selected else None)
        self.btn_delete.clicked.connect(self._delete_selected)
        self._fill_fields()

    def set_dirty(self, dirty: bool) -> None:
        self.btn_save.setText("Save ROI *" if dirty else "Save ROI")

    @property
    def selected_roi_id(self) -> str:
        return self._selected

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
                    r.plc_device or ("Chưa gán" if r.is_include else "-"),
                    ("ON" if st.occupied else "OFF") if st is not None and r.enabled else "-"]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if col in (COL_CAM, COL_STATE):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == COL_STATE and st is not None:
                    it.setForeground(QColor(COLOR_ERROR if st.occupied else COLOR_OK))
                if not r.enabled:
                    it.setForeground(QColor(COLOR_TEXT_MUTED))
                it.setToolTip(st.value if col == COL_STATE and st is not None else v)
                self.table.setItem(row, col, it)
            if r.id == self._selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        if self._selected and not any(r.id == self._selected for r in rois):
            self._selected = ""
        self._fill_fields()
        self.lbl_hint.setText("" if rois else "Chưa có ROI: vào Zones để vẽ vùng trước.")
        if new_ids:
            self.selection_changed.emit(self._selected)

    def update_states(self, states: Dict[str, OccupancyState]) -> None:
        for row in range(self.table.rowCount()):
            rid_item = self.table.item(row, COL_ID)
            if rid_item is None:
                continue
            st = states.get(rid_item.text())
            it = self.table.item(row, COL_STATE)
            roi = next((r for r in self._rois if r.id == rid_item.text()), None)
            if it is not None and st is not None and roi is not None and roi.enabled:
                it.setText("ON" if st.occupied else "OFF")
                it.setToolTip(st.value)
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
        roi = self._current()
        current = roi.camera if roi is not None else self.cmb_camera.currentIndex()
        self.cmb_camera.blockSignals(True)
        self.cmb_camera.clear()
        for i, name in enumerate(labels):
            self.cmb_camera.addItem(name, i)
        self.cmb_camera.setCurrentIndex(min(max(0, current), self.cmb_camera.count() - 1))
        self.cmb_camera.blockSignals(False)
        self.cmb_camera.setEnabled(len(labels) > 1)

    def _table_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        rid = self.table.item(rows[0].row(), COL_ID).text() if rows else ""
        if rid != self._selected:
            self._selected = rid
            self._fill_fields()
            self.selection_changed.emit(rid)

    def _table_clicked(self, row: int, _column: int) -> None:
        # Qt does not emit itemSelectionChanged when clicking an already highlighted
        # row. Re-select explicitly so a stale/empty details form can recover too.
        item = self.table.item(row, COL_ID)
        if item is None:
            return
        self._selected = item.text()
        self._fill_fields()
        self.selection_changed.emit(self._selected)

    def _delete_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_ID)
        if item is not None and any(r.id == item.text() for r in self._rois):
            self.delete_requested.emit(item.text())

    def _current(self) -> Roi | None:
        for r in self._rois:
            if r.id == self._selected:
                return r
        return None

    def _fill_fields(self) -> None:
        r = self._current()
        enabled = r is not None
        for w in (self.edt_name, self.edt_plc, self.chk_enabled, self.btn_apply, self.btn_delete, self.btn_edit,
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
