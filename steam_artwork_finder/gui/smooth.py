"""Smooth scrolling: mouse-wheel steps glide instead of jumping, in every scrolling area."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QInputDevice
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QScrollBar


class _Glide(QObject):
    """Eases one scroll bar towards a target that each wheel step pushes further.

    The bar follows the target like a critically damped spring: speed builds up and
    eases off smoothly, and steps arriving mid-glide add to the motion instead of
    restarting a curve, which is what made a plain animation feel jerky. Position is
    kept in fractions of a pixel and frames run at the screen's refresh rate."""

    OMEGA = 20.0  # spring stiffness, 1/s; settles in about 6/OMEGA seconds

    def __init__(self, bar: QScrollBar):
        super().__init__(bar)
        self.bar = bar
        self.pos = self.target = float(bar.value())
        self.speed = 0.0
        self._own = False  # True while this glide is the one moving the bar
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._frame)
        bar.sliderPressed.connect(self.stop)  # dragging the bar wins
        bar.valueChanged.connect(self._moved)

    def running(self) -> bool:
        return self.timer.isActive()

    def stop(self):
        self.timer.stop()

    def _moved(self, v: int):
        if not self._own and round(self.pos) != v:  # moved some other way: follow it
            self.stop()
            self.pos = self.target = float(v)
            self.speed = 0.0

    def push(self, by: float) -> bool:
        """Glide `by` units further. False when the bar is already at that end."""
        if not self.running():
            self.pos = self.target = float(self.bar.value())
            self.speed = 0.0
        target = max(float(self.bar.minimum()), min(float(self.bar.maximum()), self.target + by))
        if target == self.target and not self.running():
            return False
        self.target = target
        if not self.running():
            screen = self.bar.screen()
            hz = screen.refreshRate() if screen is not None else 60.0
            self.timer.start(max(4, int(1000 / max(30.0, min(hz, 240.0)))))
            self.clock.start()
        return True

    def _frame(self):
        dt = min(0.05, max(0.001, self.clock.restart() / 1000))
        w = self.OMEGA
        for _ in range(4):  # small steps keep the spring stable on slow frames
            h = dt / 4
            self.speed += (w * w * (self.target - self.pos) - 2 * w * self.speed) * h
            self.pos += self.speed * h
        if self.pos < self.bar.minimum() or self.pos > self.bar.maximum():
            self.pos = max(float(self.bar.minimum()), min(float(self.bar.maximum()), self.pos))
        if abs(self.target - self.pos) < 0.5 and abs(self.speed) < 20:
            self.pos, self.speed = self.target, 0.0
            self.stop()
        v = round(self.pos)
        if v != self.bar.value():
            self._own = True
            self.bar.setValue(v)
            self._own = False


def from_touchpad(e) -> bool:
    """Touchpads already scroll smoothly: they send gesture phases, or say what they are.
    Mouse wheels can carry a pixel delta too (KDE on Wayland does), so that alone isn't enough."""
    if e.phase() != Qt.ScrollPhase.NoScrollPhase:
        return True
    dev = e.device()
    return dev is not None and dev.type() == QInputDevice.DeviceType.TouchPad


class SmoothScroll(QObject):
    """Application-wide filter that smooths mouse-wheel scrolling everywhere.
    Shift turns a vertical wheel into sideways scrolling."""

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.Wheel or from_touchpad(e):
            return False
        area = obj.parent()
        if not isinstance(area, QAbstractScrollArea) or obj is not area.viewport():
            return False
        d = e.angleDelta()
        if d.isNull():
            return False
        sideways = abs(d.x()) > abs(d.y()) or e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        bar = area.horizontalScrollBar() if sideways else area.verticalScrollBar()
        notches = (d.x() or d.y() if sideways else d.y()) / 120  # high-resolution wheels send fractions
        if not notches or bar.maximum() <= bar.minimum():
            return False  # nothing to scroll here: let an outer area have it
        glide = bar.findChild(_Glide)
        if glide is None:
            glide = _Glide(bar)
        lines = QApplication.wheelScrollLines()
        by = -notches * lines * bar.singleStep()
        return glide.push(by)


def install(app: QApplication) -> SmoothScroll:
    f = SmoothScroll(app)
    app.installEventFilter(f)
    return f
