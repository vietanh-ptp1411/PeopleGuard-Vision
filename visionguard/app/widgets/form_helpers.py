"""Helpers that keep the configuration pages short.

A machine vision system has a lot of knobs, but an operator only ever touches a handful of
them. Everything else is commissioning detail that should stay out of sight until it is
needed - that is what AdvancedSection is for: register a row once, and a single checkbox
hides or shows the whole lot.
"""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                               QLabel, QLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

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


def page_header(title: str, subtitle: str = "", trailing: QWidget | None = None) -> QWidget:
    """The one-line heading every configuration page starts with.

    Tab labels are short by necessity; this is where a page gets to say, in plain
    Vietnamese, what it is for - which is most of the answer to "what does this
    parameter even do".
    """
    bar = QWidget()
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(2, 0, 2, 0)
    lay.setSpacing(12)
    block = QVBoxLayout()
    block.setContentsMargins(0, 0, 0, 0)
    block.setSpacing(1)
    lab = QLabel(title)
    lab.setProperty("class", "pagetitle")
    block.addWidget(lab)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setProperty("class", "pagesub")
        sub.setWordWrap(True)
        block.addWidget(sub)
    lay.addLayout(block, 1)
    if trailing is not None:
        lay.addWidget(trailing, 0, Qt.AlignmentFlag.AlignTop)
    return bar


#: Narrowest a text box, combo or spin box is allowed to get. Below this the value inside
#: stops being readable, which is worse than a little sideways scrolling.
MIN_FIELD_W = 76

#: Most a single layout may inset from the left or right. Four nesting levels at the Qt
#: default of 9-11 px each is most of a hundred pixels spent on nothing.
MAX_SIDE_MARGIN = 6


def fit_narrow_panel(root: QWidget) -> None:
    """Let a configuration page shrink to the width of the side panel it lives in.

    The panel is a fixed 470 px and the pages were laid out wider than that, so six of the
    eight tabs ended up with a horizontal scroll bar. That is the worst of both worlds: the
    labels on the left scroll out of sight, so the operator is reading values with no idea
    which setting they belong to, and has to drag a bar at the very bottom of the window to
    find out.

    Three changes do almost all of the work, and all three are about letting things shrink
    rather than making them smaller:

    * long form rows put their label ABOVE the field instead of beside it, which buys back
      the whole label column exactly on the rows that could not fit;
    * fields get a small minimum width so they stop demanding their preferred one;
    * a word-wrapped hint is allowed to be as narrow as the panel - by default Qt keeps
      enough width for the longest word plus its layout margins, which for these hints was
      wider than the panel itself.

    Applied to every tab from one place, so a tab added later gets it without being asked.
    """
    for area in root.findChildren(QScrollArea):
        # Deliberately AsNeeded, not AlwaysOff. Forcing the bar off does not make content
        # fit - it makes the overflow unreachable, which is worse than scrolling to it. The
        # bar is kept honest by making the pages and the panel actually fit instead.
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.setWidgetResizable(True)

    for form in root.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    # Margins are the quiet half of the problem. Every level of nesting - page, scroll
    # body, group box, the layout inside it - adds its own left and right margin, and these
    # pages are four deep: on the Storage tab the widest single widget was 462 px but the
    # page demanded 600, the other 138 being nothing but stacked-up margins. Capping the
    # HORIZONTAL margins alone fixes the width without touching the vertical rhythm, which
    # is what makes the pages readable in the first place.
    for layout in root.findChildren(QLayout):
        left, top, right, bottom = layout.getContentsMargins()
        layout.setContentsMargins(min(left, MAX_SIDE_MARGIN), top, min(right, MAX_SIDE_MARGIN), bottom)

    for widget in root.findChildren(QWidget):
        if isinstance(widget, QComboBox):
            # Left alone, a combo demands the width of its LONGEST entry even while showing
            # a short one. This is the trick the AI Model page already used to fit, which is
            # why that page never grew a scroll bar - now every page gets it.
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(8)
            widget.setMinimumWidth(MIN_FIELD_W)
        elif isinstance(widget, (QLineEdit, QAbstractSpinBox)):
            widget.setMinimumWidth(MIN_FIELD_W)
        # A word-wrapped QLabel is deliberately NOT given a small minimum width. It looks
        # like the obvious way to let a hint shrink, and it costs nothing at 100% scaling -
        # but on a display at 125% the form asks the label how tall it would be at that
        # minimum, gets the height of the text wrapped into a one-pixel column, and leaves
        # a 600 px hole in the middle of the page. Wrapped labels already shrink on their
        # own; the minimum is the trap.
        elif isinstance(widget, QAbstractItemView):
            widget.setMinimumWidth(MIN_FIELD_W * 2)
            header = getattr(widget, "horizontalHeader", None)
            if header is not None:
                header().setStretchLastSection(True)
