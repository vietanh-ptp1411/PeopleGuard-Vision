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

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication, QMouseEvent, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

try:
    import psutil
except ImportError:                      # ships with ultralytics, but never assume it
    psutil = None

from ...utils.paths import asset_path
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
BAR_HEIGHT = 64
LOGO_HEIGHT = 28
MARGIN = 16


def _load_logo(height: int) -> QPixmap | None:
    """The company wordmark, rendered for this screen's pixel density.

    Scaling a 160px-tall source down to 28 logical pixels is what keeps the letters
    crisp; handing Qt the raw file and letting the label squash it is what makes a
    logo look cheap. Returns None if the asset is missing so the bar still builds.
    """
    path = asset_path("company_logo.png")
    if not path.exists():
        return None
    source = QPixmap(str(path))
    if source.isNull():
        return None
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    scaled = source.scaledToHeight(max(1, round(height * ratio)),
                                   Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def _divider() -> QFrame:
    """A hairline that separates one group in the app bar from the next."""
    line = QFrame()
    line.setProperty("class", "headerline")
    line.setFixedWidth(1)
    line.setFixedHeight(32)
    return line


class _Gauge(QFrame):
    """One tile: caption and percentage on a line, a slim bar filling the width below.

    A fixed width on purpose. Letting the tile size itself makes the whole right-hand
    end of the bar shuffle sideways every time the reading crosses from 9% to 10%.
    """

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "gauge")
        self.setFixedWidth(104)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 7)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        label = QLabel(caption)
        label.setProperty("class", "meterlabel")
        self.value = QLabel("--")
        self.value.setProperty("class", "metervalue")
        self.value.setProperty("tone", "ok")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(self.value)
        lay.addLayout(head)

        self.bar = QProgressBar()
        self.bar.setProperty("class", "meter")
        self.bar.setProperty("tone", "ok")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        lay.addWidget(self.bar)

    def set(self, percent: float, text: str, tone: str) -> None:
        self.bar.setValue(int(round(max(0.0, min(100.0, percent)))))
        self.value.setText(text)
        if self.value.property("tone") != tone:
            # Repolish only when the colour actually changes - doing it every tick would
            # make the style engine rebuild these widgets twice a second for nothing.
            for widget in (self.value, self.bar):
                widget.setProperty("tone", tone)
                widget.style().unpolish(widget)
                widget.style().polish(widget)


class ResourceMeter(QFrame):
    """Machine load, read the way Task Manager reads it: CPU % and memory % of the box.

    Not this process's share. On a dedicated monitoring PC the question an operator is
    actually asking is "is this machine coping", and something else eating the CPU
    starves the detector just as effectively as VisionGuard doing it to itself. What
    VisionGuard alone costs is in the tooltip.
    """

    WARN_AT = 60.0
    HIGH_AT = 85.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        self.cpu = _Gauge("CPU")
        self.ram = _Gauge("RAM")
        lay.addWidget(self.cpu)
        lay.addWidget(self.ram)

        self._process = psutil.Process() if psutil is not None else None
        if psutil is not None:
            # cpu_percent reports usage *since the previous call*, so the first one only
            # starts the clock. Without this the first reading is a meaningless 0.
            psutil.cpu_percent(None)
            self._process.cpu_percent(None)
            self._cores = float(psutil.cpu_count() or 1)
        else:
            self._cores = 1.0
            for gauge in (self.cpu, self.ram):
                gauge.value.setText("n/a")
            self.setToolTip("psutil chưa được cài - không đo được CPU/RAM")

    def _tone(self, percent: float) -> str:
        return "ok" if percent < self.WARN_AT else ("warn" if percent < self.HIGH_AT else "high")

    def refresh(self) -> None:
        if psutil is None:
            return
        try:
            cpu = psutil.cpu_percent(None)
            memory = psutil.virtual_memory()
            own_cpu = self._process.cpu_percent(None) / self._cores
            own_gb = self._process.memory_info().rss / 1e9
        except Exception:
            return                      # a sampling hiccup must never take the UI with it
        self.cpu.set(cpu, f"{cpu:.0f}%", self._tone(cpu))
        self.ram.set(memory.percent, f"{memory.percent:.0f}%", self._tone(memory.percent))
        self.setToolTip(
            f"Toàn máy: {cpu:.0f}% CPU  ·  RAM {memory.used / 1e9:.1f} / "
            f"{memory.total / 1e9:.1f} GB ({memory.percent:.0f}%)\n"
            f"Riêng VisionGuard: {own_cpu:.0f}% CPU  ·  {own_gb:.2f} GB")


class AppBar(QFrame):
    """Dark identity bar: who made it, what it is, and what it is costing the machine.

    The two badges that used to live here are gone. The mode and the PLC's simulation
    state are both already spelled out in the status chips one row below, and saying the
    same thing twice in two different vocabularies is what made the bar look busy.

    All three groups share a single grid cell with three different alignments, so the
    product name sits at the true centre of the bar rather than at the centre of
    whatever the left and right groups happen to leave over - those are nowhere near
    the same width, and a stretch-based layout would push the title noticeably left.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppBar")
        self.setFixedHeight(BAR_HEIGHT)
        grid = QGridLayout(self)
        # Equal margins, so "centred" means centred on the bar and not 2px off it.
        grid.setContentsMargins(MARGIN, 0, MARGIN, 0)
        grid.setSpacing(0)

        middle = Qt.AlignmentFlag.AlignVCenter
        self._left = self._company_mark()
        self._title = self._title_block()
        self._right = self._status_block()
        grid.addWidget(self._left, 0, 0, Qt.AlignmentFlag.AlignLeft | middle)
        grid.addWidget(self._title, 0, 0, Qt.AlignmentFlag.AlignHCenter | middle)
        grid.addWidget(self._right, 0, 0, Qt.AlignmentFlag.AlignRight | middle)

    def minimumSizeHint(self):  # noqa: N802 (Qt API)
        """Stacked in one cell, the grid would happily let the three groups overlap.

        Summing the three widths is the obvious formula and it is wrong: a centred item
        is mirrored about the middle, so it collides with whichever side reaches further
        in, and the room it needs is twice *that* side, not left plus right. The sum said
        737px while the title was still running into the CPU tile at 770.
        """
        gap = 20
        side = MARGIN + max(self._left.sizeHint().width(), self._right.sizeHint().width())
        return QSize(2 * side + self._title.sizeHint().width() + 2 * gap, BAR_HEIGHT)

    def _title_block(self) -> QWidget:
        block = QWidget()
        block.setProperty("class", "transparent")
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        name = QLabel("VisionGuard")
        name.setProperty("class", "brand")
        sub = QLabel("Person-in-Area Monitoring")
        sub.setProperty("class", "brandsub")
        for label in (name, sub):
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(label)
        return block

    def _status_block(self) -> QWidget:
        block = QWidget()
        block.setProperty("class", "transparent")
        lay = QHBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(13)
        middle = Qt.AlignmentFlag.AlignVCenter

        self.meter = ResourceMeter()
        lay.addWidget(self.meter, 0, middle)
        lay.addWidget(_divider(), 0, middle)

        clock = QVBoxLayout()
        clock.setContentsMargins(0, 0, 0, 0)
        clock.setSpacing(0)
        self.lbl_time = QLabel("")
        self.lbl_time.setProperty("class", "clocktime")
        self.lbl_date = QLabel("")
        self.lbl_date.setProperty("class", "clockdate")
        for label in (self.lbl_time, self.lbl_date):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            clock.addWidget(label)
        lay.addLayout(clock)
        return block

    def refresh_resources(self) -> None:
        self.meter.refresh()

    def _company_mark(self) -> QFrame:
        """The company wordmark on a white plate, or its name if the file is missing."""
        plate = QFrame()
        plate.setProperty("class", "logoplate")
        plate.setToolTip("MVA LAB - Tech for Solution")
        inner = QHBoxLayout(plate)
        inner.setContentsMargins(11, 5, 11, 5)
        inner.setSpacing(0)

        label = QLabel()
        pixmap = _load_logo(LOGO_HEIGHT)
        if pixmap is not None:
            label.setPixmap(pixmap)
        else:
            label.setText("MVA LAB")
            label.setStyleSheet("color: #1436C8; font-size: 13pt; font-weight: 800;"
                                " letter-spacing: 1px; background: transparent;")
        inner.addWidget(label)
        return plate

    def set_clock(self, time_text: str, date_text: str = "") -> None:
        self.lbl_time.setText(time_text)
        self.lbl_date.setText(date_text)


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
