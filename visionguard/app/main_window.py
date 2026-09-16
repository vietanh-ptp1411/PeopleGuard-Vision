"""MainWindow: layout + signal wiring between widgets and the SystemController.

    ┌ app bar ── ◉ VisionGuard · Person-in-Area Monitoring · [AI CAMERA] ····· clock ─┐
    ├ command bar ─ START / STOP · PLC SIM │ AREA BANNER │ chips: camera · events · … ┤
    ├ alert strip ─ (hidden while nothing is wrong) ──────────────────────────────────┤
    │ ┌ live view card ───────────────────┐ ┌ side panel ──────────────────────────┐  │
    │ │ LIVE VIEW · source · fps          │ │ Overview / Camera / AI Events /      │  │
    │ │ image + ROI editor                │ │ AI Model / Zones / PLC / History     │  │
    │ │ camera + zone actions             │ │                                      │  │
    │ └───────────────────────────────────┘ └──────────────────────────────────────┘  │
    ├ system log ────────────────────────────────────────────────────────────────────┤
    └ status bar ─ message ······················· safety note ──────────────────────┘

The chips on the command bar carry what the old five-step workflow strip used to: each
one is a subsystem's health, and clicking it opens the tab that can fix it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ..camera.events.camera_event import CameraEvent
from ..config.config_manager import ConfigManager
from ..config.schemas import DetectionMode, EventProviderType
from ..logic.occupancy_state_machine import OccupancyState
from ..roi.roi_model import RoiType
from ..utils.logger import try_import_qt_handler
from ..workers.camera_worker import CameraState
from .controllers.system_controller import SystemController
from .theme import COLOR_TEXT_MUTED, status_caption, status_color
from .widgets.ai_config_widget import AIConfigWidget
from .widgets.camera_config_widget import CameraConfigWidget
from .widgets.chrome import (AlertStrip, AppBar, AreaBanner, ElidedLabel, StateChip, caption,
                            separator)
from .widgets.event_monitor_widget import EventMonitorWidget
from .widgets.events_widget import EventsWidget
from .widgets.log_widget import LogWidget
from .widgets.plc_config_widget import PlcConfigWidget
from .widgets.roi_panel import RoiPanel
from .widgets.status_panel import StatusPanel
from .widgets.video_view import PLACEHOLDER_AI_CAMERA, PLACEHOLDER_YOLO, VideoView

log = logging.getLogger("UI")

SAFETY_NOTE_SHORT = "Thiết bị giám sát - KHÔNG phải thiết bị an toàn đạt chuẩn"
SAFETY_NOTE = ("Monitoring system only - NOT a safety-rated protective device. "
               "Use certified safety PLC / sensors for personnel protection.")

TAB_STATUS, TAB_CAMERA, TAB_EVENT, TAB_AI, TAB_ROI, TAB_PLC, TAB_HISTORY = range(7)


def _button(text: str, cls: str = "", size: str = "", tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if cls:
        b.setProperty("class", cls)
    if size:
        b.setProperty("size", size)
    if tooltip:
        b.setToolTip(tooltip)
    return b


class MainWindow(QMainWindow):
    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.resize(1560, 950)
        self.ctrl = SystemController(config_manager, self)
        s = self.ctrl.settings

        # ---------------------------------------------------------- state mirrors (for the chips)
        self._camera_state = CameraState.DISCONNECTED
        self._model_loaded = False
        self._model_error = ""
        self._detecting = False
        self._plc_connected = False
        self._event_channel = "DISCONNECTED"
        self._event_message = ""
        self._ai_camera_mode = True
        self._system_state = "STOPPED"
        self._system_message = ""
        self._roi_states: Dict[str, OccupancyState] = {}

        # ---------------------------------------------------------- widgets
        self.video = VideoView(s.app.visualization, s.app.ui_fps_limit)
        self.status_panel = StatusPanel()
        self.camera_cfg = CameraConfigWidget(s.camera, self.ctrl.camera_manager.availability())
        self.ai_cfg = AIConfigWidget(s.ai)
        self.plc_cfg = PlcConfigWidget(s.plc)
        self.roi_panel = RoiPanel()
        self.events_widget = EventsWidget()
        self.event_monitor = EventMonitorWidget()
        self.log_widget = LogWidget()
        # the manual I/O screen lives inside the PLC tab: it is PLC testing, not a topic of its own
        self.io_test = self.plc_cfg.io_test

        self._build_layout()
        self._build_shortcuts()
        self._wire()
        self._attach_log_handler()

        self.video.set_rois(self.ctrl.roi_manager.all())
        self.roi_panel.set_rois(self.ctrl.roi_manager.all())
        self.events_widget.set_events(self.ctrl.events.recent(300), self.ctrl.events.count())
        self.log_widget.set_log_file(f"logs/{datetime.now():%Y-%m-%d}.log")
        self._apply_area("STOPPED")
        self.status_panel.set_heartbeat(None, s.plc.heartbeat.enabled)
        self.event_monitor.set_regions(self.ctrl.region_mapping.all())
        self.event_monitor.set_events(self.ctrl.camera_events(120))
        self.btn_sim.setChecked(s.plc.simulation_mode)
        self._style_sim_button(s.plc.simulation_mode)
        self._apply_detection_mode(s.camera.detection_mode)
        self.lbl_source.setText(s.camera.describe_source())
        self._update_window_title()
        self._update_camera_buttons(CameraState.DISCONNECTED)
        self._refresh_status()

    # ================================================================== chrome
    def _build_app_bar(self) -> QWidget:
        self.appbar = AppBar()
        clock = QTimer(self)
        clock.timeout.connect(self._update_clock)
        clock.start(1000)
        self._update_clock()
        return self.appbar

    def _build_command_bar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("class", "card")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(10)

        self.btn_start = _button("▶  START", "success", "xl", "Bắt đầu giám sát (F5)")
        self.btn_stop = _button("■  STOP", "danger", "xl", "Dừng giám sát (F6)")
        self.btn_stop.setEnabled(False)
        self.btn_start.setMinimumWidth(104)
        self.btn_stop.setMinimumWidth(98)
        lay.addWidget(self.btn_start)
        lay.addWidget(self.btn_stop)

        self.btn_sim = _button("PLC SIM", "", "",
                               "Bật: không cần PLC thật, mọi tín hiệu ghi vào bộ nhớ ảo của phần mềm")
        self.btn_sim.setCheckable(True)
        self.btn_sim.setMinimumHeight(38)
        self.btn_sim.setMinimumWidth(86)
        lay.addWidget(self.btn_sim)

        lay.addWidget(separator(length=38))

        self.banner = AreaBanner()
        lay.addWidget(self.banner)

        lay.addWidget(separator(length=38))

        self.chip_camera = StateChip("CAMERA")
        self.chip_detect = StateChip("AI EVENTS")
        self.chip_zones = StateChip("REGIONS")
        self.chip_plc = StateChip("PLC")
        for chip in (self.chip_camera, self.chip_detect, self.chip_zones, self.chip_plc):
            chip.setMaximumWidth(250)
            lay.addWidget(chip, 1)
        return bar

    def _build_shortcuts(self) -> None:
        act_start = QAction("Start system", self)
        act_start.setShortcut(QKeySequence("F5"))
        act_start.triggered.connect(self.ctrl.start_system)
        act_stop = QAction("Stop system", self)
        act_stop.setShortcut(QKeySequence("F6"))
        act_stop.triggered.connect(self.ctrl.stop_system)
        act_full = QAction("Toggle fullscreen", self)
        act_full.setShortcut(QKeySequence("F11"))
        act_full.triggered.connect(self.toggle_fullscreen)
        self.addActions([act_start, act_stop, act_full])

    def _style_sim_button(self, on: bool) -> None:
        self.btn_sim.setText("PLC SIM: ON" if on else "PLC SIM")
        self.appbar.set_simulation(on)

    # ================================================================== layout
    def _build_layout(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_app_bar())

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(10, 10, 10, 8)
        body_lay.setSpacing(9)
        body_lay.addWidget(self._build_command_bar())

        self.alert = AlertStrip()
        body_lay.addWidget(self.alert)

        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.addWidget(self._build_live_card())
        hsplit.addWidget(self._build_side_panel())
        hsplit.setStretchFactor(0, 3)
        hsplit.setStretchFactor(1, 1)
        hsplit.setSizes([1000, 500])
        hsplit.setChildrenCollapsible(False)

        log_card = QFrame()
        log_card.setProperty("class", "card")
        log_lay = QVBoxLayout(log_card)
        log_lay.setContentsMargins(2, 2, 2, 2)
        log_lay.addWidget(self.log_widget)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(hsplit)
        vsplit.addWidget(log_card)
        vsplit.setStretchFactor(0, 6)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([780, 150])
        vsplit.setChildrenCollapsible(False)
        body_lay.addWidget(vsplit, 1)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)

        # ---------------- status bar
        self.lbl_safety = QLabel("⚠  " + SAFETY_NOTE_SHORT)
        self.lbl_safety.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        self.lbl_safety.setToolTip(SAFETY_NOTE)
        self.statusBar().addPermanentWidget(self.lbl_safety)
        self.statusBar().showMessage("Sẵn sàng")

    def _build_live_card(self) -> QWidget:
        """The video, with a header that names the source and a footer that acts on it."""
        card = QFrame()
        card.setProperty("class", "card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QFrame()
        head.setProperty("class", "cardhead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 7, 12, 7)
        hl.setSpacing(10)
        hl.addWidget(caption("LIVE VIEW"))
        self.lbl_source = ElidedLabel("-")
        self.lbl_source.setProperty("class", "muted")
        hl.addWidget(self.lbl_source, 1)
        self.lbl_fps = QLabel("")
        self.lbl_fps.setProperty("class", "muted")
        hl.addWidget(self.lbl_fps)
        lay.addWidget(head)

        video_wrap = QWidget()
        video_wrap.setProperty("class", "transparent")
        wl = QVBoxLayout(video_wrap)
        wl.setContentsMargins(1, 0, 1, 0)
        wl.addWidget(self.video)
        lay.addWidget(video_wrap, 1)

        lay.addWidget(self._build_quick_bar())
        return card

    def _build_side_panel(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.addTab(self.status_panel, "Overview")
        self.tabs.addTab(self.camera_cfg, "Camera")
        self.tabs.addTab(self.event_monitor, "AI Events")
        self.tabs.addTab(self.ai_cfg, "AI Model")
        self.tabs.addTab(self.roi_panel, "Zones")
        self.tabs.addTab(self.plc_cfg, "PLC")
        self.tabs.addTab(self.events_widget, "History")
        self.tabs.setMinimumWidth(420)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setUsesScrollButtons(False)
        return self.tabs

    def _build_quick_bar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("class", "toolbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        lay.addWidget(caption("CAMERA"))
        self.btn_q_connect = _button("Connect", "", "sm", "Kết nối camera đang chọn ở tab Camera")
        self.btn_q_start = _button("Start", "success", "sm", "Bật luồng hình")
        self.btn_q_stop = _button("Stop", "", "sm", "Dừng luồng hình")
        for b in (self.btn_q_connect, self.btn_q_start, self.btn_q_stop):
            lay.addWidget(b)
        lay.addSpacing(6)
        lay.addWidget(separator())
        lay.addSpacing(6)

        lay.addWidget(caption("ZONES"))
        self.btn_q_add = _button("+ Zone", "primary", "sm", "Vẽ vùng giám sát: click từng điểm, double-click để đóng")
        self.btn_q_ex = _button("+ Exclusion", "", "sm", "Vẽ vùng loại trừ (người trong vùng này không tính)")
        self.btn_q_finish = _button("Finish", "", "sm", "Đóng polygon đang vẽ (Enter)")
        self.btn_q_edit = _button("Edit", "", "sm", "Kéo đỉnh để sửa vùng; Shift+click cạnh để thêm đỉnh")
        self.btn_q_edit.setCheckable(True)
        self.btn_q_save = _button("Save", "success", "sm", "Lưu cấu hình vùng ra config/roi_config.json")
        self.btn_q_finish.setEnabled(False)
        for b in (self.btn_q_add, self.btn_q_ex, self.btn_q_finish, self.btn_q_edit, self.btn_q_save):
            lay.addWidget(b)
        lay.addSpacing(10)

        self.lbl_video_hint = ElidedLabel("")
        self.lbl_video_hint.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        lay.addWidget(self.lbl_video_hint, 1)
        return bar

    def _attach_log_handler(self) -> None:
        handler_cls = try_import_qt_handler()
        if handler_cls is None:
            return
        self._qt_log_handler = handler_cls()
        self._qt_log_handler.setLevel(logging.INFO)
        self._qt_log_handler.emitter.message.connect(self.log_widget.append)
        logging.getLogger().addHandler(self._qt_log_handler)

    # ================================================================== wiring
    def _wire(self) -> None:
        c = self.ctrl
        self.btn_start.clicked.connect(c.start_system)
        self.btn_stop.clicked.connect(c.stop_system)
        self.btn_sim.toggled.connect(self._sim_toggled_toolbar)
        self.alert.action_clicked.connect(self._open_alert_tab)
        self.chip_camera.clicked.connect(lambda: self.tabs.setCurrentIndex(TAB_CAMERA))
        self.chip_detect.clicked.connect(
            lambda: self.tabs.setCurrentIndex(TAB_EVENT if self._ai_camera_mode else TAB_AI))
        self.chip_zones.clicked.connect(
            lambda: self.tabs.setCurrentIndex(TAB_EVENT if self._ai_camera_mode else TAB_ROI))
        self.chip_plc.clicked.connect(lambda: self.tabs.setCurrentIndex(TAB_PLC))

        # controller -> UI
        c.frame_ready.connect(self.video.set_frame)
        c.result_ready.connect(self._on_result)
        c.camera_state.connect(self._on_camera_state)
        c.camera_fps.connect(self._on_camera_fps)
        c.camera_info.connect(self._on_camera_info)
        c.devices_scanned.connect(self.camera_cfg.set_devices)
        c.video_position.connect(self.camera_cfg.set_video_position)
        c.model_status.connect(self._on_model_status)
        c.detection_state.connect(self._on_detection_state)
        c.camera_event.connect(self._on_camera_event)
        c.raw_camera_event.connect(self.event_monitor.add_raw)
        c.event_channel_state.connect(self._on_event_channel_state)
        c.zone_states_changed.connect(self._on_zone_states)
        c.detection_mode_changed.connect(self._apply_detection_mode)
        c.regions_changed.connect(self._on_regions_changed)
        c.plc_state.connect(self._on_plc_state)
        c.plc_latency.connect(self.status_panel.set_plc_latency)
        c.plc_memory.connect(self.plc_cfg.set_memory)
        c.heartbeat.connect(lambda v: self.status_panel.set_heartbeat(v, True))
        c.io_result.connect(self.io_test.append_result)
        c.io_result.connect(lambda ok, msg: self.plc_cfg.set_status(msg, ok))
        c.area_status.connect(self._apply_area)
        c.system_state.connect(self._on_system_state)
        c.message.connect(self._show_message)
        c.event_logged.connect(self.events_widget.add_event)
        c.rois_changed.connect(self._on_rois_changed)
        c.dropped_frames.connect(self.status_panel.set_dropped)

        # camera tab
        cc = self.camera_cfg
        cc.config_applied.connect(c.apply_camera_config)
        cc.config_applied.connect(lambda cfg: self.lbl_source.setText(cfg.describe_source()))
        cc.connect_requested.connect(c.camera_connect)
        cc.disconnect_requested.connect(c.camera_disconnect)
        cc.start_requested.connect(c.camera_start)
        cc.stop_requested.connect(c.camera_stop)
        cc.test_requested.connect(c.camera_test)
        cc.scan_requested.connect(c.scan_devices)
        cc.rtsp_test_requested.connect(c.rtsp_test)
        cc.video_command.connect(c.video_command)
        cc.detection_mode_changed.connect(self._mode_selected_in_tab)
        cc.ai_test_camera_requested.connect(c.test_ai_camera)
        cc.ai_test_event_requested.connect(lambda: c.test_event_channel(6.0))

        em = self.event_monitor
        em.simulate_requested.connect(c.simulate_event)
        em.region_changed.connect(c.update_region)
        em.region_delete_requested.connect(c.delete_region)
        em.regions_save_requested.connect(self._save_regions)
        em.connect_requested.connect(c.event_channel_connect)
        em.disconnect_requested.connect(c.event_channel_disconnect)

        # AI tab
        ac = self.ai_cfg
        ac.config_applied.connect(c.apply_ai_config)
        ac.load_model_requested.connect(c.load_model)
        ac.start_detection_requested.connect(c.start_detection)
        ac.stop_detection_requested.connect(c.stop_detection)

        # PLC tab
        pc = self.plc_cfg
        pc.config_applied.connect(self._plc_config_applied)
        pc.connect_requested.connect(c.plc_connect)
        pc.disconnect_requested.connect(c.plc_disconnect)
        pc.test_requested.connect(c.plc_test)
        pc.simulation_toggled.connect(self._sim_toggled_tab)

        # I/O test
        self.io_test.write_requested.connect(c.plc_manual_write)
        self.io_test.read_requested.connect(c.plc_manual_read)

        # events
        self.events_widget.refresh_requested.connect(
            lambda: self.events_widget.set_events(c.events.recent(300), c.events.count()))
        self.events_widget.export_requested.connect(
            lambda p: self._show_message(("Exported " + p) if c.events.export_csv(p) else "Export failed"))
        self.events_widget.clear_requested.connect(self._clear_events)

        # ROI panel + video editor
        rp, v = self.roi_panel, self.video
        rp.add_include_requested.connect(lambda: v.start_drawing(RoiType.INCLUDE))
        rp.add_exclude_requested.connect(lambda: v.start_drawing(RoiType.EXCLUDE))
        rp.finish_requested.connect(v.finish_drawing)
        rp.cancel_requested.connect(v.cancel_drawing)
        rp.edit_mode_toggled.connect(v.set_edit_mode)
        rp.delete_requested.connect(self._delete_roi)
        rp.clear_requested.connect(self._clear_rois)
        rp.save_requested.connect(self._save_rois)
        rp.selection_changed.connect(v.select_roi)
        rp.fields_changed.connect(c.update_roi_fields)
        v.roi_drawn.connect(c.add_roi)
        v.roi_points_changed.connect(c.update_roi_points)
        v.roi_selected.connect(rp.select)
        v.roi_delete_requested.connect(self._delete_roi)
        v.mode_changed.connect(self._on_editor_mode)
        v.status_message.connect(self._show_message)

        # quick bar
        self.btn_q_connect.clicked.connect(self._quick_camera_connect)
        self.btn_q_start.clicked.connect(self._quick_camera_start)
        self.btn_q_stop.clicked.connect(c.camera_stop)
        self.btn_q_add.clicked.connect(lambda: self._start_drawing(RoiType.INCLUDE))
        self.btn_q_ex.clicked.connect(lambda: self._start_drawing(RoiType.EXCLUDE))
        self.btn_q_finish.clicked.connect(v.finish_drawing)
        self.btn_q_edit.toggled.connect(v.set_edit_mode)
        self.btn_q_save.clicked.connect(self._save_rois)

    # ================================================================== small helpers
    def toggle_fullscreen(self) -> None:
        """F11: borderless fullscreen <-> maximized (the normal working state)."""
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def _update_clock(self) -> None:
        self.appbar.set_clock(datetime.now().strftime("%d/%m/%Y   %H:%M:%S"))

    def _show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 10000)

    def _start_drawing(self, rtype: RoiType) -> None:
        self.tabs.setCurrentIndex(TAB_ROI)
        self.video.start_drawing(rtype)

    def _quick_camera_connect(self) -> None:
        self.camera_cfg.apply_now()   # push the form values before connecting
        self.ctrl.camera_connect()

    def _quick_camera_start(self) -> None:
        self.camera_cfg.apply_now()
        self.ctrl.camera_start()

    def _open_alert_tab(self) -> None:
        self.tabs.setCurrentIndex(getattr(self, "_alert_tab", TAB_STATUS))

    # ================================================================== status slots
    def _apply_area(self, status: str) -> None:
        caption_text = status_caption(status)
        self.status_panel.set_area_status(status, caption_text)
        self.video.set_area_status(caption_text if status != "STOPPED" else "", status_color(status))
        self.banner.set_status(status, caption_text)

    def _on_system_state(self, state: str, msg: str) -> None:
        self._system_state = state
        self._system_message = msg
        self.status_panel.set_system(state, msg)
        running = self.ctrl.running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.video.set_overlay_info(msg or "")
        if msg:
            self.status_panel.set_detail(msg)
        elif state == "RUNNING":
            self.status_panel.set_detail("Đang giám sát")
        elif state == "STOPPED":
            self.status_panel.set_detail("Hệ thống đã dừng - nhấn START để bắt đầu")
        self._refresh_status()

    def _on_camera_state(self, state: str, msg: str) -> None:
        if state in ("TEST_OK", "TEST_FAIL"):
            self.camera_cfg.set_status(msg, ok=(state == "TEST_OK"))
            return
        self._camera_state = state
        led = {CameraState.STREAMING: "ok", CameraState.CONNECTED: "busy", CameraState.CONNECTING: "busy",
               CameraState.RECONNECTING: "warn", CameraState.FINISHED: "warn"}.get(
            state, "error" if state in (CameraState.LOST, CameraState.ERROR) else "off")
        self.status_panel.set_camera(state, led)
        self.camera_cfg.set_camera_state(state)
        self._update_camera_buttons(state)
        ok = None if state in (CameraState.DISCONNECTED, CameraState.CONNECTING) else state in (
            CameraState.STREAMING, CameraState.CONNECTED)
        self.camera_cfg.set_status(f"{state}: {msg}" if msg else state, ok)
        if state in (CameraState.DISCONNECTED, CameraState.LOST, CameraState.ERROR):
            self.status_panel.set_fps_camera(0.0)
            self.lbl_fps.setText("")
        if state == CameraState.DISCONNECTED:
            self.video.clear_image()
        self._refresh_status()

    def _on_camera_fps(self, fps: float) -> None:
        self.status_panel.set_fps_camera(fps)
        self.lbl_fps.setText(f"{fps:.1f} fps" if fps > 0 else "")

    def _on_camera_info(self, info) -> None:
        self.camera_cfg.set_status(info.summary(), ok=True)
        self.lbl_source.setText(info.summary())

    def _update_camera_buttons(self, state: str) -> None:
        connected = state in (CameraState.CONNECTED, CameraState.STREAMING, CameraState.FINISHED,
                              CameraState.RECONNECTING)
        streaming = state in (CameraState.STREAMING, CameraState.RECONNECTING)
        self.btn_q_connect.setEnabled(not connected)
        self.btn_q_start.setEnabled(not streaming)
        self.btn_q_stop.setEnabled(streaming or state == CameraState.FINISHED)

    def _on_model_status(self, loaded: bool, msg: str) -> None:
        failed = "failed" in msg.lower()
        self._model_loaded = loaded
        self._model_error = msg if failed else ""
        self.ai_cfg.set_model_status(msg, loaded if (failed or loaded) else None)
        if not self._detecting and not self._ai_camera_mode:
            led = "busy" if loaded else ("error" if failed else "off")
            self.status_panel.set_ai("MODEL LOADED" if loaded else ("LOAD FAILED" if failed else "STOPPED"), led)
        self._refresh_status()

    def _on_detection_state(self, running: bool, msg: str) -> None:
        self._detecting = running
        self.ai_cfg.set_detection_running(running)
        led = "ok" if running else ("busy" if self._model_loaded else "off")
        self.status_panel.set_ai("RUNNING" if running else ("MODEL LOADED" if self._model_loaded else "STOPPED"), led)
        self.video.set_display_mode("result" if running else "raw")
        if not running:
            self.video.clear_result()
            self.status_panel.set_ai_perf(0.0, 0.0)
            self.status_panel.set_counts(0, 0, 0, 0, "")
        self._refresh_status()

    def _on_plc_state(self, connected: bool, msg: str) -> None:
        self._plc_connected = connected
        sim = self.ctrl.settings.plc.simulation_mode
        led = "ok" if (connected and not sim) else ("warn" if connected else "error")
        label = ("SIMULATION" if sim else "CONNECTED") if connected else "DISCONNECTED"
        self.status_panel.set_plc(label, led)
        self.plc_cfg.set_connected(connected)
        self.plc_cfg.set_status(f"{label}: {msg}" if msg else label, connected)
        if not connected:
            self.status_panel.set_heartbeat(None, self.ctrl.settings.plc.heartbeat.enabled)
        self._refresh_status()

    def _on_result(self, result) -> None:
        self.video.set_result(result)
        ev = result.evaluation
        self.status_panel.set_counts(ev.total, ev.in_roi, ev.outside, ev.ignored, ", ".join(ev.occupied_roi_ids()))
        self.status_panel.set_ai_perf(result.ai_fps, result.inference_ms)
        if result.roi_states != self._roi_states:
            self._roi_states = dict(result.roi_states)
            self.roi_panel.update_states(self._roi_states)

    def _on_rois_changed(self) -> None:
        rois = self.ctrl.roi_manager.all()
        self.video.set_rois(rois)
        self.roi_panel.set_rois(rois, self._roi_states)
        self.roi_panel.set_dirty(self.ctrl.roi_manager.dirty)
        self._refresh_status()

    def _on_editor_mode(self, mode: str) -> None:
        self.roi_panel.set_mode(mode)
        self.btn_q_edit.blockSignals(True)
        self.btn_q_edit.setChecked(mode == "edit")
        self.btn_q_edit.blockSignals(False)
        self.btn_q_finish.setEnabled(mode == "drawing")
        self.lbl_video_hint.setText({
            "drawing": "Click thêm điểm  ·  double-click / Enter để đóng  ·  chuột phải hoàn tác  ·  Esc huỷ",
            "edit": "Kéo đỉnh để sửa  ·  Shift+click cạnh để thêm đỉnh  ·  chuột phải xoá đỉnh  ·  Delete xoá vùng",
        }.get(mode, self._idle_hint()))

    def _idle_hint(self) -> str:
        if self._ai_camera_mode:
            return "Chế độ AI Camera: camera tự phát hiện người, PC chỉ nhận sự kiện. Gán vùng ở tab AI Events."
        return "Vẽ vùng giám sát rồi nhấn START để bắt đầu."

    # ================================================================== AI camera mode
    def _apply_detection_mode(self, mode_value: str) -> None:
        """Re-label the parts of the UI whose meaning depends on the detection source."""
        try:
            mode = DetectionMode(mode_value)
        except ValueError:
            mode = DetectionMode.AI_CAMERA
        ai = mode == DetectionMode.AI_CAMERA
        self._ai_camera_mode = ai
        self.status_panel.set_ai_camera_mode(ai)
        self.status_panel.set_detection_mode("CAMERA AI" if ai else "PC AI / YOLO")
        self.video.set_placeholder(PLACEHOLDER_AI_CAMERA if ai else PLACEHOLDER_YOLO)
        self.appbar.set_mode("AI CAMERA" if ai else "PC AI · YOLO")
        self._update_window_title()
        self.tabs.setTabVisible(TAB_EVENT, ai)
        self.tabs.setTabVisible(TAB_AI, not ai)
        self.tabs.setTabVisible(TAB_ROI, not ai)
        self.status_panel.set_count_labels(ai)
        self.chip_detect.set_caption("AI EVENTS" if ai else "AI MODEL")
        self.chip_zones.set_caption("REGIONS" if ai else "ZONES")
        provider = self.ctrl.settings.camera.ai_camera.provider_enum
        self.event_monitor.set_simulation_available(provider == EventProviderType.MOCK)
        for btn in (self.btn_q_add, self.btn_q_ex, self.btn_q_finish, self.btn_q_edit, self.btn_q_save):
            btn.setEnabled(not ai)
            if ai:
                btn.setToolTip("Chế độ AI Camera: vùng được cấu hình trong camera (xem tab AI Events)")
        self.lbl_video_hint.setText(self._idle_hint())
        self.lbl_source.setText(self.camera_cfg.get_config().describe_source())
        self.video.set_display_mode("raw" if ai else self.video._display_mode)
        self._refresh_status()

    def _update_window_title(self) -> None:
        source = "AI Camera" if self._ai_camera_mode else "PC AI / YOLO"
        self.setWindowTitle(f"VisionGuard - Person-in-Area Monitoring - {source} -> Mitsubishi PLC")

    def _mode_selected_in_tab(self, mode_value: str) -> None:
        """The Camera tab combo changed: apply it straight away so the UI follows."""
        try:
            mode = DetectionMode(mode_value)
        except ValueError:
            return
        if mode != self.ctrl.detection_mode:
            self.ctrl.set_detection_mode(mode)
        self._apply_detection_mode(mode_value)

    def _on_camera_event(self, event: CameraEvent) -> None:
        self.event_monitor.add_event(event)
        if not event.type_enum.is_health:
            self.status_panel.set_last_event(event.summary(), event.timestamp.strftime("%H:%M:%S"))

    def _on_event_channel_state(self, state: str, message: str) -> None:
        self._event_channel = state
        self._event_message = message
        self.event_monitor.set_channel_state(state, message)
        led = {"ONLINE": "ok", "CONNECTING": "busy", "RECONNECTING": "warn",
               "ERROR": "error", "DISABLED": "off"}.get(state, "error" if state == "DISCONNECTED" else "off")
        self.status_panel.set_event_channel(state, led)
        self._refresh_status()

    def _on_zone_states(self, states) -> None:
        self.event_monitor.update_zone_states(states)
        occupied = [rid for rid, st in states.items() if getattr(st, "occupied", False)]
        counts = self.ctrl.event_state
        self.status_panel.set_counts(counts.total_person_count(), len(occupied), 0,
                                     counts.ignored_non_human, ", ".join(occupied))

    def _on_regions_changed(self) -> None:
        self.event_monitor.set_regions(self.ctrl.region_mapping.all(), self.ctrl.zone_states())
        self._refresh_status()

    def _save_regions(self) -> None:
        self.ctrl.save_regions()
        self.event_monitor.set_regions_dirty(False)

    # ================================================================== state chips + alert
    def _refresh_status(self) -> None:
        """Recompute the four chips, then surface the worst problem in the alert strip."""
        cam = {
            CameraState.STREAMING: ("ok", "Đang truyền hình"),
            CameraState.CONNECTED: ("busy", "Đã kết nối - nhấn Start"),
            CameraState.CONNECTING: ("busy", "Đang kết nối..."),
            CameraState.RECONNECTING: ("warn", "Đang kết nối lại..."),
            CameraState.FINISHED: ("warn", "Hết video"),
            CameraState.LOST: ("error", "Mất camera"),
            CameraState.ERROR: ("error", "Lỗi kết nối"),
        }.get(self._camera_state, ("off", "Chưa kết nối"))
        self.chip_camera.set(cam[0], cam[1])

        if self._ai_camera_mode:
            detect = {
                "ONLINE": ("ok", "Đang nhận sự kiện", ""),
                "CONNECTING": ("busy", "Đang kết nối...", ""),
                "RECONNECTING": ("warn", "Đang kết nối lại", self._event_message),
                "ERROR": ("error", self._event_message or "Không mở được kênh sự kiện", self._event_message),
                "DISABLED": ("off", "Không dùng", ""),
            }.get(self._event_channel, ("off", "Chưa kết nối", ""))
            zones = self._region_chip()
        else:
            if self._detecting:
                detect = ("ok", "Đang phát hiện người", "")
            elif self._model_error:
                detect = ("error", "Nạp model thất bại", self._model_error)
            elif self._model_loaded:
                detect = ("busy", "Đã nạp model", "")
            else:
                detect = ("off", "Chưa nạp model", "")
            zones = self._zone_chip()
        self.chip_detect.set(detect[0], detect[1], detect[2] or detect[1])
        self.chip_zones.set(*zones)

        sim = self.ctrl.settings.plc.simulation_mode
        if self._plc_connected:
            plc = (("warn", "Mô phỏng (PLC ảo)") if sim
                   else ("ok", f"Đã kết nối {self.ctrl.settings.plc.connection.ip}"))
        else:
            plc = ("error" if self.ctrl.running else "off", "Chưa kết nối")
        self.chip_plc.set(plc[0], plc[1])

        self._refresh_alert()

    def _region_chip(self) -> tuple:
        known = self.ctrl.region_mapping.all()
        mapped = [r for r in known if r.enabled and r.plc_device]
        if not known:
            return ("warn", "Chưa thấy vùng nào từ camera",
                    "Camera chưa gửi sự kiện nào nên phần mềm chưa biết camera có những vùng nào")
        if not mapped:
            return ("warn", f"{len(known)} vùng, chưa gán PLC", "")
        return ("ok", f"Đã gán {len(mapped)}/{len(known)} vùng", "")

    def _zone_chip(self) -> tuple:
        includes = [r for r in self.ctrl.roi_manager.include_rois() if r.enabled and r.is_valid()]
        excludes = [r for r in self.ctrl.roi_manager.exclude_rois() if r.enabled and r.is_valid()]
        if not includes:
            return ("warn", "Chưa vẽ vùng giám sát", "")
        detail = f"{len(includes)} vùng" + (f" + {len(excludes)} loại trừ" if excludes else "")
        dirty = self.ctrl.roi_manager.dirty
        return ("busy" if dirty else "ok", detail + (" · chưa lưu" if dirty else ""), "")

    def _refresh_alert(self) -> None:
        """One line, only when it earns its place: the most severe unhealthy subsystem.

        While the system is stopped only hard errors are worth interrupting for - a camera
        that is simply not connected yet is the normal state of a freshly opened app.
        """
        running = self._system_state in ("RUNNING", "STARTING")
        candidates = [
            (self.chip_camera, TAB_CAMERA, "Camera"),
            (self.chip_detect, TAB_EVENT if self._ai_camera_mode else TAB_AI,
             "Kênh sự kiện AI" if self._ai_camera_mode else "Model AI"),
            (self.chip_zones, TAB_EVENT if self._ai_camera_mode else TAB_ROI,
             "Vùng camera" if self._ai_camera_mode else "Vùng giám sát"),
            (self.chip_plc, TAB_PLC, "PLC"),
        ]
        unhealthy = [(chip, tab, name, chip.led.state) for chip, tab, name in candidates
                     if chip.led.state == "error" or (chip.led.state == "warn" and running)]
        if not unhealthy:
            self.alert.clear()
            return
        errors = [u for u in unhealthy if u[3] == "error"]
        chip, tab, name, state = (errors or unhealthy)[0]
        self._alert_tab = tab
        detail = chip.lbl_value.full_text()
        tip = chip.toolTip()
        text = f"{name}: {detail}" + (f" — {tip}" if tip and tip != detail else "")
        self.alert.show_alert(state, text, "Mở tab")

    # ================================================================== ROI / events actions
    def _delete_roi(self, roi_id: str) -> None:
        if roi_id:
            self.ctrl.delete_roi(roi_id)

    def _clear_rois(self) -> None:
        if QMessageBox.question(self, "Xoá tất cả vùng",
                                "Xoá TOÀN BỘ vùng giám sát (kể cả vùng loại trừ)?") == \
                QMessageBox.StandardButton.Yes:
            self.ctrl.clear_rois()

    def _save_rois(self) -> None:
        self.ctrl.save_rois()
        self.roi_panel.set_dirty(self.ctrl.roi_manager.dirty)
        self._refresh_status()

    def _clear_events(self) -> None:
        if QMessageBox.question(self, "Xoá lịch sử",
                                "Xoá toàn bộ lịch sử sự kiện?") == QMessageBox.StandardButton.Yes:
            self.ctrl.events.clear()
            self.events_widget.set_events([], 0)

    # ================================================================== PLC / simulation
    def _plc_config_applied(self, cfg) -> None:
        self.ctrl.apply_plc_config(cfg)
        self.io_test.set_mapping(cfg.mapping)
        self.btn_sim.blockSignals(True)
        self.btn_sim.setChecked(cfg.simulation_mode)
        self.btn_sim.blockSignals(False)
        self._style_sim_button(cfg.simulation_mode)
        self.status_panel.set_heartbeat(None, cfg.heartbeat.enabled)
        self._refresh_status()

    def _sim_toggled_toolbar(self, on: bool) -> None:
        self._style_sim_button(on)
        self.plc_cfg.set_simulation(on)
        self.ctrl.set_simulation(on)
        self._refresh_status()

    def _sim_toggled_tab(self, on: bool) -> None:
        self.btn_sim.blockSignals(True)
        self.btn_sim.setChecked(on)
        self.btn_sim.blockSignals(False)
        self._style_sim_button(on)
        self.ctrl.set_simulation(on)
        self._refresh_status()

    # ================================================================== close
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.ctrl.running:
            if QMessageBox.question(self, "Thoát", "Hệ thống đang chạy. Dừng giám sát và thoát?") != \
                    QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.ctrl.stop_system()
        try:
            self.ctrl.settings.camera = self.camera_cfg.get_config()
            self.ctrl.settings.ai = self.ai_cfg.get_config()
            self.ctrl.settings.plc = self.plc_cfg.get_config()
            self.ctrl.cm.save_all()
            if self.ctrl.roi_manager.dirty:
                self.ctrl.roi_manager.save()
        except Exception as exc:
            log.error("Saving configuration on exit failed: %s", exc)
        self.ctrl.shutdown()
        super().closeEvent(event)
