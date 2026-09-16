"""AI Camera configuration: what an operator needs first, everything else behind Advanced.

Visible by default: IP, user, password, event provider, the two debounce delays and the three
test buttons - enough to bring a Hikvision AcuSense camera online. Ports, stream paths,
timeouts and fail-safe details only appear when 'Advanced settings' is ticked.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ...camera.rtsp_camera import build_rtsp_url, mask_url
from ...config.schemas import AiCameraConfig, CameraBrand, EventProviderType
from ..theme import COLOR_ERROR, COLOR_OK, COLOR_TEXT_DIM
from .form_helpers import AdvancedSection, hint


class AiCameraWidget(QWidget):
    """Everything an AI camera needs: one address, two channels (video + events)."""

    test_camera_requested = Signal()
    test_rtsp_requested = Signal()
    test_event_requested = Signal()

    def __init__(self, config: AiCameraConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = deepcopy(config)
        self._brand = CameraBrand.HIKVISION
        self.advanced = AdvancedSection()
        self._build()
        self.set_config(config)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # ---------------------------------------------------------- camera address
        g_net = QGroupBox("CAMERA")
        f = QFormLayout(g_net)
        self.edt_ip = QLineEdit()
        self.edt_ip.setPlaceholderText("192.168.1.64")
        self.edt_user = QLineEdit()
        self.edt_pass = QLineEdit()
        self.edt_pass.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("IP Address", self.edt_ip)
        f.addRow("Username", self.edt_user)
        f.addRow("Password", self.edt_pass)

        self.spn_http = QSpinBox()
        self.spn_http.setRange(1, 65535)
        self.spn_rtsp = QSpinBox()
        self.spn_rtsp.setRange(1, 65535)
        self.chk_https = QCheckBox("Use HTTPS")
        f.addRow("HTTP Port", self.spn_http)
        f.addRow("RTSP Port", self.spn_rtsp)
        f.addRow("", self.chk_https)
        self.advanced.rows(f, self.spn_http, self.spn_rtsp, self.chk_https)
        f.addRow(hint("Tài khoản phải có quyền xem từ xa. Cổng mặc định 80 (HTTP) và 554 (RTSP)."))
        lay.addWidget(g_net)

        # ---------------------------------------------------------- video (advanced only)
        g_video = QGroupBox("VIDEO (RTSP)")
        fv = QFormLayout(g_video)
        self.edt_channel = QLineEdit()
        self.edt_channel.setPlaceholderText("101 = luồng chính, 102 = luồng phụ")
        self.edt_rtsp_url = QLineEdit()
        self.edt_rtsp_url.setPlaceholderText("rtsp://...  (chỉ điền khi muốn ghi đè)")
        self.cmb_transport = QComboBox()
        self.cmb_transport.addItems(["tcp", "udp"])
        fv.addRow("Stream channel", self.edt_channel)
        fv.addRow("RTSP URL", self.edt_rtsp_url)
        fv.addRow("Transport", self.cmb_transport)
        self.lbl_rtsp_preview = QLabel("")
        self.lbl_rtsp_preview.setWordWrap(True)
        self.lbl_rtsp_preview.setProperty("class", "hint")
        fv.addRow(self.lbl_rtsp_preview)
        self.advanced.widget(g_video)
        lay.addWidget(g_video)

        # ---------------------------------------------------------- event channel
        g_event = QGroupBox("AI EVENT CHANNEL")
        fe = QFormLayout(g_event)
        self.cmb_provider = QComboBox()
        for provider in EventProviderType:
            self.cmb_provider.addItem(provider.label, provider.value)
        self.cmb_provider.currentIndexChanged.connect(self._provider_changed)
        fe.addRow("Event provider", self.cmb_provider)
        self.edt_event_url = QLineEdit()
        self.edt_event_url.setPlaceholderText("/ISAPI/Event/notification/alertStream (mặc định)")
        self.spn_event_timeout = QDoubleSpinBox()
        self.spn_event_timeout.setRange(0.5, 60.0)
        self.spn_event_timeout.setSuffix(" s")
        self.spn_health = QDoubleSpinBox()
        self.spn_health.setRange(5.0, 600.0)
        self.spn_health.setSuffix(" s")
        fe.addRow("Event API path", self.edt_event_url)
        fe.addRow("Read timeout", self.spn_event_timeout)
        fe.addRow("Health check every", self.spn_health)
        self.advanced.rows(fe, self.edt_event_url, self.spn_event_timeout, self.spn_health)
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
        self.spn_on.setToolTip("Camera phải báo liên tục bao lâu thì mới bật bit PLC")
        self.spn_off = QSpinBox()
        self.spn_off.setRange(0, 600000)
        self.spn_off.setSuffix(" ms")
        self.spn_off.setSingleStep(100)
        self.spn_off.setToolTip("Vùng phải im bao lâu thì mới tắt bit PLC")
        fl.addRow("ON delay", self.spn_on)
        fl.addRow("OFF delay", self.spn_off)
        fl.addRow(hint("ON delay: bao lâu mới báo CÓ NGƯỜI.  OFF delay: bao lâu mới báo HẾT NGƯỜI. "
                       "Tăng lên nếu bit PLC nhấp nháy."))

        self.spn_clear = QDoubleSpinBox()
        self.spn_clear.setRange(0.0, 300.0)
        self.spn_clear.setSuffix(" s")
        self.spn_clear.setSpecialValueText("tắt")
        self.spn_clear.setToolTip("Camera không gửi 'inactive' thì sau bao lâu coi như hết người")
        self.spn_line_hold = QDoubleSpinBox()
        self.spn_line_hold.setRange(0.0, 60.0)
        self.spn_line_hold.setSuffix(" s")
        self.spn_line_hold.setSpecialValueText("bỏ qua")
        self.spn_line_hold.setToolTip("Sự kiện băng qua vạch không có lúc kết thúc, giữ OCCUPIED bao lâu")
        self.chk_count = QCheckBox("Đếm người theo sự kiện vào / ra vùng")
        self.chk_strict = QCheckBox("Chỉ nhận sự kiện có target = human")
        self.chk_fault_event = QCheckBox("Mất kênh sự kiện = FAULT")
        self.chk_fault_video = QCheckBox("Mất hình RTSP = FAULT")
        fl.addRow("Clear timeout", self.spn_clear)
        fl.addRow("Line cross hold", self.spn_line_hold)
        fl.addRow("", self.chk_count)
        fl.addRow("", self.chk_strict)
        fl.addRow("", self.chk_fault_event)
        fl.addRow("", self.chk_fault_video)
        self.advanced.rows(fl, self.spn_clear, self.spn_line_hold, self.chk_count, self.chk_strict,
                           self.chk_fault_event, self.chk_fault_video)
        lay.addWidget(g_logic)

        # ---------------------------------------------------------- tests
        g_test = QGroupBox("TEST")
        gl = QGridLayout(g_test)
        self.btn_test_camera = QPushButton("Test Camera")
        self.btn_test_rtsp = QPushButton("Test RTSP")
        self.btn_test_event = QPushButton("Test Event")
        self.btn_test_camera.setToolTip("Đọc thông tin thiết bị để kiểm tra IP, user và mật khẩu")
        self.btn_test_rtsp.setToolTip("Mở thử luồng video RTSP")
        self.btn_test_event.setToolTip("Nghe kênh sự kiện vài giây và báo lại camera gửi gì")
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
        self.advanced.set_visible(False)

    # ------------------------------------------------------------------ helpers
    def set_advanced_visible(self, visible: bool) -> None:
        self.advanced.set_visible(visible)

    def _provider_changed(self) -> None:
        provider = str(self.cmb_provider.currentData())
        hints = {
            EventProviderType.ISAPI.value:
                "Hikvision: bật rule AI (Intrusion / Region Entrance) trên camera và tick "
                "'Notify Surveillance Center', sau đó bấm Test Event.",
            EventProviderType.DAHUA_HTTP.value:
                "Dahua: bật rule IVS với target Human; PC sẽ attach vào eventManager.cgi.",
            EventProviderType.GENERIC_HTTP.value:
                "Generic: điền đường dẫn đầy đủ của endpoint trả về XML/JSON cảnh báo.",
            EventProviderType.MOCK.value:
                "Mô phỏng: không kết nối camera nào. Dùng tab Simulate để tự tạo sự kiện người "
                "vào / ra và chạy thử cả chuỗi.",
        }
        self.lbl_provider_hint.setText(hints.get(provider, ""))
        simulated = provider == EventProviderType.MOCK.value
        for w in (self.edt_event_url, self.spn_event_timeout, self.spn_health,
                  self.btn_test_camera, self.btn_test_event, self.btn_test_rtsp):
            w.setEnabled(not simulated)

    def _update_preview(self) -> None:
        cfg = self.get_config()
        url = mask_url(build_rtsp_url(cfg.to_rtsp_config(self._brand.value)))
        self.lbl_rtsp_preview.setText("Video URL: " + url)

    def set_test_result(self, text: str, ok: bool | None = None) -> None:
        color = COLOR_TEXT_DIM if ok is None else (COLOR_OK if ok else COLOR_ERROR)
        self.lbl_test.setStyleSheet(f"color: {color};")
        self.lbl_test.setText(text)

    def set_brand_display(self, brand: CameraBrand) -> None:
        """Remember the brand for the RTSP path template, without touching the provider."""
        self._brand = brand
        self._update_preview()

    def set_brand(self, brand: CameraBrand) -> None:
        """User picked a brand: update the path template and preselect its usual provider."""
        self.set_brand_display(brand)
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
        self.advanced.apply()

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
