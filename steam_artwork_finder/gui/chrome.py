"""The window chrome: header, content bar, status bar and the Steam pop-over."""

from __future__ import annotations

import html
import re
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QFontInfo, QFontMetrics, QGuiApplication, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from . import icons, theme
from .theme import C, q

RING = "#57cbde"


def font(scale: float, weight: QFont.Weight = QFont.Weight.Normal, spacing_em: float = 0.0) -> QFont:
    """The UI font at `scale` × the base size, with letter spacing in em."""
    app = QApplication.instance()
    f = QFont(app.font()) if app else QFont()
    f.setPointSizeF(theme.base_pt() * scale)
    f.setWeight(weight)
    if spacing_em:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, QFontInfo(f).pixelSize() * spacing_em)
    return f


def _label(text: str, f: QFont, color: str) -> QLabel:
    lab = QLabel(text)
    lab.setFont(f)
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    return lab


def _vline(h: int, margin: int = 0) -> QWidget:
    w = QFrame()
    w.setFixedSize(1, h)
    w.setStyleSheet(f"background: {C['line']};")
    if not margin:
        return w
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(margin, 0, margin, 0)
    lay.addWidget(w)
    return box


def bold_numbers(text: str, color: str | None = None) -> str:
    """`text` as rich text with every number in bold (and `color`)."""
    style = f' style="color:{color}"' if color else ""
    return re.sub(r"\d[\d,.]*", lambda m: f"<b{style}>{m.group(0)}</b>", html.escape(text))


class _Click(QWidget):
    """A plain area that acts as a button: hover colour, Enter/Space, `clicked` signal."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.hovered = False
        self.kbd_focus = False

    def focusInEvent(self, e):
        self.kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.kbd_focus = False
        self.update()
        super().focusOutEvent(e)

    def event(self, e):
        if e.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.hovered = e.type() == QEvent.Type.HoverEnter
            self.hover_changed()
            self.update()
        return super().event(e)

    def hover_changed(self):
        pass

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        if self.hasFocus() and self.kbd_focus:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(q("blue"), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 3, 3)


# ---- header -----------------------------------------------------------------

class _Avatar(QWidget):
    """32 px avatar: the Steam picture, or the name's first letter on a gradient, in a cyan ring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.pm: QPixmap | None = None
        self.letter = ""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ring = QRectF(1, 1, 34, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(RING))
        p.drawRoundedRect(ring, 4, 4)
        inner = QRectF(2, 2, 32, 32)
        path = QPainterPath()
        path.addRoundedRect(inner, 2, 2)
        p.setClipPath(path)
        if self.pm and not self.pm.isNull():
            p.drawPixmap(inner, self.pm, QRectF(self.pm.rect()))
        else:
            g = QLinearGradient(inner.topLeft(), inner.bottomRight())
            g.setColorAt(0, QColor("#6b3f8c"))
            g.setColorAt(1, QColor("#1474c8"))
            p.fillRect(inner, g)
            p.setPen(QColor("white"))
            p.setFont(font(1.05, QFont.Weight.Bold))
            p.drawText(inner, Qt.AlignmentFlag.AlignCenter, self.letter)


class _Account(_Click):
    """Avatar + persona name; opens a menu of Steam users when there is more than one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 10, 4)
        lay.setSpacing(10)
        self.avatar = _Avatar()
        lay.addWidget(self.avatar)
        self.name = _label("", font(1.0, QFont.Weight.DemiBold), C["text"])
        lay.addWidget(self.name)
        self.chev = QLabel()
        self.chev.setPixmap(_chevron(C["text2"]))
        lay.addWidget(self.chev)
        self.multi = False

    def paintEvent(self, e):
        if self.multi and self.hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("raised"))
            p.drawRoundedRect(QRectF(self.rect()), 3, 3)
            p.end()
        super().paintEvent(e)


def _chevron(color: str, size: int = 12) -> QPixmap:
    dpr = QGuiApplication.instance().devicePixelRatio() if QGuiApplication.instance() else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline([QPointF(size * .22, size * .38), QPointF(size * .5, size * .66), QPointF(size * .78, size * .38)])
    p.end()
    return pm


class Header(QWidget):
    """The top bar: app mark (home), the Steam account and the ⋯ menu."""

    home_clicked = Signal()
    user_chosen = Signal(int)
    more_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("header")
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 2, 16, 1)
        lay.setSpacing(14)

        self.home = _Click()
        self.home.setAccessibleName("Steam Artwork for Non-Steam Games: back to all games")
        self.home.setToolTip("Back to all games")
        self.home.clicked.connect(self.home_clicked)
        hl = QHBoxLayout(self.home)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(14)
        mark = QLabel()
        dpr = QGuiApplication.instance().devicePixelRatio() if QGuiApplication.instance() else 1.0
        mark.setPixmap(icons.app_pixmap(32, dpr))
        mark.setFixedSize(32, 32)
        hl.addWidget(mark)
        wm = QHBoxLayout()
        wm.setSpacing(12)
        wm.addWidget(_label("STEAM ARTWORK", font(1.3, QFont.Weight.ExtraBold, 0.1), C["blue_ink"]))
        wm.addWidget(_vline(20))
        wm.addWidget(_label("FOR NON-STEAM GAMES", font(0.92, QFont.Weight.Bold, 0.14), C["text2"]))
        hl.addLayout(wm)
        lay.addWidget(self.home)
        lay.addStretch(1)

        self.account = _Account()
        self.account.setAccessibleName("Steam account")
        self.account.clicked.connect(self._open_users)
        self.account.hide()
        lay.addWidget(self.account)
        self._users: list[str] = []
        self._current = 0

        self.more = QToolButton()
        self.more.setIcon(icons.icon("more", 22, C["text"]))
        self.more.setIconSize(QSize(22, 22))
        self.more.setToolTip("More")
        self.more.setAccessibleName("More actions")
        self.more.clicked.connect(self.more_clicked)
        lay.addWidget(self.more)

    def set_users(self, labels: list[str], current: int, avatar: QPixmap | None = None):
        """Show the signed-in user; several labels make the area a switcher. Empty hides it."""
        self._users = list(labels)
        self._current = current
        if not labels or not 0 <= current < len(labels):
            self.account.hide()
            return
        name = labels[current]
        a = self.account
        a.name.setText(name)
        a.avatar.pm = avatar
        a.avatar.letter = name.strip()[:1].upper()
        a.avatar.update()
        a.multi = len(labels) > 1
        a.chev.setVisible(a.multi)
        a.setCursor(Qt.CursorShape.PointingHandCursor if a.multi else Qt.CursorShape.ArrowCursor)
        a.setFocusPolicy(Qt.FocusPolicy.TabFocus if a.multi else Qt.FocusPolicy.NoFocus)
        a.setToolTip(f"Signed in to Steam as {name}" + ("\nClick to switch Steam user" if a.multi else ""))
        a.setAccessibleName(f"Steam user {name}" + (". Switch user" if a.multi else ""))
        a.show()

    def _open_users(self):
        if len(self._users) < 2:
            return
        menu = QMenu(self)
        group = QActionGroup(menu)
        for i, name in enumerate(self._users):
            act = QAction(name, menu, checkable=True, checked=i == self._current)
            act.triggered.connect(lambda _=False, i=i: self.user_chosen.emit(i) if i != self._current else None)
            group.addAction(act)
            menu.addAction(act)
        a = self.account
        menu.exec(a.mapToGlobal(QPoint(0, a.height() + 2)))

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        g = QLinearGradient(*_css135(r))
        g.setColorAt(0, QColor("#1b2838"))
        g.setColorAt(0.55, QColor("#171d25"))
        g.setColorAt(1, QColor("#171a21"))
        p.fillRect(r, g)
        top = QLinearGradient(0, 0, r.width(), 0)
        top.setColorAt(0, q("blue"))
        top.setColorAt(0.4, q("blue_ink"))
        top.setColorAt(0.85, QColor(102, 192, 244, 0))
        p.fillRect(QRect(0, 0, r.width(), 2), top)
        p.fillRect(QRect(0, r.height() - 1, r.width(), 1), QColor("#0b0d11"))


def _css135(r: QRect) -> tuple[QPointF, QPointF]:
    """Start and end of a CSS `linear-gradient(135deg, …)` over `r`."""
    w, h = r.width(), r.height()
    k = (w + h) / 4
    return QPointF(w / 2 - k, h / 2 - k), QPointF(w / 2 + k, h / 2 + k)


# ---- content bar --------------------------------------------------------------

class _Crumb(_Click):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 6)
        self.lab = _label(text, font(0.92, QFont.Weight.DemiBold, 0.06), C["text3"])
        lay.addWidget(self.lab)

    def hover_changed(self):
        self.lab.setStyleSheet(f"color: {C['text'] if self.hovered else C['text3']}; background: transparent;")


class _Elided(QLabel):
    """A label that shrinks with an ellipsis instead of widening the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text: str):
        self._full = text
        self.setToolTip("")
        self.updateGeometry()
        self._elide()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self._full) + 4, fm.height())

    def minimumSizeHint(self):
        return QSize(40, QFontMetrics(self.font()).height())

    def resizeEvent(self, e):
        self._elide()

    def _elide(self):
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class _Rule(QWidget):
    """A 1 px line fading out to the right, filling the spare width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(1)
        self.setMinimumWidth(12)

    def paintEvent(self, e):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, q("line"))
        end = q("line")
        end.setAlpha(0)
        g.setColorAt(1, end)
        p.fillRect(self.rect(), g)


ButtonSpec = tuple[str, str, bool, Callable]


class ContentBar(QWidget):
    """The bar above the page: LIBRARY crumb, page title, and the page's buttons."""

    home_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cbar")
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 16, 1)
        lay.setSpacing(12)
        self.crumb = _Crumb("LIBRARY")
        self.crumb.setToolTip("Back to all games")
        self.crumb.setAccessibleName("Library: back to all games")
        self.crumb.clicked.connect(self.home_clicked)
        lay.addWidget(self.crumb)
        lay.addWidget(_vline(22, 4))
        self.title = _Elided()
        self.title.setStyleSheet(f"color: {C['text']}; background: transparent;")
        lay.addWidget(self.title, 0)
        self.count = _label("", font(1.08, QFont.Weight.DemiBold, 0.04), C["text3"])
        lay.addWidget(self.count)
        self.rule = _Rule()
        lay.addWidget(self.rule, 1)
        self._btn_box = QHBoxLayout()
        self._btn_box.setSpacing(8)
        lay.addLayout(self._btn_box)
        self.buttons: list[QAbstractButton] = []
        self.show_library(None)

    def show_library(self, count: int | None):
        """Start page title: NON-STEAM GAMES (count)."""
        self.title.setFont(font(1.15, QFont.Weight.Bold, 0.09))
        self.title.setText("NON-STEAM GAMES")
        self.title.setAccessibleName("Non-Steam games")
        self.count.setText("" if count is None else f"({count})")
        self.count.setVisible(count is not None)

    def show_game(self, name: str):
        """Game page title: the game's name, normal case."""
        self.title.setFont(font(1.3, QFont.Weight.Bold))
        self.title.setText(name)
        self.title.setAccessibleName(name)
        self.count.hide()

    def set_buttons(self, specs: list[ButtonSpec]) -> list[QAbstractButton]:
        """Replace the buttons. Each spec is (text, kind "go"|"ghost"|"more", enabled, callback)."""
        for b in self.buttons:
            self._btn_box.removeWidget(b)
            b.hide()  # deleteLater waits for the event loop; don't leave it drawn meanwhile
            b.deleteLater()
        self.buttons = []
        for text, kind, enabled, cb in specs:
            if kind == "more":
                b = QToolButton()
                b.setIcon(icons.icon("more", 20, C["text"]))
                b.setIconSize(QSize(20, 20))
                b.setToolTip(text or "More")
                b.setAccessibleName(text or "More")
                b.clicked.connect(lambda _=False, b=b, cb=cb: cb(b.mapToGlobal(QPoint(0, b.height()))))
            else:
                b = QPushButton(text)
                b.setAccessibleName(text)
                if kind == "go":
                    b.setProperty("kind", "go")
                if cb is not None:
                    b.clicked.connect(lambda _=False, cb=cb: cb())
            b.setEnabled(enabled)
            self._btn_box.addWidget(b)
            self.buttons.append(b)
        return self.buttons

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, q("bar"))
        p.fillRect(QRect(0, r.height() - 1, r.width(), 1), q("line"))


# ---- status bar -----------------------------------------------------------------

class Spinner(QWidget):
    """A 14 px spinning arc."""

    def __init__(self, parent=None, size: int = 14):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.angle = 0
        self._timer = QTimer(self, interval=16, timeout=self._tick)

    def _tick(self):
        self.angle = (self.angle + 7) % 360  # ~0.8 s a turn
        self.update()

    def showEvent(self, e):
        self._timer.start()

    def hideEvent(self, e):
        self._timer.stop()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(q("raised"), 2))
        p.drawEllipse(r)
        p.setPen(QPen(q("blue_ink"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(r, int((90 - self.angle) * 16), -90 * 16)


class StatusItem(QAbstractButton):
    """A flat 30 px status button: an icon (or a dot) and a semibold word."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFont(font(0.96, QFont.Weight.DemiBold))
        self.icon_name = ""
        self.icon_color = C["text3"]
        self.dot: str | None = None
        self.dot_alpha = 1.0
        self.setFixedHeight(30)

    def set_state(self, text: str, icon: str = "", color: str = "", dot: str | None = None):
        self.setText(text)
        self.icon_name, self.icon_color, self.dot = icon, color or C["text3"], dot
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        lead = 8 if self.dot else 15
        return QSize(8 + lead + 6 + QFontMetrics(self.font()).horizontalAdvance(self.text()) + 8, 30)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.underMouse() or self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("raised"))
            p.drawRoundedRect(r, 3, 3)
        if self.hasFocus():
            p.setPen(QPen(q("blue"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        x = 8
        cy = r.height() / 2
        if self.dot:
            col = QColor(self.dot)
            col.setAlphaF(self.dot_alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QRectF(x, cy - 4, 8, 8))
            x += 8
        elif self.icon_name:
            icons.draw(p, self.icon_name, QRectF(x, cy - 7.5, 15, 15), self.icon_color)
            x += 15
        x += 6
        p.setPen(q("text"))
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, r.width() - x, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


def _tip(title: str, hint: str) -> str:
    return (f"<div style='white-space:pre'>{html.escape(title)}<br>"
            f"<span style='color:{C['text3']}; font-size:small'>{html.escape(hint)}</span></div>")


PROCESS = {
    "closed": ("Steam Closed", "stop", "text3"),
    "running": ("Steam Running", "play", "wait"),
    "restart": ("Restart Needed", "restart", "blue_ink"),
}
CONNECTION = {"connected": ("Connected", "ok"), "connecting": ("Connecting", "wait"), "disconnected": ("Disconnected", "off")}


class StatusBar(QWidget):
    """The 36 px bar at the bottom: a message on the left, Steam and SteamGridDB state on the right."""

    steam_clicked = Signal()
    process_clicked = Signal()
    connection_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusbar")
        self.setFixedHeight(36)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 1, 16, 0)
        lay.setSpacing(4)

        left = QWidget()
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        self.spinner = Spinner()
        ll.addWidget(self.spinner)
        self.done_icon = QLabel()
        self.done_icon.setFixedSize(16, 16)
        ll.addWidget(self.done_icon)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.setFont(font(0.96))
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ll.addWidget(self.message, 1)
        left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(left, 1)
        self.left = left

        self.steam = StatusItem()
        self.steam.clicked.connect(self.steam_clicked)
        lay.addWidget(self.steam)
        self.div1 = _vline(16, 4)
        lay.addWidget(self.div1)
        self.process = StatusItem()
        self.process.clicked.connect(self.process_clicked)
        lay.addWidget(self.process)
        self.div2 = _vline(16, 4)
        lay.addWidget(self.div2)
        self.connection = StatusItem()
        self.connection.clicked.connect(self.connection_clicked)
        self.connection.setToolTip(_tip("SteamGridDB", "Click to change your API key"))
        lay.addWidget(self.connection)

        self._pulse = QVariantAnimation(self, startValue=1.0, endValue=0.35, duration=600)
        self._pulse.valueChanged.connect(self._pulse_step)
        self._pulse.finished.connect(self._pulse_flip)

        self.kind: str | None = None
        self.steam_found = False
        self.process_state = "closed"
        self.connection_state = "disconnected"
        self.set_message(None, "")
        self.set_steam(False, "")
        self.set_process("closed")
        self.set_connection("disconnected")

    def set_message(self, kind: str | None, text: str = ""):
        """Left side: "busy" (spinner), "info", "done" (green check) or None (empty)."""
        self.kind = kind
        self.spinner.setVisible(kind == "busy")
        self.done_icon.setVisible(kind == "done")
        if kind == "done":
            dpr = self.devicePixelRatioF()
            self.done_icon.setPixmap(icons.pixmap("check-circle", 16, C["ok"], dpr))
        if kind is None or not text:
            self.message.setText("")
        elif kind == "done":
            self.message.setText(f"<span style='color:{C['text']}'>{bold_numbers(text)}</span>")
        elif kind == "busy":
            self.message.setText(f"<span style='color:{C['text']}'>{bold_numbers(text)}</span>")
        else:
            self.message.setText(f"<span style='color:{C['text2']}'>{bold_numbers(text, C['text'])}</span>")
        self.message.setAccessibleName(text if kind else "")

    def set_steam(self, found: bool, path_text: str = ""):
        self.steam_found = found
        if found:
            self.steam.set_state("Steam Detected", "check-circle", C["ok"])
            self.steam.setToolTip(_tip(path_text or "Steam folder", "Click to find Steam or choose another folder"))
            self.steam.setAccessibleName("Steam Detected. Find Steam or choose another folder")
        else:
            self.steam.set_state("Steam Undetected", "x-circle", C["off"])
            self.steam.setToolTip(_tip("No Steam folder found", "Click to choose your Steam folder"))
            self.steam.setAccessibleName("Steam Undetected. Choose the Steam folder")
        self.process.setVisible(found)
        self.div2.setVisible(found)

    def set_process(self, state: str):
        """"closed", "running" or "restart"."""
        self.process_state = state
        word, ic, col = PROCESS[state]
        self.process.set_state(word, ic, C[col])
        self.process.setToolTip(_tip(word, "Click for more about Steam"))
        self.process.setAccessibleName(f"{word}. More about Steam")

    def connection_anchor(self) -> QPoint:
        """Global top-right of the SteamGridDB item, for the API key pop-over."""
        return self.connection.mapToGlobal(QPoint(self.connection.width(), 0))

    def steam_anchor(self) -> QPoint:
        """Global top-centre of the Steam folder item, for its pop-over."""
        return self.steam.mapToGlobal(QPoint(self.steam.width() // 2, 0))

    def process_anchor(self) -> QPoint:
        """Global top-centre of the Steam process item, for the pop-over."""
        return self.process.mapToGlobal(QPoint(self.process.width() // 2, 0))

    def set_connection(self, state: str):
        """"connected", "connecting" or "disconnected"."""
        self.connection_state = state
        word, col = CONNECTION[state]
        self.connection.set_state(word, dot=C[col])
        self.connection.setAccessibleName(f"SteamGridDB: {word}. Change API key")
        self.connection.dot_alpha = 1.0
        if state == "connecting":
            if self._pulse.state() != QVariantAnimation.State.Running:
                self._pulse.setDirection(QVariantAnimation.Direction.Forward)
                self._pulse.start()
        else:
            self._pulse.stop()

    def _pulse_step(self, v):
        self.connection.dot_alpha = float(v)
        self.connection.update()

    def _pulse_flip(self):
        if self.connection_state != "connecting":
            return
        fwd = QVariantAnimation.Direction.Forward
        back = QVariantAnimation.Direction.Backward
        self._pulse.setDirection(back if self._pulse.direction() == fwd else fwd)
        self._pulse.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), q("ground"))
        p.fillRect(QRect(0, 0, self.width(), 1), q("line"))


# ---- Steam pop-over ----------------------------------------------------------------

POPOVER_TEXT = {
    "closed": "Steam is closed, so new artwork is saved straight away and shows the next time you open Steam.",
    "running": "Steam is open. It only loads new artwork when it starts, and it rewrites its game list when it "
               "closes, so icons are saved once Steam has closed.",
    "restart": "New artwork is saved. Restart Steam to see it.",
}


class Overlay(QFrame):
    """A pop-over drawn inside the main window, above the status bar. It fades in and
    out, and closes on Esc or a click anywhere else in the window (not when you switch
    to another app, so you can copy something and come back)."""

    FADE_MS = 140

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("spop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.anchor_widget: QWidget | None = None  # clicks on it toggle instead of closing
        self._fx = QGraphicsOpacityEffect(self, opacity=0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QVariantAnimation(self, duration=self.FADE_MS)
        self._anim.valueChanged.connect(lambda v: self._fx.setOpacity(float(v)))
        self._anim.finished.connect(self._faded)
        self._leaving = False
        self.hide()

    def open_above(self, anchor: QPoint, align_right: bool = False):
        """Open above `anchor` (a global point), kept inside the window."""
        host = self.parentWidget()
        self.adjustSize()
        w, h = self.width(), self.sizeHint().height()
        a = host.mapFromGlobal(anchor)
        x = a.x() - w + 24 if align_right else a.x() - w // 2
        x = max(8, min(x, host.width() - w - 8))
        y = max(8, a.y() - h - 8)
        self.setGeometry(x, y, w, h)
        self.raise_()
        self._leaving = False
        if not self.isVisible():
            self._fx.setOpacity(0.0)
            self.show()
        self._fade(self._fx.opacity(), 1.0)
        QApplication.instance().installEventFilter(self)

    def is_open(self) -> bool:
        return self.isVisible() and not self._leaving

    def dismiss(self):
        if not self.isVisible() or self._leaving:
            return
        self._leaving = True
        QApplication.instance().removeEventFilter(self)
        self._fade(self._fx.opacity(), 0.0)

    def _fade(self, start: float, end: float):
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def _faded(self):
        if self._leaving:
            self._leaving = False
            self.hide()

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget) and obj.window() is self.window():
            inside = obj is self or self.isAncestorOf(obj)
            on_anchor = self.anchor_widget is not None and (
                obj is self.anchor_widget or self.anchor_widget.isAncestorOf(obj))
            if not inside and not on_anchor:
                self.dismiss()
        elif t == QEvent.Type.Resize and obj is self.parentWidget():
            self.dismiss()  # its anchor moved
        elif t == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape and self.isVisible():
            self.dismiss()
            return True
        return False


class SteamPopover(Overlay):
    """The pop-up above the Steam process item: what the state means, auto-restart, Restart Now."""

    restart_clicked = Signal()
    auto_restart_changed = Signal(bool)
    apply_on_quit_changed = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName("Steam")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        head.addWidget(self.icon)
        self.title = _label("", font(1.08, QFont.Weight.Bold), C["text"])
        head.addWidget(self.title, 1)
        lay.addLayout(head)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setStyleSheet(f"color: {C['text2']}; background: transparent;")
        lay.addWidget(self.body)
        self.auto = QCheckBox("Restart Steam automatically when I click Apply")
        self.auto.setObjectName("spopcheck")
        self.auto.toggled.connect(self.auto_restart_changed)
        lay.addWidget(self.auto)
        self.on_quit = QCheckBox("Apply waiting artwork when I quit, without asking")
        self.on_quit.setObjectName("spopcheck")
        self.on_quit.toggled.connect(self.apply_on_quit_changed)
        lay.addWidget(self.on_quit)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.restart = QPushButton("Restart Steam Now")
        self.restart.clicked.connect(self._restart)
        row.addWidget(self.restart)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.dismiss)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.state = "closed"

    def set_apply_on_quit(self, on: bool):
        self.on_quit.blockSignals(True)
        self.on_quit.setChecked(on)
        self.on_quit.blockSignals(False)

    def set_auto_restart(self, on: bool):
        self.auto.blockSignals(True)
        self.auto.setChecked(on)
        self.auto.blockSignals(False)

    def set_state(self, state: str, waiting_icons: int = 0):
        self.state = state
        word, ic, col = PROCESS[state]
        self.title.setText(word)
        self.icon.setPixmap(icons.pixmap(ic, 16, C[col], self.devicePixelRatioF()))
        text = POPOVER_TEXT[state]
        if state == "restart" and waiting_icons > 0:
            text += f" {waiting_icons} {'icon is' if waiting_icons == 1 else 'icons are'} waiting to be linked."
        self.body.setText(text)
        self.restart.setVisible(state != "closed")
        self.setAccessibleDescription(text)

    def show_for(self, state: str, anchor: QPoint, waiting_icons: int = 0):
        """Open above `anchor` (global point), kept inside the window."""
        self.set_state(state, waiting_icons)
        self.open_above(anchor)
        (self.restart if self.restart.isVisible() else self.close_btn).setFocus()

    def _restart(self):
        self.dismiss()
        self.restart_clicked.emit()
