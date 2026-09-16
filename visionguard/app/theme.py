"""Design system: semantic color tokens, type scale and the application-wide QSS.

Every widget reads its colors from here (never hard-coded), so the whole look can be
retuned in one place. Colors used to draw on the video frame live in
`config.schemas.VisualizationConfig` instead - they belong to the image, not the chrome.

The look is a light industrial console: a dark header bar for identity, a white command
bar for the two actions that matter, and white cards on a cool grey page for everything
else. Three type sizes, two weights, one accent - anything more starts to look noisy.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# --------------------------------------------------------------------------- surfaces
COLOR_BG = "#EEF2F7"          # page background behind the cards
COLOR_PANEL = "#FFFFFF"       # cards, command bar, inputs
COLOR_PANEL_ALT = "#F5F8FC"   # table headers, subtle fills, hover rows
COLOR_BORDER = "#DCE3EC"      # hairline between surfaces
COLOR_BORDER_STRONG = "#C3CDDB"  # input outlines
COLOR_VIDEO_BG = "#D9E0EA"    # letterbox area around the camera image

# --------------------------------------------------------------------------- header
COLOR_HEADER = "#122234"          # app bar
COLOR_HEADER_ALT = "#1B3149"      # badges inside the app bar
COLOR_HEADER_TEXT = "#F2F6FB"
COLOR_HEADER_DIM = "#93A8C0"

# --------------------------------------------------------------------------- text
COLOR_TEXT = "#16202C"
COLOR_TEXT_DIM = "#59687A"
COLOR_TEXT_MUTED = "#8996A6"

# --------------------------------------------------------------------------- semantic
COLOR_ACCENT = "#0B63CE"
COLOR_ACCENT_HOVER = "#1A74E0"
COLOR_ACCENT_SOFT = "#E7F0FD"
COLOR_OK = "#0F7B36"
COLOR_OK_SOFT = "#E6F5EC"
COLOR_WARN = "#9A5B06"
COLOR_WARN_SOFT = "#FDF2E0"
COLOR_ERROR = "#C32B22"
COLOR_ERROR_SOFT = "#FCEBEA"
COLOR_OFF = "#94A2B2"
COLOR_BUSY = "#0B7FA8"

# --------------------------------------------------------------------------- indicators
#: LED fills are deliberately brighter than the text colors above.
LED_COLORS = {
    "off": "#B6C1CE",
    "ok": "#1BA94C",
    "warn": "#F2A200",
    "error": "#E0342A",
    "busy": "#00A3D9",
}

#: Text / soft-fill pairs for the state chips and inline badges.
STATE_COLORS = {
    "off": (COLOR_TEXT_DIM, "#EDF1F6"),
    "ok": (COLOR_OK, COLOR_OK_SOFT),
    "warn": (COLOR_WARN, COLOR_WARN_SOFT),
    "error": (COLOR_ERROR, COLOR_ERROR_SOFT),
    "busy": (COLOR_BUSY, "#E3F4FA"),
}

#: Big area-status banner fills (command bar, status panel, video overlay).
STATUS_COLORS = {
    "CLEAR": "#0F8C3E",
    "OCCUPIED": "#CE2A20",
    "FAULT": "#E08A00",
    "STOPPED": "#6C7B8D",
    "STARTING": "#0B7FA8",
}

#: Human readable banner captions.
STATUS_CAPTIONS = {
    "CLEAR": "AREA CLEAR",
    "OCCUPIED": "PERSON DETECTED",
    "FAULT": "FAULT",
    "STOPPED": "STOPPED",
    "STARTING": "STARTING...",
}


def contrast_text(background: str) -> str:
    """Black or white text, whichever stays readable on `background`."""
    c = QColor(background)
    luminance = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#1A1400" if luminance > 165 else "#FFFFFF"


def status_caption(status: str) -> str:
    return STATUS_CAPTIONS.get(status, status)


def status_color(status: str) -> str:
    return STATUS_COLORS.get(status, COLOR_OFF)


def state_colors(state: str) -> tuple[str, str]:
    """(text color, soft fill) for a led-style state name."""
    return STATE_COLORS.get(state, STATE_COLORS["off"])


# --------------------------------------------------------------------------- stylesheet
QSS = f"""
QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    font-size: 10pt;
}}
/* Labels and check boxes must not paint the window color over a white card. */
QLabel, QCheckBox, QRadioButton, QSplitter, QTabWidget, QStackedWidget {{ background: transparent; }}
QWidget[class="transparent"] {{ background: transparent; }}

/* ---------------------------------------------------------------- cards */
QGroupBox {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    margin-top: 15px;
    padding: 14px 12px 12px 12px;
    background-color: {COLOR_PANEL};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: {COLOR_TEXT_MUTED};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1.1px;
}}
QFrame[class="card"] {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
}}
QFrame[class="cardflat"] {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
}}
QFrame[class="toolbar"] {{
    background-color: {COLOR_PANEL_ALT};
    border: none;
    border-top: 1px solid {COLOR_BORDER};
}}
QFrame[class="cardhead"] {{
    background-color: {COLOR_PANEL_ALT};
    border: none;
    border-bottom: 1px solid {COLOR_BORDER};
}}
QFrame[class="hline"] {{ background: {COLOR_BORDER}; max-height: 1px; border: none; }}
QFrame[class="vline"] {{ background: {COLOR_BORDER}; max-width: 1px; border: none; }}

/* ---------------------------------------------------------------- app bar */
QFrame#AppBar {{ background-color: {COLOR_HEADER}; border: none; }}
QFrame#AppBar QLabel {{ background: transparent; color: {COLOR_HEADER_TEXT}; }}
QLabel[class="brand"] {{ font-size: 14pt; font-weight: 800; letter-spacing: 0.4px; }}
QLabel[class="brandsub"] {{ color: {COLOR_HEADER_DIM}; font-size: 9pt; }}
QLabel[class="headerbadge"] {{
    background-color: {COLOR_HEADER_ALT};
    color: {COLOR_HEADER_TEXT};
    border-radius: 11px;
    padding: 4px 12px;
    font-size: 8.5pt;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel[class="clock"] {{ color: {COLOR_HEADER_DIM}; font-size: 10pt; }}

/* ---------------------------------------------------------------- state chips */
QFrame[class="chip"] {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
}}
QFrame[class="chip"]:hover {{ border-color: {COLOR_ACCENT}; background-color: {COLOR_ACCENT_SOFT}; }}

/* ---------------------------------------------------------------- alert strip */
QFrame[class="alert"] {{ border-radius: 8px; border: 1px solid {COLOR_BORDER}; }}

/* ---------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {COLOR_BORDER};
    background: transparent;
    top: -1px;
}}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {COLOR_TEXT_DIM};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 12px 8px 12px;
    margin-right: 2px;
    font-size: 9.5pt;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {COLOR_ACCENT}; }}
QTabBar::tab:selected {{ color: {COLOR_ACCENT}; border-bottom: 2px solid {COLOR_ACCENT}; }}
QTabBar::tab:disabled {{ color: {COLOR_TEXT_MUTED}; }}

/* ---------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 6px;
    padding: 7px 14px;
    min-height: 22px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {COLOR_ACCENT_SOFT}; border-color: {COLOR_ACCENT}; color: {COLOR_ACCENT}; }}
QPushButton:pressed {{ background-color: #D9E7FA; }}
QPushButton:disabled {{ color: #A7B2BF; background-color: #F3F5F9; border-color: #E4E9F0; }}
QPushButton:checked {{ background-color: {COLOR_ACCENT}; border-color: {COLOR_ACCENT}; color: #FFFFFF; }}

QPushButton[class="primary"] {{ background-color: {COLOR_ACCENT}; border-color: {COLOR_ACCENT}; color: #FFFFFF; }}
QPushButton[class="primary"]:hover {{ background-color: {COLOR_ACCENT_HOVER}; color: #FFFFFF; }}
QPushButton[class="primary"]:disabled {{ background-color: #BACFEA; border-color: #BACFEA; color: #EEF4FC; }}

QPushButton[class="success"] {{ background-color: #12924A; border-color: #12924A; color: #FFFFFF; }}
QPushButton[class="success"]:hover {{ background-color: #17A855; color: #FFFFFF; }}
QPushButton[class="success"]:disabled {{ background-color: #C0DFCC; border-color: #C0DFCC; color: #F1F9F4; }}

QPushButton[class="danger"] {{ background-color: #C9302A; border-color: #C9302A; color: #FFFFFF; }}
QPushButton[class="danger"]:hover {{ background-color: #DB3F38; color: #FFFFFF; }}
QPushButton[class="danger"]:disabled {{ background-color: #EFC6C3; border-color: #EFC6C3; color: #FCF2F1; }}

QPushButton[class="warn"] {{ background-color: #D07E00; border-color: #D07E00; color: #FFFFFF; }}
QPushButton[class="warn"]:hover {{ background-color: #E28D08; color: #FFFFFF; }}

QPushButton[class="dangerline"] {{ background: {COLOR_PANEL}; border: 1px solid #E0A9A5; color: #C9302A; }}
QPushButton[class="dangerline"]:hover {{ background: #C9302A; border-color: #C9302A; color: #FFFFFF; }}
QPushButton[class="ghost"] {{ background: transparent; border: 1px solid transparent; color: {COLOR_TEXT_DIM}; }}
QPushButton[class="ghost"]:hover {{ background: {COLOR_ACCENT_SOFT}; color: {COLOR_ACCENT}; }}

QPushButton[size="xl"] {{ font-size: 10.5pt; font-weight: 700; letter-spacing: 0.8px; padding: 9px 20px; min-height: 30px; }}
QPushButton[size="sm"] {{ padding: 4px 10px; min-height: 19px; font-size: 9.5pt; font-weight: 600; }}

/* ---------------------------------------------------------------- inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QListWidget, QTableWidget, QTableView {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {COLOR_ACCENT};
    selection-color: #FFFFFF;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {COLOR_TEXT_MUTED}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 2px solid {COLOR_ACCENT};
    padding: 4px 7px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: #A7B2BF; background-color: #F3F5F9; border-color: #E4E9F0;
}}
QLineEdit::placeholder {{ color: {COLOR_TEXT_MUTED}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 17px; border: none; background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {COLOR_ACCENT_SOFT}; }}
QComboBox QAbstractItemView {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER_STRONG};
    selection-background-color: {COLOR_ACCENT_SOFT};
    selection-color: {COLOR_TEXT};
    outline: none;
}}
QFormLayout {{ }}

/* ---------------------------------------------------------------- tables */
QHeaderView::section {{
    background-color: {COLOR_PANEL_ALT};
    color: {COLOR_TEXT_MUTED};
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {COLOR_BORDER};
    font-weight: 700;
    font-size: 8pt;
    letter-spacing: 0.6px;
}}
QTableWidget, QTableView {{
    gridline-color: #EDF1F6;
    alternate-background-color: #F9FBFD;
    padding: 0;
}}
QTableWidget::item, QTableView::item {{ padding: 4px 6px; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {COLOR_ACCENT_SOFT}; color: {COLOR_TEXT}; }}
QTableCornerButton::section {{ background: {COLOR_PANEL_ALT}; border: none; }}

/* ---------------------------------------------------------------- checks & sliders */
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {COLOR_BORDER_STRONG}; border-radius: 4px; background: {COLOR_PANEL}; }}
QCheckBox::indicator:hover {{ border-color: {COLOR_ACCENT}; }}
QCheckBox::indicator:checked {{ background: {COLOR_ACCENT}; border-color: {COLOR_ACCENT}; }}
QCheckBox::indicator:disabled {{ background: #F0F2F6; border-color: #DEE4EB; }}
QSlider::groove:horizontal {{ height: 5px; background: {COLOR_BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {COLOR_ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 15px; margin: -6px 0; background: {COLOR_PANEL};
                              border: 2px solid {COLOR_ACCENT}; border-radius: 8px; }}

/* ---------------------------------------------------------------- scrollbars */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C7D1DE; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #AAB8C8; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C7D1DE; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: #AAB8C8; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------------------------------------------------------------- chrome */
QStatusBar {{ background: {COLOR_PANEL}; border-top: 1px solid {COLOR_BORDER}; color: {COLOR_TEXT_DIM}; padding: 2px 10px; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QScrollArea {{ border: none; background: transparent; }}
QToolTip {{ background: #1C2733; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 4px; font-size: 9.5pt; }}
QMenu {{ background: {COLOR_PANEL}; border: 1px solid {COLOR_BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {COLOR_ACCENT_SOFT}; color: {COLOR_ACCENT}; }}
QMessageBox {{ background: {COLOR_PANEL}; }}

/* ---------------------------------------------------------------- label roles */
QLabel[class="dim"] {{ color: {COLOR_TEXT_DIM}; }}
QLabel[class="muted"] {{ color: {COLOR_TEXT_MUTED}; font-size: 9pt; }}
QLabel[class="title"] {{ color: {COLOR_TEXT_MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1.1px; }}
QLabel[class="value"] {{ font-weight: 700; font-size: 10.5pt; }}
QLabel[class="hint"] {{ color: {COLOR_TEXT_DIM}; font-size: 9pt; }}
QLabel[class="section"] {{ color: {COLOR_TEXT_MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1.1px; }}
QLabel[class="pagetitle"] {{ color: {COLOR_TEXT}; font-size: 12pt; font-weight: 700; }}
QLabel[class="pagesub"] {{ color: {COLOR_TEXT_DIM}; font-size: 9pt; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(COLOR_PANEL))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_PANEL_ALT))
    pal.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(COLOR_PANEL))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1C2733"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLOR_TEXT_MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#A7B2BF"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#A7B2BF"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#A7B2BF"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
