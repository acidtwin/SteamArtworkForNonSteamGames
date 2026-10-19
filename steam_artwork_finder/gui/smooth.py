"""Smooth scrolling: mouse-wheel steps glide instead of jumping, in every scrolling area."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, Qt, QVariantAnimation
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QScrollBar

DURATION = 240  # ms for one wheel step to settle


class _Glide(QVariantAnimation):
    """Moves one scroll bar towards a target value, which more wheel steps push further."""

    def __init__(self, bar: QScrollBar):
        super().__init__(bar)
        self.bar = bar
        self.target = bar.value()
        self.setDuration(DURATION)
        self.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.valueChanged.connect(lambda v: self.bar.setValue(round(v)))
        bar.sliderPressed.connect(self.stop)  # dragging the bar wins

    def push(self, by: int) -> bool:
        """Glide `by` units further. False when the bar is already at that end."""
        start = self.target if self.state() == self.State.Running else self.bar.value()
        target = max(self.bar.minimum(), min(self.bar.maximum(), start + by))
        if target == self.bar.value() and self.state() != self.State.Running:
            return False
        self.stop()
        self.target = target
        self.setStartValue(float(self.bar.value()))
        self.setEndValue(float(target))
        self.start()
        return True


class SmoothScroll(QObject):
    """Application-wide filter. Touchpads already scroll by pixels, so only wheel
    notches are smoothed; Shift turns a vertical wheel into sideways scrolling."""

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.Wheel or not e.pixelDelta().isNull():
            return False
        area = obj.parent()
        if not isinstance(area, QAbstractScrollArea) or obj is not area.viewport():
            return False
        d = e.angleDelta()
        sideways = abs(d.x()) > abs(d.y()) or e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        bar = area.horizontalScrollBar() if sideways else area.verticalScrollBar()
        notches = (d.x() or d.y() if sideways else d.y()) / 120
        if not notches or bar.maximum() <= bar.minimum():
            return False  # nothing to scroll here: let an outer area have it
        glide = bar.findChild(_Glide)
        if glide is None:
            glide = _Glide(bar)
        lines = QApplication.wheelScrollLines()
        return glide.push(round(-notches * lines * bar.singleStep()))


def install(app: QApplication) -> SmoothScroll:
    f = SmoothScroll(app)
    app.installEventFilter(f)
    return f
