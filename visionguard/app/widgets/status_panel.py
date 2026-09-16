"""Overview tab: system state, connections, live counters and performance.

The big AREA CLEAR / PERSON DETECTED banner lives on the command bar, where it is visible
from every tab - repeating it here would just push the numbers below the fold. What this
panel adds is the detail behind that one word: which link is up, what the camera last
sent, and how fast the chain is running.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..theme import (COLOR_BORDER, COLOR_ERROR, COLOR_TEXT, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, state_colors,
                     status_caption)
from .led_indicator import LedIndicator, StatusRow


class MetricTile(QFrame):
    """A compact number tile used for the detection counters."""

    def __init__(self, caption: str, value: str = "0", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setMinimumHeight(60)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 7, 8, 7)
        lay.setSpacing(1)
        self.value = QLabel(value)
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 19pt; font-weight: 800;")
        self.caption = QLabel(caption)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 7.5pt;"
                                   f" font-weight: 700; letter-spacing: 0.6px;")
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color or COLOR_TEXT}; font-size: 19pt; font-weight: 800;")


class MiniStat(QWidget):
    """label above, value below - four of these fit on one row."""

    def __init__(self, label: str, value: str = "-", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)
        self.caption = QLabel(label)
        self.caption.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 8pt;")
        self.value = QLabel(value)
        self.value.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 11pt; font-weight: 700;")
        lay.addWidget(self.caption)
        lay.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class StatusPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 8, 4, 6)
        lay.setSpacing(9)

        # ---------------------------------------------------------- headline state
        head = QFrame()
        head.setProperty("class", "card")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(3)
        line = QGridLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setHorizontalSpacing(9)
        self.state_led = LedIndicator(diameter=13)
        self.state_label = QLabel("STOPPED")
        self.state_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 14pt; font-weight: 800;"
                                       f" letter-spacing: 0.6px;")
        self.area_value = QLabel("")
        self.area_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(self.state_led, 0, 0)
        line.addWidget(self.state_label, 0, 1)
        line.setColumnStretch(2, 1)
        line.addWidget(self.area_value, 0, 3)
        hl.addLayout(line)
        self.detail_label = QLabel("Hệ thống đã dừng")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9.5pt;")
        hl.addWidget(self.detail_label)
        lay.addWidget(head)

        # ---------------------------------------------------------- connections
        g1 = QGroupBox("CONNECTIONS")
        l1 = QVBoxLayout(g1)
        l1.setSpacing(1)
        self.system_row = StatusRow("System", "STOPPED")
        self.mode_row = StatusRow("Detection source", "-")
        self.mode_row.led.hide()
        self.camera_row = StatusRow("Video stream", "DISCONNECTED")
        self.event_row = StatusRow("AI event channel", "-")
        self.ai_row = StatusRow("AI Detection", "STOPPED")
        self.plc_row = StatusRow("PLC", "DISCONNECTED")
        self.heartbeat_row = StatusRow("Heartbeat", "-")
        for r in (self.system_row, self.mode_row, self.camera_row, self.event_row, self.ai_row,
                  self.plc_row, self.heartbeat_row):
            l1.addWidget(r)
        lay.addWidget(g1)

        # ---------------------------------------------------------- detection tiles
        g2 = QGroupBox("DETECTION")
        l2 = QGridLayout(g2)
        l2.setSpacing(7)
        self.tile_total = MetricTile("PERSONS\nDETECTED")
        self.tile_in_roi = MetricTile("IN ZONE")
        self.tile_outside = MetricTile("OUTSIDE")
        self.tile_ignored = MetricTile("IGNORED\n(EXCLUSION)")
        l2.addWidget(self.tile_total, 0, 0)
        l2.addWidget(self.tile_in_roi, 0, 1)
        l2.addWidget(self.tile_outside, 1, 0)
        l2.addWidget(self.tile_ignored, 1, 1)
        self.rois_row = StatusRow("Occupied zones", "-")
        self.rois_row.led.hide()
        l2.addWidget(self.rois_row, 2, 0, 1, 2)

        sep = QFrame()
        sep.setProperty("class", "hline")
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {COLOR_BORDER}; border: none;")
        l2.addWidget(sep, 3, 0, 1, 2)
        self.last_event_caption = QLabel("LAST CAMERA EVENT")
        self.last_event_caption.setProperty("class", "title")
        self.last_event_label = QLabel("-")
        self.last_event_label.setWordWrap(True)
        self.last_event_label.setStyleSheet(f"color: {COLOR_TEXT}; font-weight: 600;")
        self.last_event_time = QLabel("")
        self.last_event_time.setProperty("class", "muted")
        l2.addWidget(self.last_event_caption, 4, 0, 1, 2)
        l2.addWidget(self.last_event_label, 5, 0, 1, 2)
        l2.addWidget(self.last_event_time, 6, 0, 1, 2)
        lay.addWidget(g2)

        # ---------------------------------------------------------- performance
        g3 = QGroupBox("PERFORMANCE")
        l3 = QGridLayout(g3)
        l3.setSpacing(6)
        self.stat_fps_cam = MiniStat("FPS camera", "0")
        self.stat_fps_ai = MiniStat("FPS AI", "0")
        self.stat_infer = MiniStat("Inference", "0 ms")
        self.stat_plc = MiniStat("PLC latency", "0 ms")
        self.stat_dropped = MiniStat("Frames dropped", "0")
        l3.addWidget(self.stat_fps_cam, 0, 0)
        l3.addWidget(self.stat_fps_ai, 0, 1)
        l3.addWidget(self.stat_infer, 0, 2)
        l3.addWidget(self.stat_plc, 1, 0)
        l3.addWidget(self.stat_dropped, 1, 1)
        lay.addWidget(g3)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        lay.addWidget(self.info_label)
        lay.addStretch(1)

    # ------------------------------------------------------------------ setters
    def set_area_status(self, status: str, text: str | None = None) -> None:
        """The banner itself is on the command bar; here it is one coloured line."""
        if status in ("STOPPED", "STARTING"):     # the same word is already on the left
            self.area_value.setText("")
            return
        led = {"CLEAR": "ok", "OCCUPIED": "error", "FAULT": "warn"}.get(status, "off")
        color, _fill = state_colors(led)
        self.area_value.setText(text or status_caption(status))
        self.area_value.setStyleSheet(f"color: {color}; font-size: 10pt; font-weight: 700;")

    def set_system(self, state: str, message: str = "") -> None:
        led = {"RUNNING": "ok", "FAULT": "error", "STOPPED": "off", "STARTING": "busy"}.get(state, "off")
        self.system_row.set(state, led)
        self.state_led.set_state(led)
        color, _fill = state_colors(led)
        self.state_label.setText(state)
        self.state_label.setStyleSheet(f"color: {color if led != 'off' else COLOR_TEXT}; font-size: 14pt;"
                                       f" font-weight: 800; letter-spacing: 0.6px;")
        if message:
            self.detail_label.setText(message)

    def set_detail(self, message: str) -> None:
        self.detail_label.setText(message)

    def set_camera(self, text: str, led: str) -> None:
        self.camera_row.set(text, led)

    def set_ai(self, text: str, led: str) -> None:
        self.ai_row.set(text, led)

    def set_event_channel(self, text: str, led: str) -> None:
        self.event_row.set(text, led)

    def set_detection_mode(self, text: str) -> None:
        self.mode_row.set_value(text)

    def set_count_labels(self, ai_camera: bool) -> None:
        """The two big numbers mean different things in each detection mode."""
        if ai_camera:
            self.tile_total.caption.setText("PEOPLE\nCOUNTED")
            self.tile_in_roi.caption.setText("ZONES\nOCCUPIED")
            self.tile_ignored.caption.setText("IGNORED\n(NOT HUMAN)")
        else:
            self.tile_total.caption.setText("PERSONS\nDETECTED")
            self.tile_in_roi.caption.setText("IN ZONE")
            self.tile_ignored.caption.setText("IGNORED\n(EXCLUSION)")

    def set_ai_camera_mode(self, ai_camera: bool) -> None:
        """Show only the rows that mean something in the active mode."""
        self.event_row.setVisible(ai_camera)
        self.last_event_caption.setVisible(ai_camera)
        self.last_event_label.setVisible(ai_camera)
        self.last_event_time.setVisible(ai_camera)
        self.ai_row.setVisible(not ai_camera)
        self.tile_outside.setVisible(not ai_camera)
        self.tile_ignored.setVisible(not ai_camera)
        self.stat_fps_ai.setVisible(not ai_camera)
        self.stat_infer.setVisible(not ai_camera)

    def set_last_event(self, text: str, when: str = "") -> None:
        self.last_event_label.setText(text or "-")
        self.last_event_time.setText(when)

    def set_plc(self, text: str, led: str) -> None:
        self.plc_row.set(text, led)

    def set_heartbeat(self, value: bool | None, enabled: bool = True) -> None:
        if not enabled:
            self.heartbeat_row.set("DISABLED", "off")
        elif value is None:
            self.heartbeat_row.set("-", "off")
        else:
            self.heartbeat_row.set("1" if value else "0", "ok" if value else "busy")

    def set_counts(self, total: int, in_roi: int, outside: int, ignored: int, occupied_rois: str) -> None:
        self.tile_total.set_value(str(total))
        self.tile_in_roi.set_value(str(in_roi), COLOR_ERROR if in_roi else None)
        self.tile_outside.set_value(str(outside))
        self.tile_ignored.set_value(str(ignored))
        self.rois_row.set_value(occupied_rois or "-")
        self.rois_row.set_value_color(COLOR_ERROR if occupied_rois else None)

    def set_fps_camera(self, fps: float) -> None:
        self.stat_fps_cam.set_value(f"{fps:.1f}")

    def set_ai_perf(self, fps: float, inference_ms: float) -> None:
        self.stat_fps_ai.set_value(f"{fps:.1f}")
        self.stat_infer.set_value(f"{inference_ms:.1f} ms")

    def set_plc_latency(self, ms: float) -> None:
        self.stat_plc.set_value(f"{ms:.1f} ms")

    def set_dropped(self, dropped: int) -> None:
        self.stat_dropped.set_value(str(dropped))

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)
