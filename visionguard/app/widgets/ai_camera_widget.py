"""AI Camera configuration page: network, RTSP video and the AI event channel."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ...camera.rtsp_camera import build_rtsp_url, mask_url
from ...config.schemas import AiCameraConfig, CameraBrand, EventProviderType
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM, COLOR_TEXT_MUTED


class AiCameraWidget(QWidget):
    """Everything an AI camera needs: one address, two channels (video + events)."""

    test_camera_requested = Signal()
    test_rtsp_requested = Signal()
    test_event_requested = Signal()

    def __init__(self, config: AiCameraConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = deepcopy(config)
        self._build()
        self.set_config(config)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # ---------------------------------------------------------- network
        g_net = QGroupBox("CAMERA NETWORK")
        f = QFormLayout(g_net)
        self.edt_ip = QLineEdit()
        self.spn_http = QSpinBox()
        self.spn_http.setRange(1, 65535)
        self.spn_rtsp = QSpinBox()
        self.spn_rtsp.setRange(1, 65535)
        self.edt_user = QLineEdit()
        self.edt_pass = QLineEdit()
        self.edt_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.chk_https = QCheckBox("Use HTTPS")
        f.addRow("IP Address", self.edt_ip)
        f.addRow("HTTP Port", self.spn_http)
        f.addRow("RTSP Port", self.spn_rtsp)
        f.addRow("Username", self.edt_user)
        f.addRow("Password", self.edt_pass)
        f.addRow(self.chk_https)
        lay.addWidget(g_net)

        # ---------------------------------------------------------- video
        g_video = QGroupBox("VIDEO (RTSP)")
        fv = QFormLayout(g_video)
        self.edt_channel = QLineEdit()
        self.edt_channel.setPlaceholderText("101 = main stream, 102 = sub stream")
        self.edt_rtsp_url = QLineEdit()
        self.edt_rtsp_url.setPlaceholderText("rtsp://... (overrides the fields above when set)")
        self.cmb_transport = QComboBox()
        self.cmb_transport.addItems(["tcp", "udp"])
        fv.addRow("Stream channel", self.edt_channel)
        fv.addRow("RTSP URL", self.edt_rtsp_url)
        fv.addRow("Transport", self.cmb_transport)
        self.lbl_rtsp_preview = QLabel("")
        self.lbl_rtsp_preview.setWordWrap(True)
        self.lbl_rtsp_preview.setProperty("class", "hint")
        fv.addRow(self.lbl_rtsp_preview)
        lay.addWidget(g_video)

        # ---------------------------------------------------------- events
        g_event = QGroupBox("AI EVENT CHANNEL")
        fe = QFormLayout(g_event)
        self.cmb_provider = QComboBox()
        for provider in EventProviderType:
            self.cmb_provider.addItem(provider.label, provider.value)
        self.cmb_provider.currentIndexChanged.connect(self._provider_changed)
        self.edt_event_url = QLineEdit()
        self.edt_event_url.setPlaceholderText("/ISAPI/Event/notification/alertStream (default)")
        self.spn_event_timeout = QDoubleSpinBox()
        self.spn_event_timeout.setRange(0.5, 60.0)
        self.spn_event_timeout.setSuffix(" s")
        self.spn_health = QDoubleSpinBox()
        self.spn_health.setRange(5.0, 600.0)
        self.spn_health.setSuffix(" s")
        fe.addRow("Event provider", self.cmb_provider)
        fe.addRow("Event API path", self.edt_event_url)
        fe.addRow("Read timeout", self.spn_event_timeout)
        fe.addRow("Health check every", self.spn_health)
        self.lbl_provider_hint = QLabel("")
        self.lbl_provider_hint.setWordWrap(True)
        self.lbl_provider_hint.setProperty("class", "hint")
        fe.addRow(self.lbl_provider_hint)
        lay.addWidget(g_event)

        # ---------------------------------------------------------- event logic
        g_logic = QGroupBox("EVENT LOGIC")
        fl = QFormLayout(g_logic)
        self.spn_on = QSpinBox()
        self.spn_on.setRange(0, 60000)
        self.spn_on.setSuffix(" ms")
        self.spn_on.setSingleStep(50)
        self.spn_off = QSpinBox()
        self.spn_off.setRange(0, 600000)
        self.spn_off.setSuffix(" ms")
        self.spn_off.setSingleStep(100)
        self.spn_clear = QDoubleSpinBox()
        self.spn_clear.setRange(0.0, 300.0)
        self.spn_clear.setSuffix(" s")
        self.spn_clear.setSpecialValueText("disabled")
        self.spn_line_hold = QDoubleSpinBox()
        self.spn_line_hold.setRange(0.0, 60.0)
        self.spn_line_hold.setSuffix(" s")
        self.spn_line_hold.setSpecialValueText("ignore")
        self.chk_count = QCheckBox("Count people (region enter / exit)")
        self.chk_strict = QCheckBox("Only accept events with target = human")
        self.chk_fault_event = QCheckBox("Event channel lost = FAULT")
        self.chk_fault_video = QCheckBox("Video lost = FAULT")
        fl.addRow("ON delay", self.spn_on)
        fl.addRow("OFF delay", self.spn_off)
        fl.addRow("Clear timeout", self.spn_clear)
        fl.addRow("Line cross hold", self.spn_line_hold)
        fl.addRow(self.chk_count)
        fl.addRow(self.chk_strict)
        fl.addRow(self.chk_fault_event)
        fl.addRow(self.chk_fault_video)
        hint = QLabel("Clear timeout releases a zone when the camera never sends the 'inactive' alarm. "
                      "A missed exit keeps the zone OCCUPIED - it never fakes a CLEAR.")
        hint.setWordWrap(True)
        hint.setProperty("class", "hint")
        fl.addRow(hint)
        lay.addWidget(g_logic)

        # ---------------------------------------------------------- tests
        g_test = QGroupBox("TEST")
        gl = QGridLayout(g_test)
        self.btn_test_camera = QPushButton("Test Camera")
        self.btn_test_rtsp = QPushButton("Test RTSP")
        self.btn_test_event = QPushButton("Test Event")
        self.btn_test_camera.setToolTip("Doc thong tin thiet bi qua ISAPI/HTTP de kiem tra IP va mat khau")
        self.btn_test_rtsp.setToolTip("Mo thu luong video RTSP")
        self.btn_test_event.setToolTip("Mo kenh su kien vai giay va bao lai nhung gi camera gui")
        gl.addWidget(self.btn_test_camera, 0, 0)
        gl.addWidget(self.btn_test_rtsp, 0, 1)
        gl.addWidget(self.btn_test_event, 0, 2)
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        gl.addWidget(self.lbl_test, 1, 0, 1, 3)
        lay.addWidget(g_test)

        for w in (self.edt_ip, self.edt_user, self.edt_pass, self.edt_rtsp_url, self.edt_channel):
            w.textChanged.connect(self._update_preview)
        self.spn_rtsp.valueChanged.connect(self._update_preview)
        self.btn_test_camera.clicked.connect(self.test_camera_requested)
        self.btn_test_rtsp.clicked.connect(self.test_rtsp_requested)
        self.btn_test_event.clicked.connect(self.test_event_requested)

        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)

    # ------------------------------------------------------------------ helpers
    def _provider_changed(self) -> None:
        provider = str(self.cmb_provider.currentData())
        hints = {
            EventProviderType.ISAPI.value:
                "Hikvision: enable an AI rule (intrusion / region entrance) and tick "
                "'Notify surveillance center' in Linkage Method, then Test Event.",
            EventProviderType.DAHUA_HTTP.value:
                "Dahua: IVS rule with 'Human' target; the PC attaches to eventManager.cgi.",
            EventProviderType.GENERIC_HTTP.value:
                "Generic: give the full path of an endpoint that streams or returns XML/JSON alarms.",
            EventProviderType.MOCK.value:
                "Simulated: no camera is contacted. Use the buttons in the AI Event tab to inject "
                "person enter / exit events and demo the whole chain.",
        }
        self.lbl_provider_hint.setText(hints.get(provider, ""))
        simulated = provider == EventProviderType.MOCK.value
        for w in (self.edt_event_url, self.spn_event_timeout, self.spn_health,
                  self.btn_test_camera, self.btn_test_event):
            w.setEnabled(not simulated)

    def _update_preview(self) -> None:
        cfg = self.get_config()
        self.lbl_rtsp_preview.setText("Video URL: " + mask_url(build_rtsp_url(cfg.to_rtsp_config())))

    def set_test_result(self, text: str, ok: bool | None = None) -> None:
        color = COLOR_TEXT_DIM if ok is None else (COLOR_OK if ok else COLOR_ERROR)
        self.lbl_test.setStyleSheet(f"color: {color};")
        self.lbl_test.setText(text)

    def set_brand(self, brand: CameraBrand) -> None:
        """Preselect the event provider that matches the brand (only when still at its default)."""
        default = {CameraBrand.HIKVISION: EventProviderType.ISAPI,
                   CameraBrand.DAHUA: EventProviderType.DAHUA_HTTP}.get(brand)
        if default is None:
            return
        idx = self.cmb_provider.findData(default.value)
        if idx >= 0:
            self.cmb_provider.setCurrentIndex(idx)

    # ------------------------------------------------------------------ config <-> widgets
    def set_config(self, cfg: AiCameraConfig) -> None:
        self._config = deepcopy(cfg)
        self.edt_ip.setText(cfg.ip)
        self.spn_http.setValue(cfg.http_port)
        self.spn_rtsp.setValue(cfg.rtsp_port)
        self.edt_user.setText(cfg.username)
        self.edt_pass.setText(cfg.password)
        self.chk_https.setChecked(cfg.use_https)
        self.edt_channel.setText(cfg.rtsp_channel)
        self.edt_rtsp_url.setText(cfg.rtsp_url)
        self.cmb_transport.setCurrentText(cfg.rtsp_transport)
        idx = self.cmb_provider.findData(cfg.event_provider)
        self.cmb_provider.setCurrentIndex(max(0, idx))
        self.edt_event_url.setText(cfg.event_url)
        self.spn_event_timeout.setValue(cfg.event_timeout_s)
        self.spn_health.setValue(cfg.health_interval_s)
        lg = cfg.logic
        self.spn_on.setValue(lg.on_delay_ms)
        self.spn_off.setValue(lg.off_delay_ms)
        self.spn_clear.setValue(lg.clear_timeout_s)
        self.spn_line_hold.setValue(lg.line_cross_hold_s)
        self.chk_count.setChecked(lg.use_person_count)
        self.chk_strict.setChecked(lg.strict_human_only)
        self.chk_fault_event.setChecked(lg.fault_on_event_loss)
        self.chk_fault_video.setChecked(lg.fault_on_video_loss)
        self._provider_changed()
        self._update_preview()

    def get_config(self) -> AiCameraConfig:
        cfg = deepcopy(self._config)
        cfg.ip = self.edt_ip.text().strip()
        cfg.http_port = self.spn_http.value()
        cfg.rtsp_port = self.spn_rtsp.value()
        cfg.username = self.edt_user.text()
        cfg.password = self.edt_pass.text()
        cfg.use_https = self.chk_https.isChecked()
        cfg.rtsp_channel = self.edt_channel.text().strip() or "101"
        cfg.rtsp_url = self.edt_rtsp_url.text().strip()
        cfg.rtsp_transport = self.cmb_transport.currentText()
        cfg.event_provider = str(self.cmb_provider.currentData())
        cfg.event_url = self.edt_event_url.text().strip()
        cfg.event_timeout_s = round(self.spn_event_timeout.value(), 2)
        cfg.health_interval_s = round(self.spn_health.value(), 1)
        lg = cfg.logic
        lg.on_delay_ms = self.spn_on.value()
        lg.off_delay_ms = self.spn_off.value()
        lg.clear_timeout_s = round(self.spn_clear.value(), 1)
        lg.line_cross_hold_s = round(self.spn_line_hold.value(), 1)
        lg.use_person_count = self.chk_count.isChecked()
        lg.strict_human_only = self.chk_strict.isChecked()
        lg.fault_on_event_loss = self.chk_fault_event.isChecked()
        lg.fault_on_video_loss = self.chk_fault_video.isChecked()
        return cfg
