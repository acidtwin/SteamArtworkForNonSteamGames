"""Menus drawn like Steam's: the right-click menu for a game (a header with the game, then its
actions with icons) and the ⋯ menu in the top bar (sections, shortcuts and switches)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QFrame, QVBoxLayout, QWidget

from . import icons, theme
from .theme import C, q

SHADOW = 12  # room around the menu for its shadow
RADIUS = 6


@dataclass
class Item:
    text: str
    icon: str
    fn: Callable[[], None]
    enabled: bool = True
    tone: str = ""  # "go" (green icon), "bad" (red text) or ""
    badge: int = 0  # a green count on the right, e.g. changes waiting
    shortcut: str = ""  # shown faintly on the right, e.g. "Ctrl+R"
    toggle: bool | None = None  # a switch on the right; clicking flips it and keeps the menu open
    external: bool = False  # opens a web page: a small arrow on the right


class MenuRow(QAbstractButton):
    def __init__(self, item: Item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setText(item.text)
        self.setAccessibleName(f"{item.text}, {item.badge}" if item.badge else item.text)
        if item.toggle is not None:
            self.setCheckable(True)
            self.setChecked(item.toggle)
        self.setEnabled(item.enabled)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("control"))
        self.setFont(f)

    def sizeHint(self):
        return QSize(244, max(40, self.fontMetrics().height() + 20))

    def keyPressEvent(self, e):
        self.parent().keyPressEvent(e)  # the menu handles arrows, Enter and Esc

    def enterEvent(self, e):
        if self.isEnabled():
            self.setFocus()
        super().enterEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isEnabled()
        if on and self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select"))
            p.drawRoundedRect(r, 3, 3)
        tone = self.item.tone if on else ""
        if not on:
            fg, ic = QColor("#5d6875"), QColor("#4d5764")
        elif tone == "bad":
            fg = ic = QColor("#ff8a7a")
        else:
            fg = q("text")
            ic = QColor("#8fc94a") if tone == "go" else (q("text") if self.hasFocus() else q("text2"))
        isz = 16
        icons.draw(p, self.item.icon, QRect(12, (self.height() - isz) // 2, isz, isz), ic)
        p.setPen(fg)
        p.setFont(self.font())
        p.drawText(QRectF(12 + isz + 10, 0, r.width() - 40, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        right = r.right() - 10
        if self.item.toggle is not None:
            track = QRectF(right - 30, (r.height() - 17) / 2, 30, 17)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["blue"]) if self.isChecked() else QColor("#4a5462"))
            p.drawRoundedRect(track, 8.5, 8.5)
            knob = 13
            x = track.right() - knob - 2 if self.isChecked() else track.left() + 2
            p.setBrush(QColor("#dcdedf") if on else QColor("#8a95a2"))
            p.drawEllipse(QRectF(x, track.top() + 2, knob, knob))
        elif self.item.external:
            icons.draw(p, "external", QRect(int(right) - 13, (self.height() - 13) // 2, 13, 13), q("text3"))
        elif self.item.shortcut:
            f = QFont(self.font())
            f.setPointSizeF(theme.pt("faint"))
            p.setFont(f)
            p.setPen(q("text3"))
            p.drawText(QRectF(0, 0, right, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       self.item.shortcut)
        if self.item.badge and on:
            f = QFont(self.font())
            f.setPointSizeF(theme.pt("tag") * 1.1)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            text = str(self.item.badge)
            w = max(20, p.fontMetrics().horizontalAdvance(text) + 12)
            b = QRectF(r.right() - 10 - w, (r.height() - 18) / 2, w, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["go"]))
            p.drawRoundedRect(b, 9, 9)
            p.setPen(QColor("white"))
            p.drawText(b, Qt.AlignmentFlag.AlignCenter, text)


class Header(QWidget):
    """The game's icon (or initial), name, and how many pieces it has."""

    def __init__(self, name: str, sub: str, icon: QPixmap | None, parent=None):
        super().__init__(parent)
        from .library import draw_game_icon  # library imports this module's caller

        self._draw_icon = draw_game_icon
        self.name, self.sub, self.icon = name, sub, icon
        self.setAccessibleName(f"{name}, {sub}")
        self.setFixedHeight(52)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dpr = self.devicePixelRatioF()
        icon = self.icon
        if icon is not None and not icon.isNull():
            icon = icon.scaled(QSize(32, 32) * dpr, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            icon.setDevicePixelRatio(dpr)
        else:
            icon = QPixmap()  # null: the initial is drawn instead
        self._draw_icon(p, QRect(8, 8, 32, 32), self.name, icon)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(q("text"))
        x = 8 + 32 + 10
        name = p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, self.width() - x - 8)
        p.drawText(QRectF(x, 8, self.width() - x, 17), Qt.AlignmentFlag.AlignVCenter, name)
        f.setWeight(QFont.Weight.Normal)
        f.setPointSizeF(theme.pt("faint"))
        p.setFont(f)
        p.setPen(q("text3"))
        p.drawText(QRectF(x, 25, self.width() - x, 15), Qt.AlignmentFlag.AlignVCenter, self.sub)


class Section(QWidget):
    """A small heading over a group of rows."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text.upper()
        self.setAccessibleName(text)
        self.setFixedHeight(28)

    def paintEvent(self, _):
        p = QPainter(self)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("tag") * 1.05)
        f.setWeight(QFont.Weight.ExtraBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 109)
        p.setFont(f)
        p.setPen(q("text3"))
        p.drawText(QRectF(12, 0, self.width() - 12, self.height() - 3),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self.text)


class GameMenu(QFrame):
    """A popup that closes on any click outside it, like a QMenu.

    `items` holds rows, None for a line between groups, and strings for section headings.
    With no `name` there's no game header (the ⋯ menu)."""

    closed = Signal()

    def __init__(self, name: str | None, sub: str, icon: QPixmap | None, items: list[Item | str | None],
                 parent=None, width: int = 268):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAccessibleName(f"{name} menu" if name else "More")
        self.setFixedWidth(width + SHADOW * 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW + 6, SHADOW + 6, SHADOW + 6, SHADOW + 6)
        lay.setSpacing(0)
        if name:
            lay.addWidget(Header(name, sub, icon, self))
            lay.addWidget(Separator(self))
        self.rows: list[MenuRow] = []
        for it in items:
            if it is None:
                lay.addWidget(Separator(self))
                continue
            if isinstance(it, str):
                lay.addWidget(Section(it, self))
                continue
            row = MenuRow(it, self)
            if it.toggle is not None:
                row.toggled.connect(lambda on, fn=it.fn: fn(on))  # stays open, like a switch
            else:
                row.clicked.connect(lambda _=False, fn=it.fn: self._run(fn))
            self.rows.append(row)
            lay.addWidget(row)

    def popup(self, pos: QPoint):
        self.adjustSize()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x, y = pos.x() - SHADOW, pos.y() - SHADOW
        if x + self.width() > area.right():
            x = pos.x() - self.width() + SHADOW
        if y + self.height() > area.bottom():
            y = area.bottom() - self.height()
        self.move(max(area.left(), x), max(area.top(), y))
        self.show()
        self.activateWindow()
        first = next((r for r in self.rows if r.isEnabled()), None)
        if first:
            first.setFocus()

    def _run(self, fn):
        self.close()
        fn()

    def keyPressEvent(self, e):
        rows = [r for r in self.rows if r.isEnabled()]
        if not rows:
            return self.close() if e.key() == Qt.Key.Key_Escape else None
        cur = next((i for i, r in enumerate(rows) if r.hasFocus()), -1)
        k = e.key()
        if k in (Qt.Key.Key_Down, Qt.Key.Key_Tab):
            rows[(cur + 1) % len(rows)].setFocus()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Backtab):
            rows[(cur - 1) % len(rows)].setFocus()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and cur >= 0:
            rows[cur].click()
        elif k == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.closed.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        for i in range(SHADOW, 0, -1):  # a soft shadow, drawn as fading rings
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, round(70 * (1 - i / SHADOW) ** 2)))
            p.drawRoundedRect(body.adjusted(-i, -i + 4, i, i + 4), RADIUS + i, RADIUS + i)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)
        p.fillPath(path, QColor("#1d232b"))
        p.setPen(QPen(q("line"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)


class Separator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(9)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(QRect(2, 4, self.width() - 4, 1), q("line"))
