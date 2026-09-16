"""Stop the mouse wheel from silently changing settings.

Qt lets a spin box, a combo box or a slider react to the wheel as soon as the pointer is over
it, even without focus. Scrolling through a configuration page therefore changes values by
accident - an HTTP port quietly becomes 75 instead of 80, and the camera never connects again.

The guard lets the wheel through only when the widget really has focus (the user clicked or
tabbed into it). Otherwise the event is forwarded to the surrounding scroll area, so the page
scrolls as the user expected.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider

GUARDED = (QAbstractSpinBox, QComboBox, QSlider)


class WheelGuard(QObject):
    """Application wide event filter: install once on the QApplication."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, GUARDED):
            return False
        if obj.hasFocus():
            return False                     # focused on purpose: the wheel may adjust it
        scroller = self._scroll_area(obj)
        if scroller is not None:
            QApplication.sendEvent(scroller.viewport(), event)
        return True                          # never let the value change by accident

    @staticmethod
    def _scroll_area(widget) -> QAbstractScrollArea | None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None


def install(app: QApplication) -> WheelGuard:
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
