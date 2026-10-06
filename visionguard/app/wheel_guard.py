"""Stop the mouse wheel from silently changing settings.

Qt lets a spin box, a combo box or a slider react to the wheel as soon as the pointer is over
it. Scrolling through a configuration page therefore changes values by accident - an HTTP port
quietly becomes 75 instead of 80, and the camera never connects again.

The wheel never changes a guarded widget: values change only by clicking, typing or picking
from the list. The event is forwarded to the surrounding scroll area, so the page scrolls as
the user expected.

Spin boxes and combo boxes default to Qt.WheelFocus, and QApplication hands them focus for a
wheel event *before* any event filter runs - so a "let it through when focused" rule is no
guard at all. The policy is therefore demoted to StrongFocus as each widget is polished.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider

GUARDED = (QAbstractSpinBox, QComboBox, QSlider)


class WheelGuard(QObject):
    """Application wide event filter: install once on the QApplication."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        etype = event.type()
        if etype == QEvent.Type.Polish and isinstance(obj, GUARDED):
            if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # wheel must not grab focus
            return False
        if etype != QEvent.Type.Wheel or not isinstance(obj, GUARDED):
            return False
        scroller = self._scroll_area(obj)
        if scroller is not None:
            QApplication.sendEvent(scroller.viewport(), event)
        return True                          # never let the value change by the wheel

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
