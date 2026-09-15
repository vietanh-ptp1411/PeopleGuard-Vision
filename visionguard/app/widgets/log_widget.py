"""Log panel fed by QtLogHandler (thread-safe through a Qt signal)."""
from __future__ import annotations

import logging

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..theme import COLOR_BORDER, COLOR_ERROR, COLOR_PANEL, COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_WARN

LEVEL_COLORS = {
    logging.DEBUG: COLOR_TEXT_MUTED,
    logging.INFO: COLOR_TEXT,
    logging.WARNING: COLOR_WARN,
    logging.ERROR: COLOR_ERROR,
    logging.CRITICAL: COLOR_ERROR,
}


class LogWidget(QWidget):
    MAX_LINES = 2000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel("SYSTEM LOG")
        title.setProperty("class", "title")
        head.addWidget(title)
        self.lbl_file = QLabel("logs/")
        self.lbl_file.setProperty("class", "muted")
        head.addWidget(self.lbl_file)
        head.addStretch(1)
        self.chk_autoscroll = QCheckBox("Auto scroll")
        self.chk_autoscroll.setChecked(True)
        head.addWidget(self.chk_autoscroll)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setProperty("size", "sm")
        head.addWidget(self.btn_clear)
        lay.addLayout(head)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(self.MAX_LINES)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setStyleSheet(
            f"QPlainTextEdit {{ font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt;"
            f" background: {COLOR_PANEL}; border: 1px solid {COLOR_BORDER}; border-radius: 6px; }}")
        lay.addWidget(self.text)
        self.btn_clear.clicked.connect(self.text.clear)

    def set_log_file(self, path: str) -> None:
        self.lbl_file.setText(path)

    def append(self, text: str, level: int) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LEVEL_COLORS.get(level, COLOR_TEXT)))
        if level >= logging.ERROR:
            fmt.setFontWeight(700)
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        if self.chk_autoscroll.isChecked():
            sb = self.text.verticalScrollBar()
            sb.setValue(sb.maximum())
