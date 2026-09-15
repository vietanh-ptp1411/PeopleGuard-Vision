"""Camera settings tab: camera type dropdown + per-type configuration pages + controls."""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ...camera.base_camera import DeviceDescriptor
from ...camera.rtsp_camera import RTSP_PRESETS, build_rtsp_url, mask_url
from ...camera.video_camera import VIDEO_EXTENSIONS
from ...config.schemas import CameraConfig, CameraType
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM


def _btn(text: str, cls: str = "") -> QPushButton:
    b = QPushButton(text)
    if cls:
        b.setProperty("class", cls)
    return b


class CameraConfigWidget(QWidget):
    connect_requested = Signal()
    disconnect_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    test_requested = Signal()
    scan_requested = Signal(str)            # camera type value
    rtsp_test_requested = Signal()
    video_command = Signal(str, object)     # pause/resume/toggle/restart/seek/loop
    config_applied = Signal(object)         # CameraConfig

    def __init__(self, config: CameraConfig, availability: Dict[CameraType, Tuple[bool, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = deepcopy(config)
        self._availability = availability
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

        # type
        g_type = QGroupBox("CAMERA TYPE")
        lt = QFormLayout(g_type)
        self.cmb_type = QComboBox()
        for ct in CameraType:
            ok, status = self._availability.get(ct, (True, "OK"))
            label = ct.label if ok else f"{ct.label}  -  {status.split('(')[0].strip()}"
            self.cmb_type.addItem(label, ct.value)
        self.cmb_type.currentIndexChanged.connect(self._on_type_changed)
        lt.addRow("Camera Type", self.cmb_type)
        self.lbl_sdk = QLabel("")
        self.lbl_sdk.setWordWrap(True)
        self.lbl_sdk.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")
        lt.addRow(self.lbl_sdk)
        lay.addWidget(g_type)

        # pages
        self.stack = QStackedWidget()
        self.page_usb = self._build_usb()
        self.page_video = self._build_video()
        self.page_rtsp = self._build_rtsp()
        self.page_ind = self._build_industrial()
        self.stack.addWidget(self.page_usb)
        self.stack.addWidget(self.page_video)
        self.stack.addWidget(self.page_rtsp)
        self.stack.addWidget(self.page_ind)
        for i in range(self.stack.count()):
            self.stack.widget(i).setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        lay.addWidget(self.stack)

        # reconnect
        g_rc = QGroupBox("AUTO RECONNECT")
        lrc = QFormLayout(g_rc)
        self.chk_reconnect = QCheckBox("Enabled")
        self.spn_frame_timeout = QDoubleSpinBox()
        self.spn_frame_timeout.setRange(0.5, 120.0)
        self.spn_frame_timeout.setSuffix(" s")
        self.edt_delays = QLineEdit()
        self.edt_delays.setPlaceholderText("1, 2, 5")
        lrc.addRow(self.chk_reconnect)
        lrc.addRow("Frame timeout", self.spn_frame_timeout)
        lrc.addRow("Retry delays (s)", self.edt_delays)
        lay.addWidget(g_rc)

        # controls
        g_ctl = QGroupBox("CAMERA CONTROL")
        gl = QGridLayout(g_ctl)
        self.btn_apply = _btn("Apply && Save", "primary")
        self.btn_connect = _btn("Connect")
        self.btn_disconnect = _btn("Disconnect")
        self.btn_start = _btn("Start", "success")
        self.btn_stop = _btn("Stop", "danger")
        self.btn_test = _btn("Test Camera")
        gl.addWidget(self.btn_apply, 0, 0, 1, 2)
        gl.addWidget(self.btn_connect, 1, 0)
        gl.addWidget(self.btn_disconnect, 1, 1)
        gl.addWidget(self.btn_start, 2, 0)
        gl.addWidget(self.btn_stop, 2, 1)
        gl.addWidget(self.btn_test, 3, 0, 1, 2)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        gl.addWidget(self.lbl_status, 4, 0, 1, 2)
        lay.addWidget(g_ctl)
        lay.addStretch(1)

        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)

        self.btn_apply.clicked.connect(self._apply)
        self.btn_connect.clicked.connect(lambda: (self._apply(), self.connect_requested.emit()))
        self.btn_disconnect.clicked.connect(self.disconnect_requested)
        self.btn_start.clicked.connect(lambda: (self._apply(), self.start_requested.emit()))
        self.btn_stop.clicked.connect(self.stop_requested)
        self.btn_test.clicked.connect(lambda: (self._apply(), self.test_requested.emit()))

    def _build_usb(self) -> QWidget:
        w = QGroupBox("USB CAMERA")
        f = QFormLayout(w)
        row = QHBoxLayout()
        self.spn_usb_index = QSpinBox()
        self.spn_usb_index.setRange(0, 31)
        self.cmb_usb_devices = QComboBox()
        self.cmb_usb_devices.setMinimumWidth(140)
        self.cmb_usb_devices.currentIndexChanged.connect(self._usb_device_picked)
        self.btn_usb_scan = _btn("Scan")
        self.btn_usb_scan.clicked.connect(lambda: self.scan_requested.emit(CameraType.USB.value))
        row.addWidget(self.spn_usb_index)
        row.addWidget(self.cmb_usb_devices, 1)
        row.addWidget(self.btn_usb_scan)
        f.addRow("Device Index", row)
        res = QHBoxLayout()
        self.spn_usb_w = QSpinBox()
        self.spn_usb_w.setRange(0, 7680)
        self.spn_usb_h = QSpinBox()
        self.spn_usb_h.setRange(0, 4320)
        self.cmb_usb_res = QComboBox()
        for label, wh in (("640x480", (640, 480)), ("1280x720", (1280, 720)), ("1920x1080", (1920, 1080)), ("Custom", None)):
            self.cmb_usb_res.addItem(label, wh)
        self.cmb_usb_res.currentIndexChanged.connect(self._usb_res_picked)
        res.addWidget(self.cmb_usb_res)
        res.addWidget(self.spn_usb_w)
        res.addWidget(QLabel("x"))
        res.addWidget(self.spn_usb_h)
        f.addRow("Resolution", res)
        self.spn_usb_fps = QSpinBox()
        self.spn_usb_fps.setRange(0, 240)
        f.addRow("FPS", self.spn_usb_fps)
        self.cmb_usb_backend = QComboBox()
        for b in ("auto", "dshow", "msmf", "v4l2"):
            self.cmb_usb_backend.addItem(b)
        f.addRow("Backend", self.cmb_usb_backend)
        return w

    def _build_video(self) -> QWidget:
        w = QGroupBox("VIDEO FILE")
        f = QFormLayout(w)
        row = QHBoxLayout()
        self.edt_video_path = QLineEdit()
        self.edt_video_path.setPlaceholderText("path/to/video.mp4")
        self.btn_video_browse = _btn("Browse...")
        self.btn_video_browse.clicked.connect(self._browse_video)
        row.addWidget(self.edt_video_path, 1)
        row.addWidget(self.btn_video_browse)
        f.addRow("Video", row)
        self.chk_video_loop = QCheckBox("Loop")
        self.chk_video_realtime = QCheckBox("Real-time playback (file FPS)")
        self.chk_video_loop.toggled.connect(lambda v: self.video_command.emit("loop", bool(v)))
        f.addRow(self.chk_video_loop)
        f.addRow(self.chk_video_realtime)
        ctl = QHBoxLayout()
        self.btn_video_toggle = _btn("Play / Pause")
        self.btn_video_restart = _btn("Restart")
        self.btn_video_toggle.clicked.connect(lambda: self.video_command.emit("toggle", None))
        self.btn_video_restart.clicked.connect(lambda: self.video_command.emit("restart", None))
        ctl.addWidget(self.btn_video_toggle)
        ctl.addWidget(self.btn_video_restart)
        f.addRow("Playback", ctl)
        self.sld_video = QSlider(Qt.Orientation.Horizontal)
        self.sld_video.setRange(0, 1000)
        self.sld_video.sliderReleased.connect(lambda: self.video_command.emit("seek", self.sld_video.value() / 1000.0))
        self.lbl_video_pos = QLabel("0 / 0")
        prow = QHBoxLayout()
        prow.addWidget(self.sld_video, 1)
        prow.addWidget(self.lbl_video_pos)
        f.addRow("Position", prow)
        return w

    def _build_rtsp(self) -> QWidget:
        w = QGroupBox("RTSP / IP CAMERA")
        f = QFormLayout(w)
        self.cmb_rtsp_preset = QComboBox()
        for key, preset in RTSP_PRESETS.items():
            self.cmb_rtsp_preset.addItem(preset["label"], key)
        self.cmb_rtsp_preset.currentIndexChanged.connect(self._rtsp_preset_picked)
        f.addRow("Vendor preset", self.cmb_rtsp_preset)
        self.edt_rtsp_ip = QLineEdit()
        self.spn_rtsp_port = QSpinBox()
        self.spn_rtsp_port.setRange(1, 65535)
        self.edt_rtsp_user = QLineEdit()
        self.edt_rtsp_pass = QLineEdit()
        self.edt_rtsp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.edt_rtsp_path = QLineEdit()
        self.edt_rtsp_url = QLineEdit()
        self.edt_rtsp_url.setPlaceholderText("rtsp://user:pass@ip:554/... (overrides fields above if set)")
        self.cmb_rtsp_transport = QComboBox()
        self.cmb_rtsp_transport.addItems(["tcp", "udp"])
        self.spn_rtsp_timeout = QSpinBox()
        self.spn_rtsp_timeout.setRange(500, 60000)
        self.spn_rtsp_timeout.setSuffix(" ms")
        f.addRow("IP", self.edt_rtsp_ip)
        f.addRow("Port", self.spn_rtsp_port)
        f.addRow("Username", self.edt_rtsp_user)
        f.addRow("Password", self.edt_rtsp_pass)
        f.addRow("Path", self.edt_rtsp_path)
        f.addRow("RTSP URL", self.edt_rtsp_url)
        f.addRow("Transport", self.cmb_rtsp_transport)
        f.addRow("Timeout", self.spn_rtsp_timeout)
        self.lbl_rtsp_preview = QLabel("")
        self.lbl_rtsp_preview.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt;")
        self.lbl_rtsp_preview.setWordWrap(True)
        f.addRow(self.lbl_rtsp_preview)
        for wdg in (self.edt_rtsp_ip, self.edt_rtsp_user, self.edt_rtsp_pass, self.edt_rtsp_path, self.edt_rtsp_url):
            wdg.textChanged.connect(self._update_rtsp_preview)
        self.spn_rtsp_port.valueChanged.connect(self._update_rtsp_preview)
        self.btn_rtsp_test = _btn("Test Connection")
        self.btn_rtsp_test.clicked.connect(lambda: (self._apply(), self.rtsp_test_requested.emit()))
        f.addRow(self.btn_rtsp_test)
        return w

    def _build_industrial(self) -> QWidget:
        w = QGroupBox("INDUSTRIAL CAMERA")
        f = QFormLayout(w)
        hint = QLabel("GigE Vision / USB3 Vision / GenICam")
        hint.setProperty("class", "hint")
        f.addRow(hint)
        row = QHBoxLayout()
        self.cmb_ind_devices = QComboBox()
        self.cmb_ind_devices.currentIndexChanged.connect(self._ind_device_picked)
        self.btn_ind_scan = _btn("Scan devices")
        self.btn_ind_scan.clicked.connect(lambda: self.scan_requested.emit(self.current_type().value))
        row.addWidget(self.cmb_ind_devices, 1)
        row.addWidget(self.btn_ind_scan)
        f.addRow("Devices", row)
        self.edt_ind_serial = QLineEdit()
        self.edt_ind_serial.setPlaceholderText("empty = first device")
        f.addRow("Serial Number", self.edt_ind_serial)
        self.spn_ind_exposure = QDoubleSpinBox()
        self.spn_ind_exposure.setRange(1.0, 10_000_000.0)
        self.spn_ind_exposure.setSuffix(" us")
        self.spn_ind_exposure.setDecimals(0)
        f.addRow("Exposure Time", self.spn_ind_exposure)
        self.spn_ind_gain = QDoubleSpinBox()
        self.spn_ind_gain.setRange(0.0, 100.0)
        self.spn_ind_gain.setSuffix(" dB")
        f.addRow("Gain", self.spn_ind_gain)
        self.spn_ind_fps = QDoubleSpinBox()
        self.spn_ind_fps.setRange(0.0, 1000.0)
        self.spn_ind_fps.setSpecialValueText("camera default")
        f.addRow("Frame Rate", self.spn_ind_fps)
        self.cmb_ind_trigger = QComboBox()
        self.cmb_ind_trigger.addItems(["off", "on"])
        f.addRow("Trigger Mode", self.cmb_ind_trigger)
        self.spn_ind_timeout = QSpinBox()
        self.spn_ind_timeout.setRange(100, 60000)
        self.spn_ind_timeout.setSuffix(" ms")
        f.addRow("Grab timeout", self.spn_ind_timeout)
        crow = QHBoxLayout()
        self.edt_cti = QLineEdit()
        self.edt_cti.setPlaceholderText("GenICam only: path to GenTL producer .cti")
        self.btn_cti = _btn("Browse...")
        self.btn_cti.clicked.connect(self._browse_cti)
        crow.addWidget(self.edt_cti, 1)
        crow.addWidget(self.btn_cti)
        f.addRow("CTI file", crow)
        return w

    # ------------------------------------------------------------------ handlers
    def current_type(self) -> CameraType:
        try:
            return CameraType(self.cmb_type.currentData())
        except ValueError:
            return CameraType.USB

    def _on_type_changed(self) -> None:
        ct = self.current_type()
        page = {CameraType.USB: 0, CameraType.VIDEO: 1, CameraType.RTSP: 2}.get(ct, 3)
        for i in range(self.stack.count()):
            # only the visible page may claim vertical space, otherwise every page is as tall
            # as the tallest one and the card below it is pushed off screen
            self.stack.widget(i).setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred if i == page else QSizePolicy.Policy.Ignored)
        self.stack.setCurrentIndex(page)
        self.stack.adjustSize()
        ok, status = self._availability.get(ct, (True, "OK"))
        if ok:
            self.lbl_sdk.setText("" if ct in (CameraType.USB, CameraType.VIDEO, CameraType.RTSP) else "SDK detected.")
            self.lbl_sdk.setStyleSheet(f"color: {COLOR_OK}; font-size: 9pt;")
        else:
            self.lbl_sdk.setText(f"{status}. You can still save the configuration; connecting will report the missing SDK.")
            self.lbl_sdk.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 9pt;")
        self.edt_cti.setEnabled(ct == CameraType.GENICAM)
        self.btn_cti.setEnabled(ct == CameraType.GENICAM)

    def _usb_device_picked(self, idx: int) -> None:
        data = self.cmb_usb_devices.itemData(idx)
        if data is not None:
            self.spn_usb_index.setValue(int(data))

    def _usb_res_picked(self, idx: int) -> None:
        wh = self.cmb_usb_res.itemData(idx)
        custom = wh is None
        self.spn_usb_w.setEnabled(custom)
        self.spn_usb_h.setEnabled(custom)
        if wh:
            self.spn_usb_w.setValue(wh[0])
            self.spn_usb_h.setValue(wh[1])

    def _ind_device_picked(self, idx: int) -> None:
        data = self.cmb_ind_devices.itemData(idx)
        if data:
            self.edt_ind_serial.setText(str(data))

    def _rtsp_preset_picked(self, idx: int) -> None:
        key = self.cmb_rtsp_preset.itemData(idx)
        preset = RTSP_PRESETS.get(key)
        if preset:
            self.edt_rtsp_path.setText(preset["path"])
            self.spn_rtsp_port.setValue(int(preset["port"]))

    def _update_rtsp_preview(self) -> None:
        cfg = self._read_rtsp()
        self.lbl_rtsp_preview.setText("URL: " + mask_url(build_rtsp_url(cfg)))

    def _browse_video(self) -> None:
        pattern = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Select video file", "", f"Video files ({pattern});;All files (*)")
        if path:
            self.edt_video_path.setText(path)

    def _browse_cti(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select GenTL producer", "", "GenTL producer (*.cti);;All files (*)")
        if path:
            self.edt_cti.setText(path)

    # ------------------------------------------------------------------ external updates
    def set_devices(self, camera_type: str, devices: List[DeviceDescriptor]) -> None:
        combo = self.cmb_usb_devices if camera_type == CameraType.USB.value else self.cmb_ind_devices
        combo.blockSignals(True)
        combo.clear()
        if not devices:
            combo.addItem("no devices found", None)
        for d in devices:
            combo.addItem(d.display_name, d.identifier)
        combo.blockSignals(False)
        self.set_status(f"Scan: {len(devices)} device(s) found", ok=bool(devices))

    def set_status(self, text: str, ok: bool | None = None) -> None:
        color = COLOR_TEXT_DIM if ok is None else (COLOR_OK if ok else COLOR_ERROR)
        self.lbl_status.setStyleSheet(f"color: {color};")
        self.lbl_status.setText(text)

    def set_camera_state(self, state: str) -> None:
        connected = state in ("CONNECTED", "STREAMING", "FINISHED", "RECONNECTING")
        streaming = state in ("STREAMING", "RECONNECTING")
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected or state == "LOST")
        self.btn_start.setEnabled(not streaming)
        self.btn_stop.setEnabled(streaming or state == "FINISHED")
        self.cmb_type.setEnabled(not connected)
        self.cmb_type.setToolTip("Disconnect the camera first to change its type" if connected else "")

    def set_video_position(self, current: int, total: int) -> None:
        self.lbl_video_pos.setText(f"{current} / {total}")
        if total > 0 and not self.sld_video.isSliderDown():
            self.sld_video.setValue(int(current / total * 1000))

    # ------------------------------------------------------------------ config <-> widgets
    def set_config(self, cfg: CameraConfig) -> None:
        self._config = deepcopy(cfg)
        idx = self.cmb_type.findData(cfg.camera_type)
        self.cmb_type.setCurrentIndex(max(0, idx))
        self._on_type_changed()
        u = cfg.usb
        self.spn_usb_index.setValue(u.device_index)
        preset = self.cmb_usb_res.findData((u.width, u.height))
        self.cmb_usb_res.setCurrentIndex(preset if preset >= 0 else self.cmb_usb_res.count() - 1)
        self.spn_usb_w.setValue(u.width)
        self.spn_usb_h.setValue(u.height)
        self.spn_usb_fps.setValue(u.fps)
        self.cmb_usb_backend.setCurrentText(u.backend)
        v = cfg.video
        self.edt_video_path.setText(v.path)
        self.chk_video_loop.blockSignals(True)
        self.chk_video_loop.setChecked(v.loop)
        self.chk_video_loop.blockSignals(False)
        self.chk_video_realtime.setChecked(v.realtime)
        r = cfg.rtsp
        pi = self.cmb_rtsp_preset.findData(r.vendor_preset)
        self.cmb_rtsp_preset.blockSignals(True)
        self.cmb_rtsp_preset.setCurrentIndex(max(0, pi))
        self.cmb_rtsp_preset.blockSignals(False)
        self.edt_rtsp_ip.setText(r.ip)
        self.spn_rtsp_port.setValue(r.port)
        self.edt_rtsp_user.setText(r.username)
        self.edt_rtsp_pass.setText(r.password)
        self.edt_rtsp_path.setText(r.path)
        self.edt_rtsp_url.setText(r.url)
        self.cmb_rtsp_transport.setCurrentText(r.transport)
        self.spn_rtsp_timeout.setValue(r.read_timeout_ms)
        i = cfg.industrial
        self.edt_ind_serial.setText(i.serial_number)
        self.spn_ind_exposure.setValue(i.exposure_us)
        self.spn_ind_gain.setValue(i.gain)
        self.spn_ind_fps.setValue(i.frame_rate)
        self.cmb_ind_trigger.setCurrentText(i.trigger_mode)
        self.spn_ind_timeout.setValue(i.grab_timeout_ms)
        self.edt_cti.setText(i.cti_path)
        rc = cfg.reconnect
        self.chk_reconnect.setChecked(rc.enabled)
        self.spn_frame_timeout.setValue(rc.frame_timeout_s)
        self.edt_delays.setText(", ".join(f"{d:g}" for d in rc.delays_s))
        self._update_rtsp_preview()

    def _read_rtsp(self):
        r = deepcopy(self._config.rtsp)
        r.ip = self.edt_rtsp_ip.text().strip()
        r.port = self.spn_rtsp_port.value()
        r.username = self.edt_rtsp_user.text()
        r.password = self.edt_rtsp_pass.text()
        r.path = self.edt_rtsp_path.text().strip() or "/"
        r.url = self.edt_rtsp_url.text().strip()
        r.transport = self.cmb_rtsp_transport.currentText()
        r.read_timeout_ms = self.spn_rtsp_timeout.value()
        r.open_timeout_ms = self.spn_rtsp_timeout.value()
        r.vendor_preset = str(self.cmb_rtsp_preset.currentData())
        return r

    def get_config(self) -> CameraConfig:
        cfg = deepcopy(self._config)
        cfg.camera_type = self.current_type().value
        cfg.usb.device_index = self.spn_usb_index.value()
        cfg.usb.width = self.spn_usb_w.value()
        cfg.usb.height = self.spn_usb_h.value()
        cfg.usb.fps = self.spn_usb_fps.value()
        cfg.usb.backend = self.cmb_usb_backend.currentText()
        cfg.video.path = self.edt_video_path.text().strip()
        cfg.video.loop = self.chk_video_loop.isChecked()
        cfg.video.realtime = self.chk_video_realtime.isChecked()
        cfg.rtsp = self._read_rtsp()
        cfg.industrial.serial_number = self.edt_ind_serial.text().strip()
        cfg.industrial.exposure_us = self.spn_ind_exposure.value()
        cfg.industrial.gain = self.spn_ind_gain.value()
        cfg.industrial.frame_rate = self.spn_ind_fps.value()
        cfg.industrial.trigger_mode = self.cmb_ind_trigger.currentText()
        cfg.industrial.grab_timeout_ms = self.spn_ind_timeout.value()
        cfg.industrial.cti_path = self.edt_cti.text().strip()
        cfg.reconnect.enabled = self.chk_reconnect.isChecked()
        cfg.reconnect.frame_timeout_s = self.spn_frame_timeout.value()
        delays: List[float] = []
        for tok in self.edt_delays.text().replace(";", ",").split(","):
            tok = tok.strip()
            if tok:
                try:
                    delays.append(max(0.2, float(tok)))
                except ValueError:
                    pass
        cfg.reconnect.delays_s = delays or [1.0, 2.0, 5.0]
        return cfg

    def _apply(self) -> None:
        self._config = self.get_config()
        self.config_applied.emit(deepcopy(self._config))

    def apply_now(self) -> None:
        """Push the current form values to the controller (used by the quick bar)."""
        self._apply()
