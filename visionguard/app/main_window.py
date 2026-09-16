"""MainWindow: layout + signal wiring between widgets and the SystemController.

Layout (light industrial):

    ┌ toolbar ─ START / STOP · Simulate PLC · AREA banner · LEDs · clock ────────┐
    ├ workflow ─ (1) Camera > (2) AI > (3) ROI > (4) PLC > (5) Run ──────────────┤
    │ ┌ video ───────────────────────────┐ ┌ tabs ───────────────────────────┐   │
    │ │ live image + ROI editor          │ │ Status / Camera / AI / ROI /    │   │
    │ ├ quick bar: camera + ROI actions ─┤ │ PLC / I/O Test / Events         │   │
    │ └──────────────────────────────────┘ └─────────────────────────────────┘   │
    ├ system log ───────────────────────────────────────────────────────────────┤
    └ status bar ─ message · safety note ───────────────────────────────────────┘
"""
from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QSizePolicy, QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from ..camera.events.camera_event import CameraEvent
from ..camera.video_camera import VIDEO_EXTENSIONS
from ..config.config_manager import ConfigManager
from ..config.schemas import CameraType, DetectionMode, EventProviderType
from ..logic.camera_event_state_machine import ZoneState
from ..logic.occupancy_state_machine import OccupancyState
from ..roi.roi_model import RoiType
from ..utils.logger import try_import_qt_handler
from ..workers.camera_worker import CameraState
from .controllers.system_controller import SystemController
from .theme import (COLOR_BORDER_STRONG, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, contrast_text, status_caption,
                    status_color)
from .widgets.ai_config_widget import AIConfigWidget
from .widgets.camera_config_widget import CameraConfigWidget
from .widgets.event_monitor_widget import EventMonitorWidget
from .widgets.events_widget import EventsWidget
from .widgets.io_test_widget import IoTestWidget
from .widgets.led_indicator import LedIndicator
from .widgets.log_widget import LogWidget
from .widgets.plc_config_widget import PlcConfigWidget
from .widgets.roi_panel import RoiPanel
from .widgets.status_panel import StatusPanel
from .widgets.video_view import PLACEHOLDER_AI_CAMERA, PLACEHOLDER_YOLO, VideoView
from .widgets.workflow_bar import WorkflowBar

log = logging.getLogger("UI")

SAFETY_NOTE_SHORT = "NOT a safety-rated protective device - monitoring only"
SAFETY_NOTE = ("Monitoring system only - NOT a safety-rated protective device. "
               "Use certified safety PLC / sensors for personnel protection.")

TAB_STATUS, TAB_CAMERA, TAB_EVENT, TAB_AI, TAB_ROI, TAB_PLC, TAB_IO, TAB_EVENTS = range(8)
#: zone created automatically by the video demo when the user has not drawn one yet
DEMO_ROI_POINTS = [(0.08, 0.08), (0.92, 0.08), (0.92, 0.92), (0.08, 0.92)]
STEP_TO_TAB = {0: TAB_CAMERA, 1: TAB_AI, 2: TAB_ROI, 3: TAB_PLC, 4: TAB_STATUS}
STEP_TO_TAB_AI = {0: TAB_CAMERA, 1: TAB_EVENT, 2: TAB_EVENT, 3: TAB_PLC, 4: TAB_STATUS}


def _button(text: str, cls: str = "", size: str = "", tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if cls:
        b.setProperty("class", cls)
    if size:
        b.setProperty("size", size)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("class", "section")
    return lab


def _separator() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setMinimumHeight(26)
    f.setStyleSheet(f"background: {COLOR_BORDER_STRONG}; border: none;")
    return f


class MainWindow(QMainWindow):
    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.setWindowTitle("VisionGuard - Person-in-Area Monitoring (AI Camera / YOLO -> Mitsubishi PLC)")
        self.resize(1560, 960)
        self.ctrl = SystemController(config_manager, self)
        s = self.ctrl.settings

        # ---------------------------------------------------------- state mirrors (for the workflow bar)
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
        self.io_test = IoTestWidget(s.plc.mapping)
        self.events_widget = EventsWidget()
        self.event_monitor = EventMonitorWidget()
        self.log_widget = LogWidget()
        self.workflow = WorkflowBar()

        self._build_toolbar()
        self._build_layout()
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
        self._apply_detection_mode(s.camera.detection_mode)
        self._update_window_title()
        self._update_camera_buttons(CameraState.DISCONNECTED)
        self._update_workflow()

    # ================================================================== toolbar
    def _build_toolbar(self) -> None:
        tb = QToolBar("System")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        self.btn_start = _button("▶  START SYSTEM", "success", "xl", "Bat dau giam sat (F5)")
        self.btn_stop = _button("■  STOP SYSTEM", "danger", "xl", "Dung giam sat (F6)")
        self.btn_stop.setEnabled(False)
        tb.addWidget(self.btn_start)
        tb.addWidget(self.btn_stop)
        tb.addSeparator()

        self.btn_demo = _button("▶  TEST VIDEO", "primary", "",
                                "Chon 1 file video va chay thu ca he thong: camera + AI + ROI + PLC mo phong (F9)")
        self.btn_demo.setMinimumHeight(34)
        tb.addWidget(self.btn_demo)

        self.btn_sim = _button("PLC SIMULATION", "", "", "Bat: khong can PLC that, moi tin hieu ghi vao bo nho ao")
        self.btn_sim.setCheckable(True)
        self.btn_sim.setMinimumHeight(34)
        self.btn_sim.setChecked(self.ctrl.settings.plc.simulation_mode)
        self._style_sim_button(self.btn_sim.isChecked())
        tb.addWidget(self.btn_sim)
        tb.addSeparator()

        self.lbl_toolbar_area = QLabel(status_caption("STOPPED"))
        self.lbl_toolbar_area.setMinimumWidth(300)
        self.lbl_toolbar_area.setMinimumHeight(38)
        self.lbl_toolbar_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(self.lbl_toolbar_area)
        tb.addSeparator()

        leds = QWidget()
        leds.setProperty("class", "transparent")
        hl = QHBoxLayout(leds)
        hl.setContentsMargins(6, 0, 6, 0)
        hl.setSpacing(5)
        self.led_system = LedIndicator()
        self.led_camera = LedIndicator()
        self.led_ai = LedIndicator()
        self.led_plc = LedIndicator()
        for led, name, tip in ((self.led_system, "SYSTEM", "Trang thai he thong"),
                               (self.led_camera, "CAMERA", "Ket noi camera"),
                               (self.led_ai, "AI", "Model YOLO / detection"),
                               (self.led_plc, "PLC", "Ket noi PLC")):
            led.setToolTip(tip)
            hl.addWidget(led)
            lab = QLabel(name)
            lab.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 8.5pt; font-weight: 700; letter-spacing: 0.5px;")
            lab.setToolTip(tip)
            hl.addWidget(lab)
            hl.addSpacing(10)
        tb.addWidget(leds)

        spacer = QWidget()
        spacer.setProperty("class", "transparent")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.lbl_clock = QLabel("")
        self.lbl_clock.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9.5pt;")
        tb.addWidget(self.lbl_clock)
        clock = QTimer(self)
        clock.timeout.connect(self._update_clock)
        clock.start(1000)
        self._update_clock()

        act_start = QAction("Start system", self)
        act_start.setShortcut(QKeySequence("F5"))
        act_start.triggered.connect(self.ctrl.start_system)
        act_stop = QAction("Stop system", self)
        act_stop.setShortcut(QKeySequence("F6"))
        act_stop.triggered.connect(self.ctrl.stop_system)
        act_full = QAction("Toggle fullscreen", self)
        act_full.setShortcut(QKeySequence("F11"))
        act_full.triggered.connect(self.toggle_fullscreen)
        act_demo = QAction("Test with a video file", self)
        act_demo.setShortcut(QKeySequence("F9"))
        act_demo.triggered.connect(self.run_video_demo)
        self.addActions([act_start, act_stop, act_full, act_demo])

    def _style_sim_button(self, on: bool) -> None:
        self.btn_sim.setText("PLC SIMULATION: ON" if on else "PLC SIMULATION: OFF")

    # ================================================================== layout
    def _build_layout(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.setSpacing(8)

        wf_card = QFrame()
        wf_card.setProperty("class", "card")
        wf_lay = QHBoxLayout(wf_card)
        wf_lay.setContentsMargins(8, 7, 8, 7)
        wf_lay.addWidget(self.workflow)
        outer.addWidget(wf_card)

        # ---------------- left: video + quick actions
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        video_card = QFrame()
        video_card.setProperty("class", "card")
        vl = QVBoxLayout(video_card)
        vl.setContentsMargins(3, 3, 3, 3)
        vl.addWidget(self.video)
        ll.addWidget(video_card, 1)
        ll.addWidget(self._build_quick_bar())

        # ---------------- right: tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self.status_panel, "Status")
        self.tabs.addTab(self.camera_cfg, "1 Camera")
        self.tabs.addTab(self.event_monitor, "2 AI Event")
        self.tabs.addTab(self.ai_cfg, "2 AI Model")
        self.tabs.addTab(self.roi_panel, "3 ROI")
        self.tabs.addTab(self.plc_cfg, "4 PLC")
        self.tabs.addTab(self.io_test, "I/O")
        self.tabs.addTab(self.events_widget, "Events")
        self.tabs.setMinimumWidth(430)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setUsesScrollButtons(False)

        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.addWidget(left)
        hsplit.addWidget(self.tabs)
        hsplit.setStretchFactor(0, 3)
        hsplit.setStretchFactor(1, 1)
        hsplit.setSizes([990, 510])

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
        vsplit.setSizes([740, 175])
        outer.addWidget(vsplit, 1)
        self.setCentralWidget(central)

        # ---------------- status bar
        self.lbl_safety = QLabel("⚠  " + SAFETY_NOTE_SHORT)
        self.lbl_safety.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        self.lbl_safety.setToolTip(SAFETY_NOTE)
        self.lbl_safety.setMinimumWidth(0)
        self.statusBar().addPermanentWidget(self.lbl_safety)
        self.statusBar().showMessage("Ready")

    def _build_quick_bar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("class", "toolbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(7)

        lay.addWidget(_section("CAMERA"))
        self.btn_q_connect = _button("Connect", "", "sm", "Ket noi camera dang chon o tab Camera")
        self.btn_q_start = _button("Start", "success", "sm", "Bat luong hinh")
        self.btn_q_stop = _button("Stop", "", "sm", "Dung luong hinh")
        for b in (self.btn_q_connect, self.btn_q_start, self.btn_q_stop):
            lay.addWidget(b)
        lay.addWidget(_separator())

        lay.addWidget(_section("ROI"))
        self.btn_q_add = _button("+ ROI", "primary", "sm", "Ve vung giam sat: click tung diem, double-click de dong")
        self.btn_q_ex = _button("+ Exclusion", "", "sm", "Ve vung loai tru (nguoi trong vung nay khong tinh)")
        self.btn_q_finish = _button("Finish", "", "sm", "Dong polygon dang ve (Enter)")
        self.btn_q_edit = _button("Edit", "", "sm", "Keo dinh de sua vung; Shift+click canh de them dinh")
        self.btn_q_edit.setCheckable(True)
        self.btn_q_save = _button("Save ROI", "success", "sm", "Luu cau hinh ROI ra config/roi_config.json")
        self.btn_q_finish.setEnabled(False)
        for b in (self.btn_q_add, self.btn_q_ex, self.btn_q_finish, self.btn_q_edit, self.btn_q_save):
            lay.addWidget(b)
        lay.addWidget(_separator())

        self.lbl_video_hint = QLabel("Tip: draw a zone (step 3), then START SYSTEM")
        self.lbl_video_hint.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        self.lbl_video_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        self.btn_demo.clicked.connect(self.run_video_demo)
        self.btn_sim.toggled.connect(self._sim_toggled_toolbar)
        self.workflow.step_clicked.connect(self._workflow_clicked)

        # controller -> UI
        c.frame_ready.connect(self.video.set_frame)
        c.result_ready.connect(self._on_result)
        c.camera_state.connect(self._on_camera_state)
        c.camera_fps.connect(self.status_panel.set_fps_camera)
        c.camera_info.connect(lambda info: self.camera_cfg.set_status(info.summary(), ok=True))
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

    # ================================================================== one-click video demo
    def run_video_demo(self) -> None:
        """Pick a video file, then bring the whole chain up on it in one go.

        Camera -> YOLO -> ROI -> debounce -> PLC. Anything still missing is filled in:
        a default monitored zone when none is drawn, and PLC simulation when no PLC is connected.
        """
        pattern = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a video file to test the system with",
            self.ctrl.settings.camera.video.path,
            f"Video files ({pattern});;All files (*)")
        if not path:
            return
        log.info("Video demo requested: %s", path)
        if self.ctrl.running:
            self.ctrl.stop_system()

        # 1 - camera: switch to the video file, looping and at the file's own frame rate
        cfg = deepcopy(self.ctrl.settings.camera)
        cfg.camera_type = CameraType.VIDEO.value
        cfg.video.path = path
        cfg.video.loop = True
        cfg.video.realtime = True
        cfg.video.start_paused = False
        if cfg.is_ai_camera and cfg.ai_camera.provider_enum != EventProviderType.MOCK:
            # A video file cannot send AI events, so the demo runs on simulated ones.
            cfg.ai_camera.event_provider = EventProviderType.MOCK.value
            cfg.ai_camera.rtsp_url = ""
            log.info("Video demo: event provider switched to simulated events")
            self._show_message("Video demo: event provider switched to 'Simulated events' - "
                               "use the AI Event tab to inject person enter / exit")
        self.camera_cfg.set_config(cfg)
        self.ctrl.apply_camera_config(cfg)
        self._apply_detection_mode(cfg.detection_mode)

        # 2 - ROI: without an include zone the area could never become OCCUPIED
        #     (AI Camera mode has no PC side ROI - the camera owns the zones)
        if not cfg.is_ai_camera and not [r for r in self.ctrl.roi_manager.include_rois()
                                         if r.enabled and r.is_valid()]:
            roi = self.ctrl.roi_manager.create(RoiType.INCLUDE, DEMO_ROI_POINTS, name="Demo Zone")
            self.ctrl.save_rois()   # keep the zone (and the workflow step) in a finished state
            log.info("Video demo: created default zone %s (edit or delete it in tab 3 ROI)", roi.id)

        # 3 - PLC: keep a real connection if there is one, otherwise demo against the virtual PLC
        if not self._plc_connected and not self.ctrl.settings.plc.simulation_mode:
            log.info("Video demo: no PLC connected - switching to simulation mode")
            self.btn_sim.setChecked(True)

        # 4 - run: open and start the new source explicitly (start_system() only asks for a start when
        # it already knows the camera is not streaming, and that state is still the previous source's).
        self.ctrl.camera_connect()
        self.ctrl.camera_start()
        self.ctrl.start_system()
        self.tabs.setCurrentIndex(TAB_STATUS)
        self._show_message(f"Video demo: {Path(path).name} - camera, AI, ROI and PLC simulation are starting...")

    # ================================================================== small helpers
    def toggle_fullscreen(self) -> None:
        """F11: borderless fullscreen <-> maximized (the normal working state)."""
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def _update_clock(self) -> None:
        self.lbl_clock.setText(datetime.now().strftime("%Y-%m-%d   %H:%M:%S"))

    def _show_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 10000)

    def _workflow_clicked(self, step: int) -> None:
        mapping = STEP_TO_TAB_AI if self._ai_camera_mode else STEP_TO_TAB
        self.tabs.setCurrentIndex(mapping.get(step, TAB_STATUS))
        if step == 4 and self.btn_start.isEnabled():
            self.btn_start.setFocus()

    def _start_drawing(self, rtype: RoiType) -> None:
        self.tabs.setCurrentIndex(TAB_ROI)
        self.video.start_drawing(rtype)

    def _quick_camera_connect(self) -> None:
        self.camera_cfg.apply_now()   # push the form values before connecting
        self.ctrl.camera_connect()

    def _quick_camera_start(self) -> None:
        self.camera_cfg.apply_now()
        self.ctrl.camera_start()

    # ================================================================== status slots
    def _apply_area(self, status: str) -> None:
        caption = status_caption(status)
        color = status_color(status)
        self.status_panel.set_area_status(status, caption)
        self.video.set_area_status(caption if status != "STOPPED" else "", color)
        self.lbl_toolbar_area.setText(caption)
        self.lbl_toolbar_area.setStyleSheet(
            f"background: {color}; color: {contrast_text(color)}; font-weight: 800; font-size: 13pt;"
            f" padding: 7px 18px; border-radius: 6px; letter-spacing: 1.5px;")

    def _on_system_state(self, state: str, msg: str) -> None:
        self._system_state = state
        self._system_message = msg
        led = {"RUNNING": "ok", "FAULT": "error", "STARTING": "busy"}.get(state, "off")
        self.led_system.set_state(led)
        self.status_panel.set_system(state, msg)
        running = self.ctrl.running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.video.set_overlay_info(msg or "")
        if msg:
            self.status_panel.set_detail(msg)
        elif state == "RUNNING":
            self.status_panel.set_detail("Monitoring active")
        elif state == "STOPPED":
            self.status_panel.set_detail("System stopped - press START SYSTEM to begin")
        self._update_workflow()

    def _on_camera_state(self, state: str, msg: str) -> None:
        if state in ("TEST_OK", "TEST_FAIL"):
            self.camera_cfg.set_status(msg, ok=(state == "TEST_OK"))
            return
        self._camera_state = state
        led = {CameraState.STREAMING: "ok", CameraState.CONNECTED: "busy", CameraState.CONNECTING: "busy",
               CameraState.RECONNECTING: "warn", CameraState.FINISHED: "warn"}.get(
            state, "error" if state in (CameraState.LOST, CameraState.ERROR) else "off")
        self.led_camera.set_state(led)
        self.status_panel.set_camera(state, led)
        self.camera_cfg.set_camera_state(state)
        self._update_camera_buttons(state)
        ok = None if state in (CameraState.DISCONNECTED, CameraState.CONNECTING) else state in (
            CameraState.STREAMING, CameraState.CONNECTED)
        self.camera_cfg.set_status(f"{state}: {msg}" if msg else state, ok)
        if state in (CameraState.DISCONNECTED, CameraState.LOST, CameraState.ERROR):
            self.status_panel.set_fps_camera(0.0)
        if state == CameraState.DISCONNECTED:
            self.video.clear_image()
        self._update_workflow()

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
            self.led_ai.set_state(led)
            self.status_panel.set_ai("MODEL LOADED" if loaded else ("LOAD FAILED" if failed else "STOPPED"), led)
        self._update_workflow()

    def _on_detection_state(self, running: bool, msg: str) -> None:
        self._detecting = running
        self.ai_cfg.set_detection_running(running)
        led = "ok" if running else ("busy" if self._model_loaded else "off")
        if not self._ai_camera_mode:
            self.led_ai.set_state(led)
        self.status_panel.set_ai("RUNNING" if running else ("MODEL LOADED" if self._model_loaded else "STOPPED"), led)
        self.video.set_display_mode("result" if running else "raw")
        if not running:
            self.video.clear_result()
            self.status_panel.set_ai_perf(0.0, 0.0)
            self.status_panel.set_counts(0, 0, 0, 0, "")
        self._update_workflow()

    def _on_plc_state(self, connected: bool, msg: str) -> None:
        self._plc_connected = connected
        sim = self.ctrl.settings.plc.simulation_mode
        led = "ok" if (connected and not sim) else ("warn" if connected else "error")
        self.led_plc.set_state(led)
        label = ("SIMULATION" if sim else "CONNECTED") if connected else "DISCONNECTED"
        self.status_panel.set_plc(label, led)
        self.plc_cfg.set_connected(connected)
        self.plc_cfg.set_status(f"{label}: {msg}" if msg else label, connected)
        if not connected:
            self.status_panel.set_heartbeat(None, self.ctrl.settings.plc.heartbeat.enabled)
        self._update_workflow()

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
        self._update_workflow()

    def _on_editor_mode(self, mode: str) -> None:
        self.roi_panel.set_mode(mode)
        self.btn_q_edit.blockSignals(True)
        self.btn_q_edit.setChecked(mode == "edit")
        self.btn_q_edit.blockSignals(False)
        self.btn_q_finish.setEnabled(mode == "drawing")
        self.lbl_video_hint.setText({
            "drawing": "Click to add points  ·  double-click / Enter to close  ·  right-click undo  ·  Esc cancel",
            "edit": "Drag a vertex  ·  Shift+click an edge to insert  ·  right-click a vertex to remove  ·  Delete removes the ROI",
        }.get(mode, "Tip: draw a zone (step 3), then START SYSTEM"))

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
        self._update_window_title()
        self.tabs.setTabVisible(TAB_EVENT, ai)
        self.tabs.setTabVisible(TAB_AI, not ai)
        self.tabs.setTabVisible(TAB_ROI, not ai)
        # keep the numbers of the *visible* tabs in step with the workflow bar
        self.tabs.setTabText(TAB_PLC, "3 PLC" if ai else "4 PLC")
        self.status_panel.set_count_labels(ai)
        if ai:
            self.workflow.set_step_title(1, "AI Events", "Kenh su kien AI cua camera (ISAPI / HTTP)")
            self.workflow.set_step_title(2, "Regions", "Gan region id cua camera vao ten vung va bit PLC")
        else:
            self.workflow.set_step_title(1, "AI Model", "Nap model YOLO pretrained de phat hien person")
            self.workflow.set_step_title(2, "ROI Zones", "Ve vung giam sat (polygon) tren hinh")
        provider = self.ctrl.settings.camera.ai_camera.provider_enum
        self.event_monitor.set_simulation_available(provider == EventProviderType.MOCK)
        for btn in (self.btn_q_add, self.btn_q_ex, self.btn_q_finish, self.btn_q_edit, self.btn_q_save):
            btn.setEnabled(not ai)
            btn.setToolTip("Zones are configured inside the camera in AI Camera mode "
                           "(see the AI Event tab)" if ai else btn.toolTip())
        if ai:
            self.lbl_video_hint.setText("AI Camera mode: the camera detects people and sends events. "
                                        "Map its regions in the 'AI Event' tab.")
        self.video.set_display_mode("raw" if ai else self.video._display_mode)
        self._update_workflow()

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
        if self._ai_camera_mode:
            self.led_ai.set_state(led)
        self._update_workflow()

    def _on_zone_states(self, states) -> None:
        self.event_monitor.update_zone_states(states)
        occupied = [rid for rid, st in states.items() if getattr(st, "occupied", False)]
        counts = self.ctrl.event_state
        self.status_panel.set_counts(counts.total_person_count(), len(occupied), 0,
                                     counts.ignored_non_human, ", ".join(occupied))

    def _on_regions_changed(self) -> None:
        self.event_monitor.set_regions(self.ctrl.region_mapping.all(), self.ctrl.zone_states())
        self._update_workflow()

    def _save_regions(self) -> None:
        self.ctrl.save_regions()
        self.event_monitor.set_regions_dirty(False)

    # ================================================================== workflow bar
    def _update_workflow(self) -> None:
        # 1 - camera
        cam = {
            CameraState.STREAMING: ("done", "Streaming"),
            CameraState.CONNECTED: ("active", "Connected - press Start"),
            CameraState.CONNECTING: ("active", "Connecting..."),
            CameraState.RECONNECTING: ("warn", "Reconnecting..."),
            CameraState.FINISHED: ("warn", "Video finished"),
            CameraState.LOST: ("error", "Camera lost"),
            CameraState.ERROR: ("error", "Connection error"),
        }.get(self._camera_state, ("todo", "Not connected"))
        self.workflow.set_step(0, *cam)

        ai_mode = self._ai_camera_mode
        if ai_mode:
            self._update_workflow_ai_camera()
            return

        # 2 - AI model
        if self._detecting:
            ai = ("done", "Detecting persons")
        elif self._model_error:
            ai = ("error", "Model load failed")
        elif self._model_loaded:
            ai = ("active", "Model loaded")
        else:
            ai = ("todo", "Model not loaded")
        self.workflow.set_step(1, *ai)

        # 3 - ROI
        includes = [r for r in self.ctrl.roi_manager.include_rois() if r.enabled and r.is_valid()]
        excludes = [r for r in self.ctrl.roi_manager.exclude_rois() if r.enabled and r.is_valid()]
        if not includes:
            roi = ("warn", "No monitored zone yet")
        else:
            detail = f"{len(includes)} zone(s)" + (f" + {len(excludes)} exclusion" if excludes else "")
            roi = ("active" if self.ctrl.roi_manager.dirty else "done",
                   detail + (" · unsaved" if self.ctrl.roi_manager.dirty else ""))
        self.workflow.set_step(2, *roi)

        self._update_workflow_plc()

        # 5 - run
        run = {
            "RUNNING": ("done", "Monitoring active"),
            "FAULT": ("error", self._system_message or "Fault"),
            "STARTING": ("active", "Starting..."),
        }.get(self._system_state, ("todo", "Press START SYSTEM (F5)"))
        self.workflow.set_step(4, *run)

    def _update_workflow_plc(self) -> None:
        sim = self.ctrl.settings.plc.simulation_mode
        if self._plc_connected:
            detail = "Simulation (virtual PLC)" if sim else f"Connected {self.ctrl.settings.plc.connection.ip}"
            self.workflow.set_step(3, "done", detail)
        else:
            self.workflow.set_step(3, "error" if self.ctrl.running else "todo", "Not connected")

    def _update_workflow_ai_camera(self) -> None:
        """Steps 2 and 3 mean 'event channel' and 'region mapping' in AI Camera mode."""
        channel = {
            "ONLINE": ("done", "Receiving camera events"),
            "CONNECTING": ("active", "Connecting..."),
            "RECONNECTING": ("warn", self._event_message or "Reconnecting..."),
            "ERROR": ("error", self._event_message or "Cannot open the event channel"),
            "DISABLED": ("todo", "Not used"),
        }.get(self._event_channel, ("todo", "Not connected"))
        self.workflow.set_step(1, *channel)

        mapped = [r for r in self.ctrl.region_mapping.all() if r.enabled and r.plc_device]
        known = self.ctrl.region_mapping.all()
        if not known:
            regions = ("warn", "No camera region seen yet")
        elif not mapped:
            regions = ("warn", f"{len(known)} region(s), none mapped to a PLC device")
        else:
            regions = ("done", f"{len(mapped)} of {len(known)} region(s) mapped")
        self.workflow.set_step(2, *regions)
        self._update_workflow_plc()

        run = {
            "RUNNING": ("done", "Monitoring active"),
            "FAULT": ("error", self._system_message or "Fault"),
            "STARTING": ("active", "Starting..."),
        }.get(self._system_state, ("todo", "Press START SYSTEM (F5)"))
        self.workflow.set_step(4, *run)

    # ================================================================== ROI / events actions
    def _delete_roi(self, roi_id: str) -> None:
        if roi_id:
            self.ctrl.delete_roi(roi_id)

    def _clear_rois(self) -> None:
        if QMessageBox.question(self, "Clear all ROI",
                                "Delete ALL ROIs (including exclusion zones)?") == QMessageBox.StandardButton.Yes:
            self.ctrl.clear_rois()

    def _save_rois(self) -> None:
        self.ctrl.save_rois()
        self.roi_panel.set_dirty(self.ctrl.roi_manager.dirty)
        self._update_workflow()

    def _clear_events(self) -> None:
        if QMessageBox.question(self, "Clear history",
                                "Delete the whole event history?") == QMessageBox.StandardButton.Yes:
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
        self._update_workflow()

    def _sim_toggled_toolbar(self, on: bool) -> None:
        self._style_sim_button(on)
        self.plc_cfg.set_simulation(on)
        self.ctrl.set_simulation(on)
        self._update_workflow()

    def _sim_toggled_tab(self, on: bool) -> None:
        self.btn_sim.blockSignals(True)
        self.btn_sim.setChecked(on)
        self.btn_sim.blockSignals(False)
        self._style_sim_button(on)
        self.ctrl.set_simulation(on)
        self._update_workflow()

    # ================================================================== close
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.ctrl.running:
            if QMessageBox.question(self, "Exit", "The system is RUNNING. Stop monitoring and exit?") != \
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
