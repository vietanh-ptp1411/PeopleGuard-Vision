"""PLC tab: Mitsubishi MC Protocol connection, device mapping, signal mode, heartbeat, fail-safe,
simulation toggle and the Virtual PLC memory table."""
from __future__ import annotations

from copy import deepcopy
from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...config.schemas import FaultPersonOutput, FrameFormat, PlcConfig, SignalMode
from ...plc.device_address import is_valid_device
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM

PLC_SERIES = ["iQ-F (FX5U)", "iQ-R", "Q Series", "L Series", "Other MC 3E"]


class PlcConfigWidget(QWidget):
    connect_requested = Signal()
    disconnect_requested = Signal()
    test_requested = Signal()
    simulation_toggled = Signal(bool)
    config_applied = Signal(object)   # PlcConfig

    def __init__(self, config: PlcConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = deepcopy(config)
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

        # simulation
        g_sim = QGroupBox("MODE")
        ls = QVBoxLayout(g_sim)
        self.btn_sim = QPushButton("SIMULATE PLC (no hardware)")
        self.btn_sim.setCheckable(True)
        self.btn_sim.setMinimumHeight(34)
        self.btn_sim.toggled.connect(self._sim_toggled)
        ls.addWidget(self.btn_sim)
        self.lbl_sim = QLabel("")
        self.lbl_sim.setWordWrap(True)
        self.lbl_sim.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")
        ls.addWidget(self.lbl_sim)
        lay.addWidget(g_sim)

        # connection
        g_conn = QGroupBox("MC PROTOCOL CONNECTION")
        f = QFormLayout(g_conn)
        self.lbl_type = QLabel("Mitsubishi MC / SLMP")
        f.addRow("PLC Type", self.lbl_type)
        self.cmb_series = QComboBox()
        self.cmb_series.addItems(PLC_SERIES)
        f.addRow("PLC Series", self.cmb_series)
        self.edt_ip = QLineEdit()
        self.spn_port = QSpinBox()
        self.spn_port.setRange(1, 65535)
        f.addRow("IP Address", self.edt_ip)
        f.addRow("Port", self.spn_port)
        self.cmb_frame = QComboBox()
        self.cmb_frame.addItems(["3E"])
        self.cmb_format = QComboBox()
        for ff in FrameFormat:
            self.cmb_format.addItem(ff.value.capitalize(), ff.value)
        row = QHBoxLayout()
        row.addWidget(self.cmb_frame)
        row.addWidget(self.cmb_format)
        f.addRow("Frame / Code", row)
        self.spn_net = QSpinBox()
        self.spn_net.setRange(0, 255)
        self.spn_pc = QSpinBox()
        self.spn_pc.setRange(0, 255)
        self.spn_io = QSpinBox()
        self.spn_io.setRange(0, 0xFFFF)
        self.spn_io.setDisplayIntegerBase(16)
        self.spn_io.setPrefix("0x")
        self.spn_station = QSpinBox()
        self.spn_station.setRange(0, 255)
        f.addRow("Network No.", self.spn_net)
        f.addRow("PC No.", self.spn_pc)
        f.addRow("Dest. Module I/O", self.spn_io)
        f.addRow("Dest. Module Station", self.spn_station)
        self.spn_timeout = QDoubleSpinBox()
        self.spn_timeout.setRange(0.2, 30.0)
        self.spn_timeout.setSuffix(" s")
        self.spn_retry = QSpinBox()
        self.spn_retry.setRange(0, 10)
        self.chk_auto_reconnect = QCheckBox("Auto reconnect")
        f.addRow("Timeout", self.spn_timeout)
        f.addRow("Retry count", self.spn_retry)
        f.addRow(self.chk_auto_reconnect)
        lay.addWidget(g_conn)

        # mapping
        g_map = QGroupBox("DEVICE MAPPING")
        fm = QFormLayout(g_map)
        self.cmb_signal = QComboBox()
        self.cmb_signal.addItem("Mode A - single bit (PERSON)", SignalMode.SINGLE_BIT.value)
        self.cmb_signal.addItem("Mode B - PERSON bit + CLEAR bit", SignalMode.DUAL_BIT.value)
        fm.addRow("Signal mode", self.cmb_signal)
        self.edt_person = QLineEdit()
        self.edt_clear = QLineEdit()
        self.edt_camera = QLineEdit()
        self.edt_ai = QLineEdit()
        self.edt_fault = QLineEdit()
        self.edt_hb = QLineEdit()
        self.edt_word = QLineEdit()
        fm.addRow("Device Person / Occupied", self.edt_person)
        fm.addRow("Device Area Clear", self.edt_clear)
        fm.addRow("Device Camera OK", self.edt_camera)
        fm.addRow("Device AI Running", self.edt_ai)
        fm.addRow("Device System Fault", self.edt_fault)
        fm.addRow("Heartbeat Device", self.edt_hb)
        self.chk_word = QCheckBox("Write status word")
        fm.addRow(self.chk_word)
        fm.addRow("Status word device", self.edt_word)
        lbl_codes = QLabel("0 = CLEAR   1 = OCCUPIED   2 = CAMERA ERROR   3 = PLC ERROR   4 = AI ERROR   5 = STOPPED")
        lbl_codes.setWordWrap(True)
        lbl_codes.setProperty("class", "hint")
        fm.addRow(lbl_codes)
        self.chk_roi_devices = QCheckBox("Write per-ROI devices")
        fm.addRow(self.chk_roi_devices)
        for e in (self.edt_person, self.edt_clear, self.edt_camera, self.edt_ai, self.edt_fault, self.edt_hb, self.edt_word):
            e.textChanged.connect(self._validate_devices)
        lay.addWidget(g_map)

        # heartbeat + failsafe
        g_hb = QGroupBox("HEARTBEAT & FAIL-SAFE")
        fh = QFormLayout(g_hb)
        self.chk_hb = QCheckBox("Heartbeat enabled")
        self.spn_hb = QSpinBox()
        self.spn_hb.setRange(100, 60000)
        self.spn_hb.setSuffix(" ms")
        self.spn_hb.setSingleStep(100)
        fh.addRow(self.chk_hb)
        fh.addRow("Heartbeat interval", self.spn_hb)
        self.cmb_fault_person = QComboBox()
        self.cmb_fault_person.addItem("Hold last value (never fake CLEAR)", FaultPersonOutput.HOLD.value)
        self.cmb_fault_person.addItem("Force ON (treat as occupied)", FaultPersonOutput.ON.value)
        self.cmb_fault_person.addItem("Force OFF (PLC evaluates FAULT bit)", FaultPersonOutput.OFF.value)
        fh.addRow("PERSON bit on FAULT", self.cmb_fault_person)
        self.chk_clear_off = QCheckBox("CLEAR bit OFF on FAULT")
        fh.addRow(self.chk_clear_off)
        lay.addWidget(g_hb)

        # control
        g_ctl = QGroupBox("PLC CONTROL")
        gl = QGridLayout(g_ctl)
        self.btn_apply = QPushButton("Apply && Save")
        self.btn_apply.setProperty("class", "primary")
        self.btn_connect = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_test = QPushButton("Test Connection")
        gl.addWidget(self.btn_apply, 0, 0, 1, 2)
        gl.addWidget(self.btn_connect, 1, 0)
        gl.addWidget(self.btn_disconnect, 1, 1)
        gl.addWidget(self.btn_test, 2, 0, 1, 2)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        gl.addWidget(self.lbl_status, 3, 0, 1, 2)
        lay.addWidget(g_ctl)

        # virtual memory
        g_mem = QGroupBox("PLC MEMORY")
        lm = QVBoxLayout(g_mem)
        lbl_mem = QLabel("Virtual memory in simulation mode, last written values with a real PLC.")
        lbl_mem.setWordWrap(True)
        lbl_mem.setProperty("class", "hint")
        lm.addWidget(lbl_mem)
        self.tbl_mem = QTableWidget(0, 3)
        self.tbl_mem.setHorizontalHeaderLabels(["Device", "Value", "Meaning"])
        self.tbl_mem.horizontalHeader().setStretchLastSection(True)
        self.tbl_mem.verticalHeader().setVisible(False)
        self.tbl_mem.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_mem.setMinimumHeight(180)
        lm.addWidget(self.tbl_mem)
        lay.addWidget(g_mem)
        lay.addStretch(1)

        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)

        self.btn_apply.clicked.connect(self._apply)
        self.btn_connect.clicked.connect(lambda: (self._apply(), self.connect_requested.emit()))
        self.btn_disconnect.clicked.connect(self.disconnect_requested)
        self.btn_test.clicked.connect(lambda: (self._apply(), self.test_requested.emit()))

    # ------------------------------------------------------------------ handlers
    def _sim_toggled(self, on: bool) -> None:
        self.btn_sim.setText("SIMULATION MODE: ON  (virtual PLC)" if on else "SIMULATE PLC (no hardware)")
        self.lbl_sim.setText("No real PLC is contacted. Writes go to the virtual memory table below."
                             if on else "Real Mitsubishi PLC via MC Protocol 3E over TCP.")
        for w in (self.edt_ip, self.spn_port, self.cmb_format, self.spn_net, self.spn_pc, self.spn_io, self.spn_station,
                  self.spn_timeout, self.spn_retry, self.chk_auto_reconnect, self.cmb_series):
            w.setEnabled(not on)
        self.simulation_toggled.emit(on)

    def _validate_devices(self) -> None:
        for e in (self.edt_person, self.edt_clear, self.edt_camera, self.edt_ai, self.edt_fault, self.edt_hb, self.edt_word):
            txt = e.text().strip()
            ok = (not txt) or is_valid_device(txt)
            e.setStyleSheet("" if ok else f"border: 1px solid {COLOR_ERROR};")

    def set_status(self, text: str, ok: bool | None = None) -> None:
        color = COLOR_TEXT_DIM if ok is None else (COLOR_OK if ok else COLOR_ERROR)
        self.lbl_status.setStyleSheet(f"color: {color};")
        self.lbl_status.setText(text)

    def set_connected(self, connected: bool) -> None:
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)

    def set_simulation(self, on: bool) -> None:
        if self.btn_sim.isChecked() != on:
            self.btn_sim.setChecked(on)

    def set_memory(self, mem: Dict[str, int]) -> None:
        meanings = self._meanings()
        self.tbl_mem.setRowCount(len(mem))
        for row, (dev, val) in enumerate(mem.items()):
            it_dev = QTableWidgetItem(dev)
            it_val = QTableWidgetItem("ON" if (dev[0] in "MXYBLFSV" and not dev.startswith(("SD", "SW", "SN")) and val in (0, 1)) and val == 1
                                      else ("OFF" if dev[0] in "MXYBLFSV" and not dev.startswith(("SD", "SW", "SN")) and val == 0 else str(val)))
            it_val.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if val:
                it_val.setForeground(QColor(COLOR_ERROR if dev == self._config.mapping.device_person else COLOR_OK))
            else:
                it_val.setForeground(QColor(COLOR_TEXT_DIM))
            it_mean = QTableWidgetItem(meanings.get(dev, ""))
            self.tbl_mem.setItem(row, 0, it_dev)
            self.tbl_mem.setItem(row, 1, it_val)
            self.tbl_mem.setItem(row, 2, it_mean)

    def _meanings(self) -> Dict[str, str]:
        m = self._config.mapping
        return {
            m.device_person: "PERSON PRESENT / AREA OCCUPIED",
            m.device_clear: "AREA CLEAR",
            m.device_camera_ok: "Camera connected",
            m.device_ai_running: "AI running",
            m.device_fault: "System fault",
            m.device_heartbeat: "Heartbeat",
            m.device_status_word: "Status word",
        }

    # ------------------------------------------------------------------ config <-> widgets
    def set_config(self, cfg: PlcConfig) -> None:
        self._config = deepcopy(cfg)
        c = cfg.connection
        self.cmb_series.setCurrentText(c.plc_series if c.plc_series in PLC_SERIES else PLC_SERIES[-1])
        self.edt_ip.setText(c.ip)
        self.spn_port.setValue(c.port)
        idx = self.cmb_format.findData(c.frame_format)
        self.cmb_format.setCurrentIndex(max(0, idx))
        self.spn_net.setValue(c.network_no)
        self.spn_pc.setValue(c.pc_no)
        self.spn_io.setValue(c.dest_module_io)
        self.spn_station.setValue(c.dest_module_station)
        self.spn_timeout.setValue(c.timeout_s)
        self.spn_retry.setValue(c.retry_count)
        self.chk_auto_reconnect.setChecked(c.auto_reconnect)
        m = cfg.mapping
        idx = self.cmb_signal.findData(cfg.signal_mode)
        self.cmb_signal.setCurrentIndex(max(0, idx))
        self.edt_person.setText(m.device_person)
        self.edt_clear.setText(m.device_clear)
        self.edt_camera.setText(m.device_camera_ok)
        self.edt_ai.setText(m.device_ai_running)
        self.edt_fault.setText(m.device_fault)
        self.edt_hb.setText(m.device_heartbeat)
        self.edt_word.setText(m.device_status_word)
        self.chk_word.setChecked(cfg.word_output_enabled)
        self.chk_roi_devices.setChecked(m.write_roi_devices)
        self.chk_hb.setChecked(cfg.heartbeat.enabled)
        self.spn_hb.setValue(cfg.heartbeat.interval_ms)
        idx = self.cmb_fault_person.findData(cfg.failsafe.person_output_on_fault)
        self.cmb_fault_person.setCurrentIndex(max(0, idx))
        self.chk_clear_off.setChecked(cfg.failsafe.clear_off_on_fault)
        self.btn_sim.blockSignals(True)
        self.btn_sim.setChecked(cfg.simulation_mode)
        self.btn_sim.blockSignals(False)
        self._sim_toggled_silent(cfg.simulation_mode)

    def _sim_toggled_silent(self, on: bool) -> None:
        self.btn_sim.setText("SIMULATION MODE: ON  (virtual PLC)" if on else "SIMULATE PLC (no hardware)")
        self.lbl_sim.setText("No real PLC is contacted. Writes go to the virtual memory table below."
                             if on else "Real Mitsubishi PLC via MC Protocol 3E over TCP.")
        for w in (self.edt_ip, self.spn_port, self.cmb_format, self.spn_net, self.spn_pc, self.spn_io, self.spn_station,
                  self.spn_timeout, self.spn_retry, self.chk_auto_reconnect, self.cmb_series):
            w.setEnabled(not on)

    def get_config(self) -> PlcConfig:
        cfg = deepcopy(self._config)
        cfg.simulation_mode = self.btn_sim.isChecked()
        c = cfg.connection
        c.plc_series = self.cmb_series.currentText()
        c.ip = self.edt_ip.text().strip()
        c.port = self.spn_port.value()
        c.frame = "3E"
        c.frame_format = str(self.cmb_format.currentData())
        c.network_no = self.spn_net.value()
        c.pc_no = self.spn_pc.value()
        c.dest_module_io = self.spn_io.value()
        c.dest_module_station = self.spn_station.value()
        c.timeout_s = round(self.spn_timeout.value(), 2)
        c.retry_count = self.spn_retry.value()
        c.auto_reconnect = self.chk_auto_reconnect.isChecked()
        cfg.signal_mode = str(self.cmb_signal.currentData())
        m = cfg.mapping
        m.device_person = self.edt_person.text().strip().upper()
        m.device_clear = self.edt_clear.text().strip().upper()
        m.device_camera_ok = self.edt_camera.text().strip().upper()
        m.device_ai_running = self.edt_ai.text().strip().upper()
        m.device_fault = self.edt_fault.text().strip().upper()
        m.device_heartbeat = self.edt_hb.text().strip().upper()
        m.device_status_word = self.edt_word.text().strip().upper()
        m.write_roi_devices = self.chk_roi_devices.isChecked()
        cfg.word_output_enabled = self.chk_word.isChecked()
        cfg.heartbeat.enabled = self.chk_hb.isChecked()
        cfg.heartbeat.interval_ms = self.spn_hb.value()
        cfg.failsafe.person_output_on_fault = str(self.cmb_fault_person.currentData())
        cfg.failsafe.clear_off_on_fault = self.chk_clear_off.isChecked()
        return cfg

    def _apply(self) -> None:
        self._config = self.get_config()
        self.config_applied.emit(deepcopy(self._config))
