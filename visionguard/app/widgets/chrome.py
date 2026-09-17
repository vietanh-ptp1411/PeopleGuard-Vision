"""Window chrome: the app bar, the state chips, the area banner and the alert strip.

These four pieces carry the whole "what is the system doing right now" story:

    AppBar       identity + detection mode + clock          (always the same height)
    AreaBanner   the one readout an operator looks at       (AREA CLEAR / PERSON DETECTED)
    StateChip    camera / events / PLC, one line each       (click to open its tab)
    AlertStrip   only visible when something is wrong       (with a button that fixes it)

The chips replace the old step-by-step workflow strip: the same information, one row
instead of five boxes, and silent while everything is healthy.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..theme import (COLOR_BORDER, COLOR_ERROR, COLOR_ERROR_SOFT, COLOR_HEADER_DIM, COLOR_TEXT_DIM,
                     COLOR_TEXT_MUTED, COLOR_WARN, COLOR_WARN_SOFT, contrast_text, state_colors, status_caption,
                     status_color)
from .led_indicator import LedIndicator


class ElidedLabel(QLabel):
    """A label that shortens its text with an ellipsis instead of cutting a word in half.

    The full text always stays in the tooltip, so nothing is ever lost - the old status
    strip silently truncated messages like "cannot reach 192.168.1.64 - no answer (check I".
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(30, self.width() - 2)
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, width))


# --------------------------------------------------------------------------- app bar
class AppBar(QFrame):
    """Dark identity bar: product name, what it is monitoring, detection mode, clock."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppBar")
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        mark = QLabel("◉")
        mark.setStyleSheet("color: #3D8BFD; font-size: 19pt;")
        lay.addWidget(mark)

        block = QVBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(0)
        name = QLabel("VisionGuard")
        name.setProperty("class", "brand")
        sub = QLabel("Person-in-Area Monitoring")
        sub.setProperty("class", "brandsub")
        block.addWidget(name)
        block.addWidget(sub)
        lay.addLayout(block)

        lay.addSpacing(8)
        self.badge_mode = QLabel("AI CAMERA")
        self.badge_mode.setProperty("class", "headerbadge")
        self.badge_mode.setToolTip("Nguồn phát hiện người đang dùng")
        lay.addWidget(self.badge_mode)

        self.badge_plc = QLabel("PLC SIMULATION")
        self.badge_plc.setProperty("class", "headerbadge")
        self.badge_plc.setToolTip("PLC đang chạy ở chế độ mô phỏng, không ghi ra thiết bị thật")
        lay.addWidget(self.badge_plc)
        self.badge_plc.hide()

        lay.addStretch(1)

        self.lbl_clock = QLabel("")
        self.lbl_clock.setProperty("class", "clock")
        self.lbl_clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.lbl_clock)

    def set_mode(self, text: str) -> None:
        self.badge_mode.setText(text)

    def set_simulation(self, on: bool) -> None:
        self.badge_plc.setVisible(on)

    def set_clock(self, text: str) -> None:
        self.lbl_clock.setText(text)


# --------------------------------------------------------------------------- area banner
class AreaBanner(QFrame):
    """The single most important readout, sized to be legible across the room."""

    def __init__(self, large: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.large = bool(large)
        self.setMinimumWidth(228)
        self.setFixedHeight(104 if large else 48)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10 if large else 4, 18, 10 if large else 4)
        lay.setSpacing(2 if large else 0)
        self.caption = QLabel("AREA STATUS")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value = QLabel(status_caption("STOPPED"))
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.caption)
        lay.addWidget(self.value)
        self.set_status("STOPPED")

    def set_status(self, status: str, text: str | None = None) -> None:
        color = status_color(status)
        fg = contrast_text(color)
        self.value.setText(text or status_caption(status))
        self.setStyleSheet(f"QFrame {{ background: {color}; border-radius: 8px; border: none; }}")
        self.caption.setStyleSheet(f"color: {fg}; font-size: 7.5pt; font-weight: 700;"
                                   f" letter-spacing: 1.4px; background: transparent;")
        self.value.setStyleSheet(f"color: {fg}; font-size: {24 if self.large else 15}pt;"
                                 f" font-weight: 800; letter-spacing: 1.4px; background: transparent;")


# --------------------------------------------------------------------------- state chip
class StateChip(QFrame):
    """[LED] CAPTION / value - a compact health readout for one subsystem.

    Clicking it opens the tab that can do something about it, so a red chip is always
    one click away from its own settings page.
    """

    clicked = Signal()

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(118)
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 5, 11, 5)
        lay.setSpacing(9)
        self.led = LedIndicator(diameter=11)
        lay.addWidget(self.led, 0, Qt.AlignmentFlag.AlignVCenter)

        block = QVBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(1)
        self.lbl_caption = QLabel(caption)
        self.lbl_caption.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 7.5pt; font-weight: 700;"
                                       f" letter-spacing: 1px; background: transparent;")
        self.lbl_value = ElidedLabel("-")
        self._style_value("off")
        block.addWidget(self.lbl_caption)
        block.addWidget(self.lbl_value)
        lay.addLayout(block, 1)

    def _style_value(self, state: str) -> None:
        color, _fill = state_colors(state)
        self.lbl_value.setStyleSheet(f"color: {color}; font-size: 10pt; font-weight: 700; background: transparent;")

    def set_caption(self, text: str) -> None:
        self.lbl_caption.setText(text)

    def set(self, state: str, value: str, detail: str = "") -> None:
        self.led.set_state(state)
        self._style_value(state)
        self.lbl_value.setText(value)
        self.setToolTip(detail or value)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- alert strip
class AlertStrip(QFrame):
    """One line that appears only when something needs the operator's attention."""

    action_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "alert")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 8, 5)
        lay.setSpacing(9)
        self.icon = QLabel("!")
        self.icon.setFixedWidth(16)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = ElidedLabel("")
        self.btn = QPushButton("Mở")
        self.btn.setProperty("size", "sm")
        self.btn.clicked.connect(self.action_clicked)
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.btn)
        self.hide()

    def show_alert(self, level: str, text: str, action: str = "") -> None:
        color = COLOR_ERROR if level == "error" else COLOR_WARN
        fill = COLOR_ERROR_SOFT if level == "error" else COLOR_WARN_SOFT
        self.setStyleSheet(f"QFrame[class=\"alert\"] {{ background: {fill}; border: 1px solid {color}; }}")
        self.icon.setStyleSheet(f"color: {color}; font-weight: 800; font-size: 12pt; background: transparent;")
        self.text.setStyleSheet(f"color: {color}; font-weight: 600; background: transparent;")
        self.text.setText(text)
        self.btn.setText(action or "Mở")
        self.btn.setVisible(bool(action))
        self.show()

    def clear(self) -> None:
        self.hide()


# --------------------------------------------------------------------------- small bits
def card_header(title: str, subtitle: str = "") -> QFrame:
    """The grey strip at the top of a card: bold title, optional muted subtitle."""
    frame = QFrame()
    frame.setProperty("class", "cardhead")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(12, 7, 12, 7)
    lay.setSpacing(10)
    lab = QLabel(title)
    lab.setProperty("class", "title")
    lay.addWidget(lab)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setProperty("class", "muted")
        lay.addWidget(sub)
    lay.addStretch(1)
    return frame


def separator(vertical: bool = True, length: int = 26) -> QFrame:
    f = QFrame()
    if vertical:
        f.setFixedWidth(1)
        f.setMinimumHeight(length)
    else:
        f.setFixedHeight(1)
    f.setStyleSheet(f"background: {COLOR_BORDER}; border: none;")
    return f


def caption(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 8pt; font-weight: 700;"
                      f" letter-spacing: 1px; background: transparent;")
    return lab


def header_hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {COLOR_HEADER_DIM}; font-size: 9pt; background: transparent;")
    return lab


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9pt; background: transparent;")
    lab.setWordWrap(True)
    return lab
