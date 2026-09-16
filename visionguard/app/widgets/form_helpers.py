"""Helpers that keep the configuration pages short.

A machine vision system has a lot of knobs, but an operator only ever touches a handful of
them. Everything else is commissioning detail that should stay out of sight until it is
needed - that is what AdvancedSection is for: register a row once, and a single checkbox
hides or shows the whole lot.
"""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QWidget

from ..theme import COLOR_TEXT_MUTED


class AdvancedSection:
    """Rows and whole groups that are only needed while commissioning."""

    def __init__(self) -> None:
        self._rows: List[Tuple[QFormLayout, QWidget]] = []
        self._widgets: List[QWidget] = []
        self._visible = False

    # ------------------------------------------------------------------ registration
    def row(self, layout: QFormLayout, field: QWidget) -> QWidget:
        """Register a form row by its field widget; returns the field for chaining."""
        self._rows.append((layout, field))
        return field

    def rows(self, layout: QFormLayout, *fields: QWidget) -> None:
        for field in fields:
            self.row(layout, field)

    def widget(self, widget: QWidget) -> QWidget:
        """Register a whole widget or group box."""
        self._widgets.append(widget)
        return widget

    def widgets(self, *widgets: QWidget) -> None:
        for widget in widgets:
            self.widget(widget)

    # ------------------------------------------------------------------ visibility
    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        for layout, field in self._rows:
            try:
                layout.setRowVisible(field, self._visible)
            except (RuntimeError, ValueError):
                pass                       # row already removed
        for widget in self._widgets:
            widget.setVisible(self._visible)

    def apply(self) -> None:
        self.set_visible(self._visible)

    @property
    def visible(self) -> bool:
        return self._visible

    def __len__(self) -> int:
        return len(self._rows) + len(self._widgets)


def advanced_checkbox(section: AdvancedSection, text: str = "Advanced settings") -> QCheckBox:
    """A checkbox wired to a section, with the count of what it hides."""
    box = QCheckBox(text)
    box.setToolTip("Hiện các tham số nâng cao (chỉ cần khi lắp đặt / căn chỉnh)")
    box.toggled.connect(section.set_visible)
    return box


def hint(text: str) -> QLabel:
    """A short explanation line under a field group."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 9pt;")
    return label
