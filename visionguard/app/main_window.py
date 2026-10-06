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

from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from ..camera.events.camera_event import CameraEvent
from ..config.config_manager import ConfigManager
from ..config.schemas import DetectionMode, EventProviderType
from ..logic.occupancy_state_machine import OccupancyState
from ..plc.roi_mapping import NO_ZONE_MESSAGE
from ..roi.roi_model import RoiType
from ..workers.camera_worker import CameraState
from .controllers.system_controller import SystemController
from .theme import status_caption
from .widgets.ai_config_widget import AIConfigWidget
from .widgets.camera_config_widget import CameraConfigWidget
from .widgets.chrome import AlertStrip, AppBar, ElidedLabel, StateChip, caption
from .widgets.event_monitor_widget import EventMonitorWidget
from .widgets.events_widget import EventsWidget
from .widgets.form_helpers import fit_narrow_panel
from .widgets.plc_config_widget import PlcConfigWidget
from .widgets.roi_panel import RoiPanel
from .widgets.status_panel import StatusPanel
from .widgets.storage_config_widget import StorageConfigWidget
from .widgets.video_grid import VideoGrid
from .widgets.video_view import PLACEHOLDER_AI_CAMERA, PLACEHOLDER_YOLO

log = logging.getLogger("UI")

TAB_STATUS, TAB_CAMERA, TAB_EVENT, TAB_AI, TAB_ROI, TAB_PLC, TAB_STORAGE = range(7)


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
        self._camera_message = ""
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
        self._storage_error = self.ctrl.events.last_error

        # ---------------------------------------------------------- widgets
        self.video = VideoGrid(s.app.visualization, s.app.ui_fps_limit)
        self.status_panel = StatusPanel()
        self.camera_cfg = CameraConfigWidget(s.camera, self.ctrl.camera_manager.availability())
        self.ai_cfg = AIConfigWidget(s.ai)
        self.plc_cfg = PlcConfigWidget(s.plc)
        self.roi_panel = RoiPanel()
        self.storage_cfg = StorageConfigWidget(s.app)
        self.history = EventsWidget()
        self.storage_tabs = QTabWidget()
        self.storage_tabs.addTab(self.storage_cfg, "Cấu hình")
        self.storage_tabs.addTab(self.history, "Lịch sử")
        self.event_monitor = EventMonitorWidget()
        # the manual I/O screen lives inside the PLC tab: it is PLC testing, not a topic of its own
        self.io_test = self.plc_cfg.io_test

        self._build_layout()
        self._build_shortcuts()
        self._wire()

        self.video.set_rois(self.ctrl.roi_manager.all())
        self.roi_panel.set_rois(self.ctrl.roi_manager.all())
        self.plc_cfg.set_rois(self.ctrl.roi_manager.all())
        self._apply_area("STOPPED")
        self.status_panel.set_heartbeat(None, s.plc.heartbeat.enabled)
        self.event_monitor.set_regions(self.ctrl.region_mapping.all())
        self.event_monitor.set_events(self.ctrl.camera_events(120))
        self.btn_sim.setChecked(s.plc.simulation_mode)
        self._style_sim_button(s.plc.simulation_mode)
        self._apply_detection_mode(s.camera.detection_mode)
        self._apply_camera_count(s.camera)
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
        # Resources on their own, slower timer: sampled every second the numbers flicker
        # too much to read, and two seconds is plenty to notice a machine in trouble.
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self.appbar.refresh_resources)
        self._meter_timer.start(2000)
        return self.appbar

    def _build_status_row(self) -> QWidget:
        """The four readouts across the top: what each subsystem is doing right now.

        Readouts only - the buttons that used to share this row now sit in the action bar
        along the bottom edge. Mixing "press me" with "read me" in one strip made the top
        of the window busy, and every pixel this row gives back is a pixel the video gets.
        """
        bar = QFrame()
        bar.setProperty("class", "card")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(10)

        self.chip_camera = StateChip("CAMERA")
        self.chip_detect = StateChip("AI EVENTS")
        self.chip_zones = StateChip("REGIONS")
        self.chip_plc = StateChip("PLC")
        # No width cap on the chips. Capped, each one floats in the middle of its quarter
        # of the row and the strip reads as four stickers with gaps between them; letting
        # them fill turns it into one instrument panel with four gauges.
        for chip in (self.chip_camera, self.chip_detect, self.chip_zones, self.chip_plc):
            lay.addWidget(chip, 1)
        return bar

    def _build_shortcuts(self) -> None:
        act_start = QAction("Start system", self)
        act_start.setShortcut(QKeySequence("F5"))
        act_start.triggered.connect(self._start_clicked)
        act_stop = QAction("Stop system", self)
        act_stop.setShortcut(QKeySequence("F6"))
        act_stop.triggered.connect(self.ctrl.stop_system)
        act_full = QAction("Toggle fullscreen", self)
        act_full.setShortcut(QKeySequence("F11"))
        act_full.triggered.connect(self.toggle_fullscreen)
        act_panel = QAction("Toggle control panel", self)
        act_panel.setShortcut(QKeySequence("F9"))
        act_panel.triggered.connect(lambda: self.toggle_side_panel())
        self.addActions([act_start, act_stop, act_full, act_panel])

    def _style_sim_button(self, on: bool) -> None:
        # The PLC chip in the status row already says "Mô phỏng (PLC ảo)"; the app bar
        # used to repeat it in a second vocabulary, which is what made the bar look busy.
        self.btn_sim.setText("PLC SIM: ON" if on else "PLC SIM")

    # ================================================================== layout
    def _build_layout(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_app_bar())

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(10, 8, 10, 7)
        body_lay.setSpacing(7)
        body_lay.addWidget(self._build_status_row())

        self.alert = AlertStrip()
        body_lay.addWidget(self.alert)

        # A fixed split, not a draggable one. An HMI gets nudged by people leaning on the
        # desk, and a splitter that can be dragged will eventually be dragged - leaving the
        # live view a sliver and nobody sure how to get it back. The side panel keeps one
        # width; the video takes everything else and grows with the window.
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(9)
        row_lay.addWidget(self._build_live_card(), 1)
        # Built before the panel, because building the panel is what measures every page
        # against its fixed width - a control block added afterwards would skip that check.
        self.status_panel.set_controls(self._build_controls())
        row_lay.addWidget(self._build_side_panel(), 0)
        body_lay.addWidget(row, 1)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)

        # No QStatusBar. It was a second one-line message strip directly under the first
        # one in the action bar - 19px of window spent saying what the line above it was
        # already there to say. Messages now land at the right end of the action bar.

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
        return card

    #: Width the control panel keeps whatever the window does. Measured on a real screen,
    #: not offscreen: at 125% scaling the widest page (Zones) needs 386 logical px, so 410
    #: clears every tab with room for the tab frame and a vertical scroll bar.
    #:
    #: Measure this on a REAL display if it ever needs revisiting. An offscreen Qt session
    #: reports different - here, much larger - minimum widths than the same widgets laid out
    #: on a scaled screen, and sizing the panel from those numbers costs the video a third
    #: of the window for nothing. F9 folds the panel away when the picture matters more.
    SIDE_PANEL_W = 410

    def _build_side_panel(self) -> QWidget:
        self.tabs = QTabWidget()
        # Seven tabs at the app-wide tab padding want 489 px; the panel is 410. Nothing
        # warned about it - the tab bar simply cut the last tab in half, so "Ghi hình"
        # was there but unreachable. Tighter padding here only, by object name: the
        # sub-tabs inside a page have shorter labels and do not need the squeeze.
        self.tabs.setObjectName("SidePanelTabs")
        self.tabs.tabBar().setObjectName("SidePanelTabBar")
        self.tabs.addTab(self.status_panel, "Overview")
        self.tabs.addTab(self.camera_cfg, "Camera")
        self.tabs.addTab(self.event_monitor, "AI Events")
        self.tabs.addTab(self.ai_cfg, "AI Model")
        self.tabs.addTab(self.roi_panel, "Zones")
        self.tabs.addTab(self.plc_cfg, "PLC")
        self.tabs.addTab(self.storage_tabs, "Lưu trữ")
        # Every page has to fit the panel's fixed width; none of them may ask the operator
        # to drag a horizontal scroll bar to find out which setting a value belongs to.
        # Done here rather than in each page so a tab added later is covered too.
        for index in range(self.tabs.count()):
            fit_narrow_panel(self.tabs.widget(index))
        self.tabs.setFixedWidth(self.SIDE_PANEL_W)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setUsesScrollButtons(False)

        # The collapse handle lives OUTSIDE the panel it collapses - put it inside and
        # folding the panel away takes the only way back with it.
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.btn_panel = QPushButton("▶")
        self.btn_panel.setFixedWidth(16)
        self.btn_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.btn_panel.setFlat(True)
        self.btn_panel.clicked.connect(lambda: self.toggle_side_panel())
        lay.addWidget(self.btn_panel)
        lay.addWidget(self.tabs)
        self._sync_panel_button()
        return holder

    def toggle_side_panel(self, collapse: bool | None = None) -> None:
        """Fold the control panel to the right edge so the video has the whole window."""
        if collapse is None:
            collapse = self.tabs.isVisible()
        self.tabs.setVisible(not collapse)
        self._sync_panel_button()

    def _sync_panel_button(self) -> None:
        collapsed = not self.tabs.isVisible()
        self.btn_panel.setText("◀" if collapsed else "▶")
        self.btn_panel.setToolTip(("Mở bảng điều khiển (F9)" if collapsed
                                   else "Thu gọn bảng điều khiển để xem hình lớn (F9)"))

    def _build_controls(self) -> QWidget:
        """Everything the operator presses, as a block on the Overview page.

        Not a bar along the bottom of the window. A bar there is always visible, which
        sounds like the safer choice, but it also permanently costs the video its bottom
        50 px and cannot be folded away - and the whole point of F9 is to hand the picture
        the entire window. On this page the buttons fold away with the panel that holds
        them. F5 and F6 still start and stop with the panel shut.
        """
        block = QWidget()
        outer = QVBoxLayout(block)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(7)

        run = QHBoxLayout()
        run.setContentsMargins(0, 0, 0, 0)
        run.setSpacing(6)
        self.btn_start = _button("▶  START", "success", "", "Bắt đầu giám sát (F5)")
        self.btn_stop = _button("■  STOP", "danger", "", "Dừng giám sát (F6)")
        self.btn_stop.setEnabled(False)
        self.btn_sim = _button("PLC SIM", "", "",
                               "Bật: không cần PLC thật, mọi tín hiệu ghi vào bộ nhớ ảo của phần mềm")
        self.btn_sim.setCheckable(True)
        # Minimum width, not fixed: three buttons across a 410px panel have room to spare,
        # and letting them share the row equally keeps the block aligned with the groups
        # above it however long "PLC SIM: ON" gets in translation.
        for b in (self.btn_start, self.btn_stop, self.btn_sim):
            b.setMinimumHeight(34)
            b.setMinimumWidth(72)
            run.addWidget(b, 1)
        outer.addLayout(run)

        cam = QHBoxLayout()
        cam.setContentsMargins(0, 0, 0, 0)
        cam.setSpacing(6)
        cam.addWidget(caption("CAMERA"))
        self.btn_q_connect = _button("Connect", "", "sm", "Mở kết nối tới mọi camera trong nhóm")
        self.btn_q_disconnect = _button("Disconnect", "", "sm", "Ngắt kết nối mọi camera")
        for b in (self.btn_q_connect, self.btn_q_disconnect):
            b.setMinimumWidth(72)
            cam.addWidget(b, 1)
        self.btn_restore = _button("⤢  Thu nhỏ", "", "sm", "Trở lại lưới nhiều camera")
        self.btn_restore.hide()
        cam.addWidget(self.btn_restore)
        outer.addLayout(cam)

        return block

    # ================================================================== wiring
    def _wire(self) -> None:
        c = self.ctrl
        self.btn_start.clicked.connect(self._start_clicked)
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
        c.rois_changed.connect(self._on_rois_changed)
        c.dropped_frames.connect(self.status_panel.set_dropped)

        # camera tab
        cc = self.camera_cfg
        cc.config_applied.connect(c.apply_camera_config)
        cc.config_applied.connect(lambda cfg: self.lbl_source.setText(cfg.describe_source()))
        cc.config_applied.connect(self._apply_camera_count)
        self.video.active_changed.connect(self._on_active_camera)
        cc.connect_requested.connect(c.camera_connect)
        cc.disconnect_requested.connect(c.camera_disconnect)
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


        # storage
        self.storage_cfg.config_applied.connect(c.apply_app_config)
        self.storage_cfg.open_folder_requested.connect(self._open_folder)
        self.history.refresh_requested.connect(c.events.request_recent)
        self.history.older_requested.connect(c.events.request_recent)
        self.history.export_requested.connect(c.events.request_export)
        self.history.clear_requested.connect(self._clear_history)
        c.events.history_ready.connect(self.history.set_events)
        c.events.saved.connect(self.history.add_event)
        c.events.cleared.connect(c.events.request_recent)
        c.events.exported.connect(self._on_history_exported)
        c.events.failed.connect(self._on_storage_error)
        c.events.recovered.connect(lambda: self._on_storage_error(""))
        self.storage_tabs.currentChanged.connect(lambda index: c.events.request_recent() if index == 1 else None)
        self.tabs.currentChanged.connect(lambda index: c.events.request_recent()
                                         if index == TAB_STORAGE and self.storage_tabs.currentIndex() == 1 else None)

        # ROI panel + video editor
        rp, v = self.roi_panel, self.video
        rp.add_include_requested.connect(lambda: self._begin_drawing(RoiType.INCLUDE))
        rp.finish_requested.connect(v.finish_drawing)
        rp.cancel_requested.connect(v.cancel_drawing)
        rp.edit_mode_toggled.connect(v.set_edit_mode)
        rp.delete_requested.connect(self._delete_roi)
        rp.clear_requested.connect(self._clear_rois)
        rp.save_requested.connect(self._save_rois)
        rp.devices_requested.connect(self._open_roi_devices)
        rm = pc.roi_mapping
        rm.selection_changed.connect(self._select_roi_device)
        rm.fields_changed.connect(c.update_roi_fields)
        rm.save_requested.connect(self._save_rois)
        rm.delete_requested.connect(self._delete_roi)
        rm.edit_requested.connect(self._edit_roi_from_devices)
        rm.zones_requested.connect(lambda: self.tabs.setCurrentIndex(TAB_ROI))
        v.roi_drawn.connect(c.add_roi)
        v.roi_points_changed.connect(c.update_roi_points)
        v.roi_selected.connect(rp.select)
        v.roi_selected.connect(rm.select)
        v.roi_delete_requested.connect(self._delete_roi)
        v.mode_changed.connect(self._on_editor_mode)
        # "Need at least 3 points to close a polygon" and friends: onto the Zones page,
        # where someone drawing is already looking. The next set_mode overwrites it, which
        # is the right lifetime - the warning stands until the mode it applies to ends.
        v.status_message.connect(self.roi_panel.set_notice)
        # Picking a camera on the Zones page zooms the picture to it; the zoom is what makes
        # it the active camera, so the next polygon lands on it.
        self.roi_panel.target_camera_changed.connect(self.video.set_maximized)

        # quick bar
        self.btn_q_connect.clicked.connect(self._quick_camera_connect)
        self.btn_q_disconnect.clicked.connect(c.camera_disconnect)
        self.btn_restore.clicked.connect(lambda: self.video.set_maximized(-1))
        self.video.maximize_changed.connect(self._on_maximize_changed)

    # ================================================================== small helpers
    def toggle_fullscreen(self) -> None:
        """F11: borderless fullscreen <-> maximized (the normal working state)."""
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def _clear_history(self) -> None:
        if QMessageBox.question(self, "Xóa lịch sử", "Xóa toàn bộ lịch sử sự kiện trong cơ sở dữ liệu?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.ctrl.events.request_clear()

    def _on_storage_error(self, message: str) -> None:
        self._storage_error = message
        self._refresh_alert()

    def _on_history_exported(self, ok: bool, path: str) -> None:
        if ok:
            QMessageBox.information(self, "Xuất lịch sử", f"Đã lưu CSV: {path}")
        else:
            QMessageBox.warning(self, "Xuất lịch sử", f"Không xuất được CSV: {path}")

    def _update_clock(self) -> None:
        now = datetime.now()
        self.appbar.set_clock(now.strftime("%H:%M:%S"), now.strftime("%d/%m/%Y"))

    def _show_message(self, text: str) -> None:
        """Transient confirmations go to the log, not to the screen.

        They had a line of their own under the buttons and it earned its space poorly:
        "YOLO loaded", "ROI configuration saved" - each one confirms an action the
        operator just took and can see the result of. What they must not miss is not
        transient: a failure turns its chip red, raises the alert strip and is written to
        the log as an event. DEBUG rather than INFO so an on-site log is not doubled -
        most of these are already logged by the subsystem that emitted them, and
        `main.py --log-level DEBUG` brings the whole stream back when diagnosing.
        """
        logging.getLogger("UI").debug(text)

    def _quick_camera_connect(self) -> None:
        self.camera_cfg.apply_now()   # push the form values before connecting
        self.ctrl.camera_connect()

    def _begin_drawing(self, rtype: RoiType) -> None:
        """A polygon is drawn on one picture, so zoom into that camera first."""
        if self.video.count > 1 and self.video.maximized < 0:
            self.video.set_maximized(self.video.active)
        self.video.start_drawing(rtype)

    def _open_roi_devices(self) -> None:
        self.plc_cfg.show_devices()
        self.tabs.setCurrentIndex(TAB_PLC)

    def _start_clicked(self) -> None:
        """START by hand refuses while a zone or a ROI bit is missing: the PLC would hear nothing.

        Only the operator's click is stopped here. Autostart goes through the controller,
        which reports the same problem as a FAULT instead of a dialog nobody is there to close.
        """
        if self.ctrl.running:
            return
        error = self.ctrl.roi_mapping_error()
        if error:
            QMessageBox.warning(self, "Chưa thể START", f"{error}\n\nMỗi zone cần một bit riêng để gửi "
                                "có người / không có người xuống PLC.")
            if error == NO_ZONE_MESSAGE:
                self.tabs.setCurrentIndex(TAB_ROI)
            else:
                self._open_roi_devices()
            return
        self.ctrl.start_system()

    def _select_roi_device(self, roi_id: str) -> None:
        roi = self.ctrl.roi_manager.get(roi_id)
        if roi is not None and self.video.count > 1:
            self.video.set_maximized(roi.camera)
        # This selection originated in Devices. Highlighting the video must not
        # send selection events back into the table while Qt is handling a click.
        with QSignalBlocker(self.video):
            self.video.select_roi(roi_id)
        self.roi_panel.select(roi_id)

    def _edit_roi_from_devices(self, roi_id: str) -> None:
        self._select_roi_device(roi_id)
        self.tabs.setCurrentIndex(TAB_ROI)
        self.video.set_edit_mode(True)

    def _open_alert_tab(self) -> None:
        self.tabs.setCurrentIndex(getattr(self, "_alert_tab", TAB_STATUS))

    # ================================================================== status slots
    def _apply_area(self, status: str) -> None:
        # Only the Overview tab carries the verdict now. The video used to paint a second
        # copy of it across the top-left of the picture, over the one thing in this window
        # that cannot be shown anywhere else.
        self.status_panel.set_area_status(status, status_caption(status))

    def _on_system_state(self, state: str, msg: str) -> None:
        self._system_state = state
        self._system_message = msg
        running = self.ctrl.running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.video.set_overlay_info(msg or "")
        self._refresh_status()

    def _on_camera_state(self, state: str, msg: str) -> None:
        if state in ("TEST_OK", "TEST_FAIL"):
            self.camera_cfg.set_status(msg, ok=(state == "TEST_OK"))
            return
        self._camera_state = state
        self._camera_message = msg
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
        self.btn_q_connect.setEnabled(not connected)
        self.btn_q_disconnect.setEnabled(connected or state == CameraState.LOST)

    def _on_maximize_changed(self, index: int) -> None:
        """Only a maximised tile can be put back."""
        self.btn_restore.setVisible(index >= 0)
        self._on_active_camera(self.video.active)

    def _on_model_status(self, loaded: bool, msg: str) -> None:
        failed = "failed" in msg.lower()
        self._model_loaded = loaded
        self._model_error = msg if failed else ""
        self.ai_cfg.set_model_status(msg, loaded if (failed or loaded) else None)
        self._refresh_status()

    def _on_detection_state(self, running: bool, msg: str) -> None:
        self._detecting = running
        self.ai_cfg.set_detection_running(running)
        self.video.set_display_mode("result" if running else "raw")
        if not running:
            self.video.clear_result()
            self.status_panel.set_ai_perf(0.0, 0.0)
        self._refresh_status()

    def _on_plc_state(self, connected: bool, msg: str) -> None:
        self._plc_connected = connected
        sim = self.ctrl.settings.plc.simulation_mode
        label = ("SIMULATION" if sim else "CONNECTED") if connected else "DISCONNECTED"
        self.plc_cfg.set_connected(connected)
        self.plc_cfg.set_status(f"{label}: {msg}" if msg else label, connected)
        if not connected:
            self.status_panel.set_heartbeat(None, self.ctrl.settings.plc.heartbeat.enabled)
        self._refresh_status()

    def _on_result(self, result) -> None:
        self.video.set_result(result)
        self.status_panel.set_ai_perf(result.ai_fps, result.inference_ms)
        if result.roi_states != self._roi_states:
            self._roi_states = dict(result.roi_states)
            self.plc_cfg.roi_mapping.update_states(self._roi_states)

    def _on_rois_changed(self) -> None:
        rois = self.ctrl.roi_manager.all()
        # A tile on a different camera may clear its local selection when its ROI
        # list is refreshed. Keep that from clearing the selected Devices form.
        with QSignalBlocker(self.video):
            self.video.set_rois(rois)
        self.roi_panel.set_rois(rois)
        self.plc_cfg.set_rois(rois, self._roi_states)
        self._select_roi_device(self.plc_cfg.roi_mapping.selected_roi_id)
        self.roi_panel.set_dirty(self.ctrl.roi_manager.dirty)
        self.plc_cfg.roi_mapping.set_dirty(self.ctrl.roi_manager.dirty)
        self._refresh_status()

    def _on_editor_mode(self, mode: str) -> None:
        # set_mode writes the drawing instructions on the Zones page itself. There used to
        # be a second copy of them under the video, in different words - two sets of
        # instructions for one action, and the operator reads whichever they happen to be
        # looking at. The Zones page wins: it is the page you are on while drawing.
        self.roi_panel.set_mode(mode)

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
        self.video.set_placeholder(PLACEHOLDER_AI_CAMERA if ai else PLACEHOLDER_YOLO)
        self.tabs.setTabVisible(TAB_EVENT, ai)
        self.tabs.setTabVisible(TAB_AI, not ai)
        self.tabs.setTabVisible(TAB_ROI, not ai)
        self.plc_cfg.set_roi_mode(not ai)
        self.chip_detect.set_caption("AI EVENTS" if ai else "AI MODEL")
        self.chip_zones.set_caption("REGIONS" if ai else "ZONES")
        provider = self.ctrl.settings.camera.ai_camera.provider_enum
        self.event_monitor.set_simulation_available(provider == EventProviderType.MOCK)
        self.lbl_source.setText(self.camera_cfg.get_config().describe_source())
        self.video.set_display_mode("raw" if ai else self.video._display_mode)
        self._refresh_status()

    def _apply_camera_count(self, cfg) -> None:
        """One tile per camera, named the way the Camera tab names them."""
        count = cfg.camera_count
        self.video.set_count(count, [cfg.label(i) for i in range(count)])
        with QSignalBlocker(self.video):
            self.video.set_rois(self.ctrl.roi_manager.all())
        self._select_roi_device(self.plc_cfg.roi_mapping.selected_roi_id)
        self._on_active_camera(self.video.active)

    def _on_active_camera(self, index: int) -> None:
        """Which camera a new zone would be drawn on."""
        cfg_now = self.ctrl.settings.camera
        labels = [cfg_now.label(i) for i in range(cfg_now.camera_count)]
        self.plc_cfg.roi_mapping.set_cameras(labels)
        self.roi_panel.set_target_camera(index, labels)
        if self.video.count <= 1:
            self.lbl_source.setText(self.ctrl.settings.camera.describe_source())
            return
        cfg = self.ctrl.settings.camera
        self.lbl_source.setText(f"{cfg.label(index)} · {cfg.unit(index).describe_source()}"
                                f"   ({self.video.count} camera)")

    def _update_window_title(self) -> None:
        # Just the name. The old title spelled out the subtitle, the detection mode and
        # the PLC brand, all three of which the window itself shows a few pixels lower.
        self.setWindowTitle("VisionGuard")

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
        self._refresh_status()

    def _on_zone_states(self, states) -> None:
        self.event_monitor.update_zone_states(states)

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
        # The reason, not just the state. "Đang kết nối lại..." on its own sent somebody
        # hunting for a network fault when the camera worker had been saying "serial '1'
        # not found" all along - the answer was in the log and nowhere on the screen. The
        # chip carries it as its tooltip, which is also what the alert strip reads.
        self.chip_camera.set(cam[0], cam[1], self._camera_message or cam[1])

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
        # Simulation is a mode the operator picked, not something to interrupt them about:
        # the amber badge in the app bar already says the PLC output is virtual.
        self._plc_expected = bool(self._plc_connected and sim)
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
        error = self.ctrl.roi_mapping_error()
        if error:
            return ("warn", "ROI chưa cấu hình đủ bit PLC", error)
        includes = [r for r in self.ctrl.roi_manager.include_rois() if r.enabled and r.is_valid()]
        excludes = [r for r in self.ctrl.roi_manager.exclude_rois() if r.enabled and r.is_valid()]
        count = self.ctrl.settings.camera.camera_count
        bare = count - len({int(getattr(r, "camera", 0)) for r in includes})   # cameras with no zone
        if not includes:
            return ("warn", "Chưa có ROI đang bật xuất PLC", "Vẽ vùng tại Zones, gán bit tại PLC → Devices")
        detail = f"{len(includes)} ROI · {sum(bool(r.plc_device) for r in includes)} bit PLC"
        if excludes:
            detail += f" + {len(excludes)} vùng loại trừ cũ"
        if bare > 0:
            detail += f" · {bare} camera toàn khung"
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
        if getattr(self, "_plc_expected", False):
            candidates = [c for c in candidates if c[0] is not self.chip_plc]
        unhealthy = [(chip, tab, name, chip.led.state) for chip, tab, name in candidates
                     if chip.led.state == "error" or (chip.led.state == "warn" and running)]
        if not unhealthy:
            if self._storage_error:
                self._alert_tab = TAB_STORAGE
                self.alert.show_alert("error", self._storage_error, "Mở tab")
                return
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
        self.plc_cfg.roi_mapping.set_dirty(self.ctrl.roi_manager.dirty)
        self._refresh_status()

    def _open_folder(self, path: str) -> None:
        """Show a storage folder in the file manager, creating it if it is not there yet.

        A path that has only been typed into the box has no folder behind it until the
        first file is written, and "nothing happened" is a poor answer to a button press.
        """
        folder = Path(path.strip() or ".")
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_message(f"Không mở được thư mục: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    # ================================================================== PLC / simulation
    def _plc_config_applied(self, cfg) -> None:
        error = self.ctrl.apply_plc_config(cfg)
        if error:
            # Refused settings used to vanish without a word: the form snapped back and
            # the PLC kept the old bits, while the operator believed the new ones were live.
            QMessageBox.warning(self, "Chưa lưu cấu hình PLC",
                                f"{error}\n\nCấu hình PLC cũ vẫn đang được dùng.")
            self.plc_cfg.set_config(self.ctrl.settings.plc)
            return
        self.plc_cfg.roi_mapping.set_plc_config(cfg)
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
        # Nothing should still be sampling the process while it is being torn down.
        self._meter_timer.stop()
        try:
            # X saves everything, including edits typed but never confirmed with Apply.
            self.plc_cfg.roi_mapping.apply_pending()
            self.ctrl.settings.camera = self.camera_cfg.get_config()
            self.ctrl.settings.ai = self.ai_cfg.get_config()
            self.ctrl.settings.plc = self.plc_cfg.get_config()
            app_cfg = self.storage_cfg.get_config()
            self.ctrl.settings.app = app_cfg
            self.ctrl.cm.save_all()
            if self.ctrl.roi_manager.dirty:
                self.ctrl.roi_manager.save()
            if self.ctrl.region_mapping.dirty:
                self.ctrl.region_mapping.save()
        except Exception as exc:
            log.error("Saving configuration on exit failed: %s", exc)
        # Go off screen before shutting the workers down. If the model happens to be
        # loading, shutdown has to outwait it - ten seconds or so - and without this the
        # operator sits looking at a frozen window wondering what he broke.
        self.hide()
        QApplication.processEvents()
        self.ctrl.shutdown()
        super().closeEvent(event)
