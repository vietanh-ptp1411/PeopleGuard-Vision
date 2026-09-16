"""Status panel: big area banner + LEDs + live counters, readable from a distance."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..theme import (COLOR_ERROR, COLOR_TEXT, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, contrast_text, status_caption,
                     status_color)
from .led_indicator import StatusRow


class MetricTile(QFrame):
    """A compact number tile used for the detection counters."""

    def __init__(self, caption: str, value: str = "0", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setMinimumHeight(56)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(0)
        self.value = QLabel(value)
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 18pt; font-weight: 800;")
        self.caption = QLabel(caption)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 0.5px;")
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color or COLOR_TEXT}; font-size: 18pt; font-weight: 800;")


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
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(10)

        # ---------------------------------------------------------- big area status
        self.area_label = QLabel(status_caption("STOPPED"))
        self.area_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area_label.setMinimumHeight(78)
        self._style_area("STOPPED")
        lay.addWidget(self.area_label)

        self.detail_label = QLabel("System stopped")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9.5pt;")
        lay.addWidget(self.detail_label)

        # ---------------------------------------------------------- connections
        g1 = QGroupBox("SYSTEM STATUS")
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

        g_last = QGroupBox("LAST CAMERA EVENT")
        l_last = QVBoxLayout(g_last)
        self.last_event_label = QLabel("-")
        self.last_event_label.setWordWrap(True)
        self.last_event_label.setStyleSheet(f"color: {COLOR_TEXT}; font-weight: 600;")
        self.last_event_time = QLabel("")
        self.last_event_time.setProperty("class", "muted")
        l_last.addWidget(self.last_event_label)
        l_last.addWidget(self.last_event_time)
        self.group_last_event = g_last
        lay.addWidget(g_last)

        # ---------------------------------------------------------- detection tiles
        g2 = QGroupBox("DETECTION")
        l2 = QGridLayout(g2)
        l2.setSpacing(6)
        self.tile_total = MetricTile("PERSONS\nDETECTED")
        self.tile_in_roi = MetricTile("IN ROI")
        self.tile_outside = MetricTile("OUTSIDE ROI")
        self.tile_ignored = MetricTile("IGNORED\n(EXCLUSION)")
        l2.addWidget(self.tile_total, 0, 0)
        l2.addWidget(self.tile_in_roi, 0, 1)
        l2.addWidget(self.tile_outside, 1, 0)
        l2.addWidget(self.tile_ignored, 1, 1)
        self.rois_row = StatusRow("Occupied ROIs", "-")
        self.rois_row.led.hide()
        l2.addWidget(self.rois_row, 2, 0, 1, 2)
        lay.addWidget(g2)

        # ---------------------------------------------------------- performance
        g3 = QGroupBox("PERFORMANCE")
        l3 = QVBoxLayout(g3)
        l3.setSpacing(1)
        self.fps_cam_row = StatusRow("FPS Camera", "0")
        self.fps_ai_row = StatusRow("FPS AI", "0")
        self.infer_row = StatusRow("Inference", "0 ms")
        self.plc_lat_row = StatusRow("PLC Latency", "0 ms")
        self.dropped_row = StatusRow("Frames dropped", "0")
        for r in (self.fps_cam_row, self.fps_ai_row, self.infer_row, self.plc_lat_row, self.dropped_row):
            r.led.hide()
            l3.addWidget(r)
        lay.addWidget(g3)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        lay.addWidget(self.info_label)
        lay.addStretch(1)

    # ------------------------------------------------------------------ setters
    def _style_area(self, status: str) -> None:
        color = status_color(status)
        self.area_label.setStyleSheet(
            f"background-color: {color}; color: {contrast_text(color)}; font-size: 23pt; font-weight: 800;"
            f" letter-spacing: 2px; border-radius: 10px; border: 1px solid rgba(0,0,0,30);")

    def set_area_status(self, status: str, text: str | None = None) -> None:
        self.area_label.setText(text or status_caption(status))
        self._style_area(status)

    def set_system(self, state: str, message: str = "") -> None:
        led = {"RUNNING": "ok", "FAULT": "error", "STOPPED": "off", "STARTING": "busy"}.get(state, "off")
        self.system_row.set(state, led)
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
            self.tile_in_roi.caption.setText("IN ROI")
            self.tile_ignored.caption.setText("IGNORED\n(EXCLUSION)")

    def set_ai_camera_mode(self, ai_camera: bool) -> None:
        """Show only the rows that mean something in the active mode."""
        self.event_row.setVisible(ai_camera)
        self.group_last_event.setVisible(ai_camera)
        self.ai_row.setVisible(not ai_camera)
        self.tile_outside.setVisible(not ai_camera)
        self.tile_ignored.setVisible(not ai_camera)

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
        self.fps_cam_row.set_value(f"{fps:.1f}")

    def set_ai_perf(self, fps: float, inference_ms: float) -> None:
        self.fps_ai_row.set_value(f"{fps:.1f}")
        self.infer_row.set_value(f"{inference_ms:.1f} ms")

    def set_plc_latency(self, ms: float) -> None:
        self.plc_lat_row.set_value(f"{ms:.1f} ms")

    def set_dropped(self, dropped: int) -> None:
        self.dropped_row.set_value(str(dropped))

    def set_info(self, text: str) -> None:
        self.info_label.setText(text)
