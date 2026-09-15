"""I/O Test tab: manual PLC read/write independent from the AI pipeline."""
from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...config.schemas import PlcMappingConfig
from ...plc.device_address import is_valid_device
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM


class IoTestWidget(QWidget):
    write_requested = Signal(str, int)
    read_requested = Signal(str)

    def __init__(self, mapping: PlcMappingConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mapping = mapping
        self._quick_buttons: List[QWidget] = []
        self._build()
        self.set_mapping(mapping)

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

        g_manual = QGroupBox("MANUAL DEVICE ACCESS")
        gl = QGridLayout(g_manual)
        gl.addWidget(QLabel("Device"), 0, 0)
        self.edt_device = QLineEdit("M100")
        self.edt_device.textChanged.connect(self._validate)
        gl.addWidget(self.edt_device, 0, 1)
        gl.addWidget(QLabel("Value"), 0, 2)
        self.spn_value = QSpinBox()
        self.spn_value.setRange(0, 65535)
        gl.addWidget(self.spn_value, 0, 3)
        self.btn_on = QPushButton("Write ON (1)")
        self.btn_on.setProperty("class", "success")
        self.btn_off = QPushButton("Write OFF (0)")
        self.btn_off.setProperty("class", "danger")
        self.btn_write = QPushButton("Write Value")
        self.btn_read = QPushButton("Read")
        gl.addWidget(self.btn_on, 1, 0)
        gl.addWidget(self.btn_off, 1, 1)
        gl.addWidget(self.btn_write, 1, 2)
        gl.addWidget(self.btn_read, 1, 3)
        lay.addWidget(g_manual)

        self.g_quick = QGroupBox("QUICK ACCESS (mapped devices)")
        self.quick_layout = QGridLayout(self.g_quick)
        lay.addWidget(self.g_quick)

        g_log = QGroupBox("RESULTS")
        ll = QVBoxLayout(g_log)
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setMaximumBlockCount(500)
        self.txt.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        self.txt.setMinimumHeight(120)
        ll.addWidget(self.txt)
        lay.addWidget(g_log, 1)

        self.btn_on.clicked.connect(lambda: self._write(1))
        self.btn_off.clicked.connect(lambda: self._write(0))
        self.btn_write.clicked.connect(lambda: self._write(self.spn_value.value()))
        self.btn_read.clicked.connect(self._read)

    def _device(self) -> str:
        return self.edt_device.text().strip().upper()

    def _validate(self) -> None:
        ok = is_valid_device(self._device())
        self.edt_device.setStyleSheet("" if ok else f"border: 1px solid {COLOR_ERROR};")
        for b in (self.btn_on, self.btn_off, self.btn_write, self.btn_read):
            b.setEnabled(ok)

    def _write(self, value: int) -> None:
        dev = self._device()
        if is_valid_device(dev):
            self.write_requested.emit(dev, int(value))

    def _read(self) -> None:
        dev = self._device()
        if is_valid_device(dev):
            self.read_requested.emit(dev)

    def set_mapping(self, mapping: PlcMappingConfig) -> None:
        self._mapping = mapping
        for w in self._quick_buttons:
            self.quick_layout.removeWidget(w)
            w.deleteLater()
        self._quick_buttons.clear()
        rows: List[Tuple[str, str, bool]] = [
            ("Person / Occupied", mapping.device_person, True),
            ("Area Clear", mapping.device_clear, True),
            ("Camera OK", mapping.device_camera_ok, True),
            ("AI Running", mapping.device_ai_running, True),
            ("System Fault", mapping.device_fault, True),
            ("Heartbeat", mapping.device_heartbeat, True),
            ("Status Word", mapping.device_status_word, False),
        ]
        r = 0
        for label, dev, is_bit in rows:
            if not dev:
                continue
            lbl = QLabel(f"{label}  <b>{dev}</b>")
            self.quick_layout.addWidget(lbl, r, 0)
            self._quick_buttons.append(lbl)
            if is_bit:
                b_on = QPushButton("ON")
                b_off = QPushButton("OFF")
                b_on.clicked.connect(lambda _=False, d=dev: self.write_requested.emit(d, 1))
                b_off.clicked.connect(lambda _=False, d=dev: self.write_requested.emit(d, 0))
                self.quick_layout.addWidget(b_on, r, 1)
                self.quick_layout.addWidget(b_off, r, 2)
                self._quick_buttons += [b_on, b_off]
            else:
                b_w = QPushButton("Write value")
                b_w.clicked.connect(lambda _=False, d=dev: self.write_requested.emit(d, self.spn_value.value()))
                self.quick_layout.addWidget(b_w, r, 1, 1, 2)
                self._quick_buttons.append(b_w)
            b_rd = QPushButton("Read")
            b_rd.clicked.connect(lambda _=False, d=dev: self.read_requested.emit(d))
            self.quick_layout.addWidget(b_rd, r, 3)
            self._quick_buttons.append(b_rd)
            r += 1

    def append_result(self, ok: bool, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        color = COLOR_OK if ok else COLOR_ERROR
        self.txt.appendHtml(f'<span style="color:{COLOR_TEXT_DIM}">{ts}</span>  <span style="color:{color}">{message}</span>')
