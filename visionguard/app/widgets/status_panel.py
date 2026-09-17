"""Overview tab: the area verdict, what the camera last said, and how fast the chain runs.

The four state chips on the command bar cover the links. What this page adds is the one
word an operator acts on - AREA CLEAR or PERSON DETECTED - large enough to read from the
machine, plus the diagnostics that do not fit in a chip.
"""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..theme import COLOR_TEXT, COLOR_TEXT_MUTED
from .chrome import AreaBanner


class MiniStat(QWidget):
    """label above, value below - three of these fit on one row."""

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

        # ---------------------------------------------------------- the verdict
        self.banner = AreaBanner(large=True)
        lay.addWidget(self.banner)

        # ---------------------------------------------------------- last camera event (AI camera mode)
        self.group_event = QGroupBox("LAST CAMERA EVENT")
        le = QVBoxLayout(self.group_event)
        le.setSpacing(2)
        self.last_event_label = QLabel("-")
        self.last_event_label.setWordWrap(True)
        self.last_event_label.setStyleSheet(f"color: {COLOR_TEXT}; font-weight: 600;")
        self.last_event_time = QLabel("")
        self.last_event_time.setProperty("class", "muted")
        le.addWidget(self.last_event_label)
        le.addWidget(self.last_event_time)
        lay.addWidget(self.group_event)

        # ---------------------------------------------------------- performance
        g3 = QGroupBox("PERFORMANCE")
        l3 = QGridLayout(g3)
        l3.setSpacing(6)
        self.stat_fps_cam = MiniStat("FPS camera", "0")
        self.stat_fps_ai = MiniStat("FPS AI", "0")
        self.stat_infer = MiniStat("Inference", "0 ms")
        self.stat_plc = MiniStat("PLC latency", "0 ms")
        self.stat_dropped = MiniStat("Frames dropped", "0")
        self.stat_heartbeat = MiniStat("Heartbeat", "-")
        l3.addWidget(self.stat_fps_cam, 0, 0)
        l3.addWidget(self.stat_fps_ai, 0, 1)
        l3.addWidget(self.stat_infer, 0, 2)
        l3.addWidget(self.stat_plc, 1, 0)
        l3.addWidget(self.stat_dropped, 1, 1)
        l3.addWidget(self.stat_heartbeat, 1, 2)
        lay.addWidget(g3)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
        lay.addWidget(self.info_label)
        lay.addStretch(1)

    # ------------------------------------------------------------------ setters
    def set_area_status(self, status: str, text: str | None = None) -> None:
        self.banner.set_status(status, text)

    def set_ai_camera_mode(self, ai_camera: bool) -> None:
        """Show only what means something in the active mode."""
        self.group_event.setVisible(ai_camera)
        self.stat_fps_ai.setVisible(not ai_camera)
        self.stat_infer.setVisible(not ai_camera)

    def set_last_event(self, text: str, when: str = "") -> None:
        self.last_event_label.setText(text or "-")
        self.last_event_time.setText(when)

    def set_heartbeat(self, value: bool | None, enabled: bool = True) -> None:
        if not enabled:
            self.stat_heartbeat.set_value("off")
        elif value is None:
            self.stat_heartbeat.set_value("-")
        else:
            self.stat_heartbeat.set_value("1" if value else "0")

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
