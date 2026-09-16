"""AI tab: detector settings (model/conf/iou/device/tracking) + ROI logic & debounce settings."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...config.schemas import AIConfig, ContainmentMode
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM
from .form_helpers import AdvancedSection, advanced_checkbox, hint


class AIConfigWidget(QWidget):
    load_model_requested = Signal()
    start_detection_requested = Signal()
    stop_detection_requested = Signal()
    config_applied = Signal(object)   # AIConfig

    def __init__(self, config: AIConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = deepcopy(config)
        self.advanced = AdvancedSection()
        self._build()
        self.set_config(config)

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

        head = QHBoxLayout()
        self.chk_advanced = advanced_checkbox(self.advanced)
        head.addWidget(self.chk_advanced)
        head.addStretch(1)
        lay.addLayout(head)

        g = QGroupBox("YOLO PERSON DETECTOR")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.edt_model = QLineEdit()
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.edt_model, 1)
        row.addWidget(self.btn_browse)
        f.addRow("Model path", row)
        self.spn_conf = QDoubleSpinBox()
        self.spn_conf.setRange(0.05, 0.95)
        self.spn_conf.setSingleStep(0.05)
        self.spn_iou = QDoubleSpinBox()
        self.spn_iou.setRange(0.1, 0.95)
        self.spn_iou.setSingleStep(0.05)
        self.cmb_device = QComboBox()
        self.cmb_device.addItems(["auto", "cpu", "cuda"])
        self.spn_imgsz = QSpinBox()
        self.spn_imgsz.setRange(160, 1920)
        self.spn_imgsz.setSingleStep(32)
        self.chk_tracking = QCheckBox("Person tracking (ByteTrack IDs)")
        self.chk_half = QCheckBox("FP16 on CUDA")
        self.chk_autoload = QCheckBox("Load model automatically at startup")
        f.addRow("Confidence", self.spn_conf)
        f.addRow("Device", self.cmb_device)
        f.addRow(hint("Confidence thấp thì dễ bắt người hơn nhưng dễ báo nhầm. Device auto sẽ dùng GPU nếu có."))
        f.addRow("IoU", self.spn_iou)
        f.addRow("Image size", self.spn_imgsz)
        f.addRow("", self.chk_tracking)
        f.addRow("", self.chk_half)
        f.addRow("", self.chk_autoload)
        self.advanced.rows(f, self.spn_iou, self.spn_imgsz, self.chk_tracking, self.chk_half,
                           self.chk_autoload)
        self.lbl_model = QLabel("Model: not loaded")
        self.lbl_model.setWordWrap(True)
        self.lbl_model.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        f.addRow(self.lbl_model)
        lay.addWidget(g)

        g2 = QGroupBox("ROI LOGIC & DEBOUNCE")
        f2 = QFormLayout(g2)
        self.cmb_mode = QComboBox()
        for m in ContainmentMode:
            self.cmb_mode.addItem(m.label, m.value)
        self.cmb_mode.currentIndexChanged.connect(self._mode_changed)
        self.spn_inter = QDoubleSpinBox()
        self.spn_inter.setRange(0.05, 1.0)
        self.spn_inter.setSingleStep(0.05)
        self.spn_on = QSpinBox()
        self.spn_on.setRange(0, 60000)
        self.spn_on.setSuffix(" ms")
        self.spn_on.setSingleStep(50)
        self.spn_off = QSpinBox()
        self.spn_off.setRange(0, 600000)
        self.spn_off.setSuffix(" ms")
        self.spn_off.setSingleStep(100)
        self.spn_min_frames = QSpinBox()
        self.spn_min_frames.setRange(1, 100)
        f2.addRow("Containment mode", self.cmb_mode)
        f2.addRow("ON delay", self.spn_on)
        f2.addRow("OFF delay", self.spn_off)
        f2.addRow("Intersection threshold", self.spn_inter)
        f2.addRow("Min detection frames", self.spn_min_frames)
        self.advanced.rows(f2, self.spn_inter, self.spn_min_frames)
        f2.addRow(hint("ON delay: người phải xuất hiện liên tục bao lâu mới báo CÓ NGƯỜI.  "
                       "OFF delay: vùng phải trống bao lâu mới báo HẾT NGƯỜI "
                       "(mất 1-2 frame vẫn giữ trạng thái)."))
        lay.addWidget(g2)

        g3 = QGroupBox("AI CONTROL")
        gl = QGridLayout(g3)
        self.btn_apply = QPushButton("Apply && Save")
        self.btn_apply.setProperty("class", "primary")
        self.btn_load = QPushButton("Load Model")
        self.btn_start = QPushButton("Start Detection")
        self.btn_start.setProperty("class", "success")
        self.btn_stop = QPushButton("Stop Detection")
        self.btn_stop.setProperty("class", "danger")
        gl.addWidget(self.btn_apply, 0, 0, 1, 2)
        gl.addWidget(self.btn_load, 1, 0, 1, 2)
        gl.addWidget(self.btn_start, 2, 0)
        gl.addWidget(self.btn_stop, 2, 1)
        lay.addWidget(g3)
        lay.addStretch(1)

        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)

        self.advanced.set_visible(False)

        self.btn_apply.clicked.connect(self._apply)
        self.btn_load.clicked.connect(lambda: (self._apply(), self.load_model_requested.emit()))
        self.btn_start.clicked.connect(lambda: (self._apply(), self.start_detection_requested.emit()))
        self.btn_stop.clicked.connect(self.stop_detection_requested)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select YOLO model", "models", "YOLO model (*.pt *.onnx *.engine);;All files (*)")
        if path:
            self.edt_model.setText(path)

    def _mode_changed(self) -> None:
        self.spn_inter.setEnabled(self.cmb_mode.currentData() == ContainmentMode.INTERSECTION.value)

    # ------------------------------------------------------------------ state from controller
    def set_model_status(self, text: str, ok: bool | None) -> None:
        color = COLOR_TEXT_DIM if ok is None else (COLOR_OK if ok else COLOR_ERROR)
        self.lbl_model.setStyleSheet(f"color: {color};")
        self.lbl_model.setText(text)

    def set_detection_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    # ------------------------------------------------------------------ config <-> widgets
    def set_config(self, cfg: AIConfig) -> None:
        self._config = deepcopy(cfg)
        d = cfg.detector
        self.edt_model.setText(d.model_path)
        self.spn_conf.setValue(d.confidence)
        self.spn_iou.setValue(d.iou)
        self.cmb_device.setCurrentText(d.device if d.device in ("auto", "cpu", "cuda") else "auto")
        self.spn_imgsz.setValue(d.imgsz)
        self.chk_tracking.setChecked(d.tracking_enabled)
        self.chk_half.setChecked(d.half)
        self.chk_autoload.setChecked(d.auto_load_on_start)
        lg = cfg.logic
        idx = self.cmb_mode.findData(lg.containment_mode)
        self.cmb_mode.setCurrentIndex(max(0, idx))
        self.spn_inter.setValue(lg.intersection_threshold)
        self.spn_on.setValue(lg.on_delay_ms)
        self.spn_off.setValue(lg.off_delay_ms)
        self.spn_min_frames.setValue(lg.min_detection_frames)
        self._mode_changed()
        self.advanced.apply()

    def get_config(self) -> AIConfig:
        cfg = deepcopy(self._config)
        d = cfg.detector
        d.model_path = self.edt_model.text().strip() or "models/yolo11n.pt"
        d.confidence = round(self.spn_conf.value(), 3)
        d.iou = round(self.spn_iou.value(), 3)
        d.device = self.cmb_device.currentText()
        d.imgsz = int(self.spn_imgsz.value() // 32 * 32)
        d.tracking_enabled = self.chk_tracking.isChecked()
        d.half = self.chk_half.isChecked()
        d.auto_load_on_start = self.chk_autoload.isChecked()
        lg = cfg.logic
        lg.containment_mode = str(self.cmb_mode.currentData())
        lg.intersection_threshold = round(self.spn_inter.value(), 3)
        lg.on_delay_ms = self.spn_on.value()
        lg.off_delay_ms = self.spn_off.value()
        lg.min_detection_frames = self.spn_min_frames.value()
        return cfg

    def _apply(self) -> None:
        self._config = self.get_config()
        self.config_applied.emit(deepcopy(self._config))
