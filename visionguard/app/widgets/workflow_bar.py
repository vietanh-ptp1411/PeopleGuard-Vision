"""WorkflowBar: the 5 setup steps shown as clickable chips.

    (1) Camera  >  (2) AI Model  >  (3) ROI  >  (4) PLC  >  (5) Run

Each chip shows whether the step is done, in progress, still to do or in error, so an
operator always sees what is missing before the system can run. Clicking a chip opens
the matching tab.
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..theme import (COLOR_ACCENT, COLOR_ACCENT_SOFT, COLOR_BORDER, COLOR_ERROR, COLOR_ERROR_SOFT, COLOR_OK,
                     COLOR_OK_SOFT, COLOR_PANEL, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, COLOR_WARN, COLOR_WARN_SOFT)

# state -> (border, background, number badge, title color)
_STATE_STYLE = {
    "todo": (COLOR_BORDER, COLOR_PANEL, "#AEB9C7", COLOR_TEXT_DIM),
    "active": (COLOR_ACCENT, COLOR_ACCENT_SOFT, COLOR_ACCENT, COLOR_ACCENT),
    "done": (COLOR_OK, COLOR_OK_SOFT, COLOR_OK, COLOR_OK),
    "warn": (COLOR_WARN, COLOR_WARN_SOFT, COLOR_WARN, COLOR_WARN),
    "error": (COLOR_ERROR, COLOR_ERROR_SOFT, COLOR_ERROR, COLOR_ERROR),
}


class StepChip(QFrame):
    clicked = Signal(int)

    def __init__(self, index: int, number: str, title: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self._number = number
        self._state = "todo"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(48)
        self.setMinimumWidth(92)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 12, 5)
        lay.setSpacing(9)
        self.badge = QLabel(number)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFixedSize(24, 24)
        lay.addWidget(self.badge)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        self.title = QLabel(title)
        self.detail = QLabel("-")
        for lab in (self.title, self.detail):
            # the chip must be free to shrink with the window; text simply clips
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 8.5pt;")
        texts.addWidget(self.title)
        texts.addWidget(self.detail)
        lay.addLayout(texts, 1)
        self.set_state("todo", "-")

    def set_state(self, state: str, detail: str = "") -> None:
        self._state = state
        border, bg, badge, title = _STATE_STYLE.get(state, _STATE_STYLE["todo"])
        self.setStyleSheet(f"StepChip {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}")
        self.badge.setText({"done": "✓", "error": "!", "warn": "!"}.get(state, self._number))
        self.badge.setStyleSheet(
            f"background: {badge}; color: #FFFFFF; border-radius: 12px; font-weight: 800; font-size: 8.5pt;")
        self.title.setStyleSheet(f"color: {title}; font-weight: 700; font-size: 9.5pt;")
        if detail:
            self.detail.setText(detail)
            # the chip is narrow, so the full reason lives in the tooltip
            self.detail.setToolTip(detail)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class WorkflowBar(QWidget):
    """Emits the index of the step the user clicked (0..4)."""

    step_clicked = Signal(int)

    STEPS = (
        ("1", "Camera", "Chon nguon hinh (USB / Video / RTSP), Connect roi Start"),
        ("2", "AI Model", "Nap model YOLO pretrained de phat hien person"),
        ("3", "ROI Zones", "Ve vung giam sat (polygon) va vung loai tru tren hinh"),
        ("4", "PLC", "Ket noi PLC that qua MC Protocol hoac bat Simulation"),
        ("5", "Run", "Nhan START SYSTEM (F5) de bat dau giam sat"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.chips: List[StepChip] = []
        for i, (num, title, tip) in enumerate(self.STEPS):
            chip = StepChip(i, num, title, tip)
            chip.clicked.connect(self.step_clicked)
            self.chips.append(chip)
            lay.addWidget(chip, 1)
            if i < len(self.STEPS) - 1:
                arrow = QLabel("›")
                arrow.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 15pt; font-weight: 700;")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lay.addWidget(arrow)

    def set_step(self, index: int, state: str, detail: str = "") -> None:
        if 0 <= index < len(self.chips):
            self.chips[index].set_state(state, detail)

    def set_step_title(self, index: int, title: str, tooltip: str = "") -> None:
        """The meaning of a step changes with the detection mode."""
        if 0 <= index < len(self.chips):
            chip = self.chips[index]
            chip.title.setText(title)
            if tooltip:
                chip.setToolTip(tooltip)
