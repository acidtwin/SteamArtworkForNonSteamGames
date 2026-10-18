"""Small building blocks shared by the pages."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QWidget

from .theme import C, q

_cache: dict[tuple, QPixmap] = {}


def load_pixmap(path: Path | None = None, data: bytes | None = None, key=None) -> QPixmap:
    """A full-size pixmap from a file or bytes, cached."""
    if path is not None:
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return QPixmap()
    if key is not None and key in _cache:
        return _cache[key]
    pm = QPixmap()
    if path is not None:
        pm.load(str(path))
    elif data:
        pm.loadFromData(data)
    if key is not None and not pm.isNull():
        if len(_cache) > 600:
            _cache.clear()
        _cache[key] = pm
    return pm


def fill_rect(pm: QPixmap, target: QSize) -> QPixmap:
    """Scale to cover target, cropping the overflow (like CSS object-fit: cover)."""
    s = pm.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                  Qt.TransformationMode.SmoothTransformation)
    x = (s.width() - target.width()) // 2
    y = (s.height() - target.height()) // 2
    return s.copy(x, y, target.width(), target.height())


def draw_checker(p: QPainter, rect: QRectF, size: int = 10):
    p.save()
    p.setClipRect(rect)
    p.fillRect(rect, QColor("#232a33"))
    c = QColor("#2b333e")
    y = int(rect.top())
    row = 0
    while y < rect.bottom():
        x = int(rect.left()) + (size if row % 2 else 0)
        while x < rect.right():
            p.fillRect(x, y, size, size, c)
            x += size * 2
        y += size
        row += 1
    p.restore()


def draw_dashed_empty(p: QPainter, rect: QRectF, text: str, plus: bool = True, focus=False):
    p.save()
    p.fillRect(rect, QColor("#1f2a38"))
    pen = QPen(q("blue") if focus else QColor("#4a5666"), 1.5, Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)
    p.setPen(q("text2"))
    f = QFont(p.font())
    f.setPointSizeF(9.5)
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    if plus:
        pf = QFont(f)
        pf.setPointSizeF(15)
        pf.setWeight(QFont.Weight.Light)
        p.setFont(pf)
        p.setPen(q("blue_ink"))
        p.drawText(rect.adjusted(0, -28, 0, 0), Qt.AlignmentFlag.AlignCenter, "+")
        p.setFont(f)
        p.setPen(q("text2"))
        p.drawText(rect.adjusted(6, 14, -6, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
    else:
        p.drawText(rect.adjusted(12, 0, -12, -14), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
                   | Qt.TextFlag.TextWordWrap, text)
    p.restore()


def draw_badge(p: QPainter, rect: QRectF, text: str = "IN USE"):
    p.save()
    f = QFont(p.font())
    f.setPointSizeF(7)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    w = p.fontMetrics().horizontalAdvance(text) + 10
    r = QRectF(rect.left() + 4, rect.bottom() - 20, w, 16)
    p.fillRect(r, q("blue"))
    p.setPen(QColor("#0b1a26"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


def draw_dots(p: QPainter, x: float, y: float, states: list[bool], d: float = 7, gap: float = 4):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, on in enumerate(states):
        r = QRectF(x + i * (d + gap), y, d, d)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("blue_ink"))
        else:
            p.setPen(QPen(q("text3"), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r.adjusted(0.6, 0.6, -0.6, -0.6))
    p.restore()
    return x + len(states) * (d + gap)


class ArtView(QWidget):
    """One piece of artwork drawn at a fixed shape, or a dashed empty slot."""

    clicked = Signal()
    hovered = Signal(bool)

    def __init__(self, size: QSize, empty_text: str = "", checker: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(size)
        self.pixmap = QPixmap()
        self.backdrop = QPixmap()  # drawn behind (logo-on-banner)
        self.empty_text = empty_text
        self.checker = checker
        self.badge = False
        self.selected = False
        self.round = 3
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_pixmap(self, pm: QPixmap | None, backdrop: QPixmap | None = None):
        self.pixmap = pm or QPixmap()
        self.backdrop = backdrop or QPixmap()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, self.round, self.round)
        if self.pixmap.isNull() and self.backdrop.isNull():
            draw_dashed_empty(p, r, self.empty_text, focus=self.hasFocus())
            return
        p.setClipPath(clip)
        if not self.backdrop.isNull():
            p.drawPixmap(self.rect(), fill_rect(self.backdrop, self.size()))
        elif self.checker:
            draw_checker(p, r)
        if not self.pixmap.isNull():
            if self.checker or not self.backdrop.isNull():
                # Logos keep their shape, centred with a margin.
                box = self.size() * (0.7 if not self.backdrop.isNull() else 0.86)
                s = self.pixmap.scaled(box, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s)
            else:
                p.drawPixmap(self.rect(), fill_rect(self.pixmap, self.size()))
        elif not self.backdrop.isNull():
            p.fillRect(r, QColor(0, 0, 0, 90))
        p.setClipping(False)
        if self.badge:
            draw_badge(p, r)
        if self.selected or self.hasFocus() or self.underMouse():
            p.setPen(QPen(q("blue") if (self.selected or self.hasFocus()) else QColor("#5a6a7e"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), self.round, self.round)

    def enterEvent(self, e):
        self.hovered.emit(True)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered.emit(False)
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class Notice(QFrame):
    """A full-width strip under the header."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notice")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 8, 16, 8)
        self.icon = QLabel()
        lay.addWidget(self.icon)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.text.setMinimumWidth(200)
        lay.addWidget(self.text, 1)
        self.buttons = QHBoxLayout()
        lay.addLayout(self.buttons)
        self.hide()

    def show_message(self, tone: str, text: str, actions: list[tuple[str, object]] = ()):
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon.setText("⚠" if tone == "warn" else "ⓘ")
        self.icon.setStyleSheet(f"color: {C['warn'] if tone == 'warn' else C['blue_ink']}; font-size: 13pt;")
        self.text.setText(text)
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w:
                w.deleteLater()
        for label, fn in actions:
            b = QPushButton(label)
            b.clicked.connect(fn)
            self.buttons.addWidget(b)
        self.show()


class Toast(QFrame):
    """Bottom-centre message with an optional Undo, floating over the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 4, 6, 4)
        self.label = QLabel()
        lay.addWidget(self.label)
        self.action = QPushButton("Undo")
        self.action.clicked.connect(self._act)
        lay.addWidget(self.action)
        self._fn = None
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def show_message(self, text: str, undo=None, ms: int = 8000):
        self.label.setText(text)
        self._fn = undo
        self.action.setVisible(undo is not None)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 28)
        self.raise_()
        self.show()
        self.timer.start(ms)

    def _act(self):
        fn, self._fn = self._fn, None
        self.hide()
        if fn:
            fn()


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping onto new lines."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisible() and not test:
                continue
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x = rect.x()
                y += line + self._spacing
                line = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y()


def label(text: str, name: str = "", wrap=False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if wrap:
        lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lab


def quiet_button(text: str, fn=None) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("kind", "quiet")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if fn:
        b.clicked.connect(fn)
    return b
