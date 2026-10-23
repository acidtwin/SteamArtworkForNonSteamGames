"""Moving between the library and a game page, and the Apply button filling as it writes.

From the cover wall the clicked cover grows into the game page's small cover while the library
fades away (going back, the cover shrinks home again, faster). From anywhere else, a quick
cross-fade. The new page is live underneath from the first frame: this overlay only holds a
picture of the page being left and the cover in flight, so nothing waits on it."""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QEvent, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

GROW_MS, BACK_MS, FADE_MS = 380, 220, 160


class PageTransition(QWidget):
    def __init__(self, stage: QWidget):
        super().__init__(stage.parentWidget())
        self.stage = stage
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self.old: QPixmap | None = None
        self.cover: QPixmap | None = None
        self.src = self.dst = None
        self.ms = FADE_MS
        self.fade_ms = FADE_MS
        self.t = 1.0
        self._hide = None
        self._start = 0.0
        self._ease = QEasingCurve(QEasingCurve.Type.OutQuint)
        self._timer = QTimer(self, interval=16, timeout=self._step)

    def snapshot(self) -> QPixmap:
        """The page about to be left, as it looks now."""
        return self.stage.grab()

    def run(self, old: QPixmap, cover: QPixmap | None = None, src=None, dst=None, back: bool = False,
            hide=None):
        """Fade `old` off the live page; with a cover, fly it from src to dst. Each rect is in the
        stage's coordinates, or a function giving it (asked on every frame, so a page still
        laying itself out lands the cover in the right place); None means no flight. `hide(True)`
        is called to empty the cover's landing place while it flies, and `hide(False)` on landing."""
        self.finish()  # one at a time: a run still going lands at once
        self.old = old
        self.cover = cover if cover is not None and not cover.isNull() and src is not None and dst is not None else None
        self.src, self.dst = src, dst
        flying = self.cover is not None
        self._hide = hide if flying else None
        if self._hide:
            self._hide(True)
        self.ms = (BACK_MS if back else GROW_MS) if flying else FADE_MS
        self.fade_ms = min(self.ms, 140 if back else 200)  # the old page goes before the cover lands
        self.setGeometry(self.stage.geometry())
        self.raise_()
        self.show()
        self.t = 0.0
        self._start = time.monotonic()
        self._timer.start()
        self.update()

    def finish(self):
        self._timer.stop()
        self.hide()
        self.old = self.cover = None
        hide, self._hide = self._hide, None
        if hide:
            hide(False)

    def _step(self):
        self.t = min(1.0, (time.monotonic() - self._start) * 1000 / self.ms)
        if self.t >= 1.0:
            self.finish()
        else:
            self.update()

    def paintEvent(self, _):
        if self.old is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        elapsed = self.t * self.ms
        fade = min(1.0, elapsed / self.fade_ms)
        if fade < 1.0:
            p.setOpacity(1.0 - self._ease.valueForProgress(fade))
            p.drawPixmap(0, 0, self.old)
            p.setOpacity(1.0)
        s, d = (v() if callable(v) else v for v in (self.src, self.dst))
        if self.cover is not None and s is not None and d is not None:
            e = self._ease.valueForProgress(self.t)
            r = QRectF(s.x() + (d.x() - s.x()) * e, s.y() + (d.y() - s.y()) * e,
                       s.width() + (d.width() - s.width()) * e, s.height() + (d.height() - s.height()) * e)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(4):  # a soft shadow while it's in the air
                p.setBrush(Qt.GlobalColor.black)
                p.setOpacity(0.08)
                p.drawRoundedRect(r.adjusted(-i, 2 + i, i, 4 + 2 * i), 4 + i, 4 + i)
            p.setOpacity(1.0)
            clip = QPainterPath()
            clip.addRoundedRect(r, 3, 3)
            p.setClipPath(clip)
            p.drawPixmap(r, self.cover, QRectF(self.cover.rect()))


class ApplyFill(QWidget):
    """A light band filling an Apply button from the left as the writes go, laid over the button
    (it takes no clicks). fill(button, fraction) makes or finds the one on `button`."""

    def __init__(self, button: QWidget):
        super().__init__(button)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.at = 0.0
        self._anim = QVariantAnimation(self, duration=250, easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        button.installEventFilter(self)  # follow the button as its text changes its width

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False

    @staticmethod
    def fill(button: QWidget, fraction: float | None):
        bar = button.findChild(ApplyFill)
        if fraction is None:
            if bar is not None:
                bar.hide()
                bar.at = 0.0
            return
        if bar is None:
            bar = ApplyFill(button)
        bar.setGeometry(button.rect())
        if not bar.isVisible():
            bar.at = 0.0
            bar.show()
        bar.raise_()
        running = bar._anim.state() == QVariantAnimation.State.Running
        target = bar._anim.endValue() if running else bar.at
        if abs(fraction - target) > 1e-3:
            bar._anim.stop()
            bar._anim.setStartValue(bar.at)
            bar._anim.setEndValue(float(fraction))
            bar._anim.start()

    def _step(self, v):
        self.at = float(v)
        self.update()

    def paintEvent(self, _):
        if self.at <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 56))
        p.drawRoundedRect(QRectF(0, 0, self.width() * self.at, self.height()), 2, 2)
