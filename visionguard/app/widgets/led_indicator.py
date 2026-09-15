"""LED-style status indicator widgets (light theme)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ..theme import COLOR_TEXT, COLOR_TEXT_DIM, LED_COLORS


class LedIndicator(QWidget):
    """A round LED. States: off | ok | warn | error | busy."""

    def __init__(self, state: str = "off", diameter: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._d = diameter
        self.setFixedSize(QSize(diameter + 6, diameter + 6))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def set_bool(self, ok: bool, bad_state: str = "off") -> None:
        self.set_state("ok" if ok else bad_state)

    @property
    def state(self) -> str:
        return self._state

    def paintEvent(self, _event) -> None:  # noqa: N802
        color = QColor(LED_COLORS.get(self._state, LED_COLORS["off"]))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._d / 2.0
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self._state != "off":  # soft halo so lit LEDs pop on a white panel
            halo = QColor(color)
            halo.setAlpha(60)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(int(cx - r - 3), int(cy - r - 3), int(2 * r + 6), int(2 * r + 6))
        grad = QRadialGradient(cx - r * 0.35, cy - r * 0.35, r * 1.4)
        grad.setColorAt(0.0, color.lighter(155))
        grad.setColorAt(0.65, color)
        grad.setColorAt(1.0, color.darker(135))
        p.setBrush(grad)
        p.setPen(QPen(color.darker(160), 1))
        p.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
        p.end()


class StatusRow(QWidget):
    """[LED]  Label .................... VALUE"""

    def __init__(self, label: str, value: str = "-", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 3, 2, 3)
        lay.setSpacing(9)
        self.led = LedIndicator()
        self.label = QLabel(label)
        self.label.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self.value = QLabel(value)
        self.value.setStyleSheet(f"color: {COLOR_TEXT}; font-weight: 700; font-size: 10.5pt;")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.led)
        lay.addWidget(self.label)
        lay.addStretch(1)
        lay.addWidget(self.value)

    def set(self, text: str, led_state: str) -> None:
        self.value.setText(text)
        self.led.set_state(led_state)
        dark = {"ok": "#0F7B36", "warn": "#A9640A", "error": "#C32B22", "busy": "#0B7FA8"}.get(led_state)
        self.value.setStyleSheet(f"color: {dark or COLOR_TEXT}; font-weight: 700; font-size: 10.5pt;")

    def set_value(self, text: str) -> None:
        self.value.setText(text)

    def set_value_color(self, color: str | None) -> None:
        self.value.setStyleSheet(f"color: {color or COLOR_TEXT}; font-weight: 700; font-size: 10.5pt;")
