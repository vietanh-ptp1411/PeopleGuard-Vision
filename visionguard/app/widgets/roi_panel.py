"""Zones tab: draw and reshape polygons; PLC > Devices owns ROI assignments."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)
from ..theme import COLOR_OK, COLOR_TEXT_DIM, COLOR_WARN
from .form_helpers import page_header


class RoiPanel(QWidget):
    add_include_requested = Signal()
    finish_requested = Signal()
    cancel_requested = Signal()
    edit_mode_toggled = Signal(bool)
    delete_requested = Signal(str)
    clear_requested = Signal()
    save_requested = Signal()
    devices_requested = Signal()
    target_camera_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rois = []
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

        lay.addWidget(page_header("Zones", "Vẽ và chỉnh hình vùng; chọn ROI và gán bit tại PLC → Devices"))
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
        self.btn_add.setToolTip("Vẽ ROI, sau đó vào PLC → Devices để gán bit riêng")
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

        self.lbl_selected = QLabel("Chưa chọn ROI — bấm vào vùng trên hình để chọn")
        self.lbl_selected.setWordWrap(True)
        lay.addWidget(self.lbl_selected)
        self.btn_devices = QPushButton("Chọn ROI / gán bit tại PLC → Devices")
        self.btn_devices.setProperty("class", "primary")
        self.btn_devices.clicked.connect(self.devices_requested)
        lay.addWidget(self.btn_devices)
        lay.addStretch(1)
        self.btn_add.clicked.connect(self.add_include_requested)
        self.btn_finish.clicked.connect(self.finish_requested)
        self.btn_cancel.clicked.connect(self.cancel_requested)
        self.btn_edit.toggled.connect(self.edit_mode_toggled)
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self._selected) if self._selected else None)
        self.btn_clear.clicked.connect(self.clear_requested)
        self.btn_save.clicked.connect(self.save_requested)
        self.set_mode("none")
        self.select("")

    def set_rois(self, rois) -> None:
        self._rois = list(rois)
        self.select(self._selected)

    def select(self, roi_id: str) -> None:
        roi = next((r for r in self._rois if r.id == roi_id), None)
        self._selected = roi.id if roi else ""
        self.lbl_selected.setText(f"Đang chọn: {roi.id} · {roi.name}" if roi else
                                  "Chưa chọn ROI — bấm vào vùng trên hình để chọn")
        self.btn_delete.setEnabled(roi is not None)

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
        if self.btn_edit.isChecked() != (mode == "edit"):
            self.btn_edit.blockSignals(True)
            self.btn_edit.setChecked(mode == "edit")
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
