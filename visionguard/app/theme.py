"""Light industrial theme: semantic color tokens + application-wide QSS.

Every widget reads its colors from here (never hard-coded), so the whole look can be
retuned in one place. Colors used to draw on the video frame live in
`config.schemas.VisualizationConfig` instead - they belong to the image, not the chrome.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# --------------------------------------------------------------------------- surfaces
COLOR_BG = "#EDF1F7"          # window / tab page background
COLOR_PANEL = "#FFFFFF"       # cards (QGroupBox), toolbar, status bar
COLOR_PANEL_ALT = "#F4F7FB"   # table headers, inactive tabs, subtle fills
COLOR_BORDER = "#D5DDE8"
COLOR_BORDER_STRONG = "#BAC5D4"
COLOR_VIDEO_BG = "#DDE4EE"    # letterbox area around the camera image

# --------------------------------------------------------------------------- text
COLOR_TEXT = "#16202C"
COLOR_TEXT_DIM = "#5C6A7A"
COLOR_TEXT_MUTED = "#8A96A5"

# --------------------------------------------------------------------------- semantic
COLOR_ACCENT = "#0B63CE"
COLOR_ACCENT_HOVER = "#1A74E0"
COLOR_ACCENT_SOFT = "#E8F1FD"
COLOR_OK = "#0F7B36"
COLOR_OK_SOFT = "#E6F5EC"
COLOR_WARN = "#A9640A"
COLOR_WARN_SOFT = "#FDF3E2"
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

#: Big area-status banner fills (toolbar, status panel, video overlay).
STATUS_COLORS = {
    "CLEAR": "#16A34A",
    "OCCUPIED": "#DC2626",
    "FAULT": "#F2A200",
    "STOPPED": "#7C8899",
    "STARTING": "#0EA5E9",
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
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    background-color: {COLOR_PANEL};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {COLOR_TEXT_DIM};
    font-size: 8.5pt;
    font-weight: 700;
    letter-spacing: 1px;
}}
QFrame[class="card"] {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
}}
QFrame[class="toolbar"] {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
}}
QFrame[class="vline"] {{ background: {COLOR_BORDER}; max-width: 1px; }}

/* ---------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    background: {COLOR_BG};
    top: -1px;
}}
QTabBar::tab {{
    background: {COLOR_PANEL_ALT};
    color: {COLOR_TEXT_DIM};
    padding: 8px 9px;
    border: 1px solid {COLOR_BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-size: 9pt;
    font-weight: 600;
}}
QTabBar::tab:hover {{ background: {COLOR_ACCENT_SOFT}; color: {COLOR_ACCENT}; }}
QTabBar::tab:selected {{
    background: {COLOR_PANEL};
    color: {COLOR_ACCENT};
    border-top: 3px solid {COLOR_ACCENT};
    padding-top: 6px;
}}

/* ---------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 6px;
    padding: 7px 14px;
    min-height: 24px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {COLOR_ACCENT_SOFT}; border-color: {COLOR_ACCENT}; color: {COLOR_ACCENT}; }}
QPushButton:pressed {{ background-color: #DCE9FB; }}
QPushButton:disabled {{ color: #A7B2BF; background-color: #F3F5F9; border-color: #E2E7EE; }}
QPushButton:checked {{ background-color: {COLOR_ACCENT}; border-color: {COLOR_ACCENT}; color: #FFFFFF; }}

QPushButton[class="primary"] {{ background-color: {COLOR_ACCENT}; border-color: {COLOR_ACCENT}; color: #FFFFFF; }}
QPushButton[class="primary"]:hover {{ background-color: {COLOR_ACCENT_HOVER}; color: #FFFFFF; }}
QPushButton[class="primary"]:disabled {{ background-color: #B9CEE9; border-color: #B9CEE9; color: #EDF3FB; }}

QPushButton[class="success"] {{ background-color: #17A44C; border-color: #17A44C; color: #FFFFFF; }}
QPushButton[class="success"]:hover {{ background-color: #1BB856; color: #FFFFFF; }}
QPushButton[class="success"]:disabled {{ background-color: #BEE3CB; border-color: #BEE3CB; color: #F0F9F3; }}

QPushButton[class="danger"] {{ background-color: #D6352B; border-color: #D6352B; color: #FFFFFF; }}
QPushButton[class="danger"]:hover {{ background-color: #E6453B; color: #FFFFFF; }}
QPushButton[class="danger"]:disabled {{ background-color: #EFC4C1; border-color: #EFC4C1; color: #FCF1F0; }}

QPushButton[class="warn"] {{ background-color: #E08A00; border-color: #E08A00; color: #FFFFFF; }}
QPushButton[class="warn"]:hover {{ background-color: #F09A10; color: #FFFFFF; }}

QPushButton[size="xl"] {{ font-size: 11.5pt; font-weight: 800; letter-spacing: 1px; padding: 9px 22px; min-height: 34px; }}
QPushButton[size="sm"] {{ padding: 4px 10px; min-height: 20px; font-weight: 600; }}

/* ---------------------------------------------------------------- inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QListWidget, QTableWidget, QTableView {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {COLOR_ACCENT};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 2px solid {COLOR_ACCENT};
    padding: 4px 7px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: #A7B2BF; background-color: #F3F5F9; border-color: #E2E7EE;
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

/* ---------------------------------------------------------------- tables */
QHeaderView::section {{
    background-color: {COLOR_PANEL_ALT};
    color: {COLOR_TEXT_DIM};
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
    font-weight: 700;
    font-size: 8.5pt;
    letter-spacing: 0.5px;
}}
QTableWidget, QTableView {{
    gridline-color: #E7ECF3;
    alternate-background-color: #F8FAFD;
    padding: 0;
}}
QTableWidget::item, QTableView::item {{ padding: 4px 6px; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {COLOR_ACCENT_SOFT}; color: {COLOR_TEXT}; }}
QTableCornerButton::section {{ background: {COLOR_PANEL_ALT}; border: none; }}

/* ---------------------------------------------------------------- checks & sliders */
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator {{ border: 1px solid {COLOR_BORDER_STRONG}; border-radius: 4px; background: {COLOR_PANEL}; }}
QCheckBox::indicator:hover {{ border-color: {COLOR_ACCENT}; }}
QCheckBox::indicator:checked {{ background: {COLOR_ACCENT}; border-color: {COLOR_ACCENT}; }}
QCheckBox::indicator:disabled {{ background: #F0F2F6; border-color: #DDE3EB; }}
QSlider::groove:horizontal {{ height: 5px; background: {COLOR_BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {COLOR_ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 15px; margin: -6px 0; background: {COLOR_PANEL};
                              border: 2px solid {COLOR_ACCENT}; border-radius: 8px; }}

/* ---------------------------------------------------------------- scrollbars */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C3CDDA; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #A9B6C6; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C3CDDA; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: #A9B6C6; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------------------------------------------------------------- chrome */
QToolBar {{ background: {COLOR_PANEL}; border: none; border-bottom: 1px solid {COLOR_BORDER}; spacing: 10px; padding: 7px 10px; }}
QToolBar::separator {{ background: {COLOR_BORDER}; width: 1px; margin: 4px 6px; }}
QStatusBar {{ background: {COLOR_PANEL}; border-top: 1px solid {COLOR_BORDER}; color: {COLOR_TEXT_DIM}; padding: 2px 8px; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: {COLOR_BG}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {COLOR_ACCENT_SOFT}; }}
QScrollArea {{ border: none; background: {COLOR_BG}; }}
QToolTip {{ background: #202B38; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 4px; font-size: 9.5pt; }}
QMenu {{ background: {COLOR_PANEL}; border: 1px solid {COLOR_BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {COLOR_ACCENT_SOFT}; color: {COLOR_ACCENT}; }}
QMessageBox {{ background: {COLOR_PANEL}; }}

/* ---------------------------------------------------------------- label roles */
QLabel[class="dim"] {{ color: {COLOR_TEXT_DIM}; }}
QLabel[class="muted"] {{ color: {COLOR_TEXT_MUTED}; font-size: 9pt; }}
QLabel[class="title"] {{ color: {COLOR_TEXT_DIM}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}
QLabel[class="value"] {{ font-weight: 700; font-size: 10.5pt; }}
QLabel[class="hint"] {{ color: {COLOR_TEXT_DIM}; font-size: 9pt; }}
QLabel[class="section"] {{ color: {COLOR_TEXT_MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1.2px; }}
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
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#202B38"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLOR_TEXT_MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#A7B2BF"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#A7B2BF"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#A7B2BF"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
