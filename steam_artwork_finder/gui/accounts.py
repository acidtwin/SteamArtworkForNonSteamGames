"""The account switcher that opens from the avatar in the top bar (and from Switch in the Steam
folder pop-over): every Steam account on this computer with its avatar and how many non-Steam
games it has, a tick on the one in use. One click switches."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import icons, theme
from .gamemenu import SHADOW, FadingPopup, PopupRow, Section, Separator
from .theme import C, q

RING = "#57cbde"  # as the avatar in the top bar


@dataclass
class Account:
    name: str
    games: int | None  # non-Steam games in its shortcuts.vdf; None when it couldn't be read
    avatar: QPixmap | None = None


def games_text(n: int | None) -> str:
    if n is None:
        return "Game list couldn't be read"
    if n == 0:
        return "No non-Steam games yet"
    return f"{n} non-Steam game{'' if n == 1 else 's'}"


def draw_avatar(p: QPainter, r: QRectF, name: str, pm: QPixmap | None, ring: bool = True):
    """The Steam picture, or the name's first letter on a gradient, inside a cyan ring."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(RING) if ring else q("track_off"))
    p.drawRoundedRect(r, 4, 4)
    inner = r.adjusted(2, 2, -2, -2)
    path = QPainterPath()
    path.addRoundedRect(inner, 2, 2)
    p.setClipPath(path)
    if pm is not None and not pm.isNull():
        p.drawPixmap(inner, pm, QRectF(pm.rect()))
    else:
        g = QLinearGradient(inner.topLeft(), inner.bottomRight())
        g.setColorAt(0, QColor("#6b3f8c"))
        g.setColorAt(1, q("action"))
        p.fillRect(inner, g)
        p.setPen(QColor("white"))
        f = QFont(p.font())
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(inner, Qt.AlignmentFlag.AlignCenter, (name.strip()[:1] or "?").upper())
    p.restore()


class AccountRow(PopupRow):
    """Avatar, name, game count; a tick when it's the account in use."""

    def __init__(self, acct: Account, current: bool, parent=None):
        super().__init__(acct.name, parent)
        self.acct, self.current = acct, current
        self.setAccessibleName(f"{acct.name}, {games_text(acct.games)}" + (", in use" if current else ""))

    def sizeHint(self):
        return QSize(280, 52)

    def enterEvent(self, e):
        self.setFocus()
        super().enterEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.current or self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["fade"]) if self.hasFocus() and not self.current else q("select"))
            p.drawRoundedRect(r, 2, 2)
        draw_avatar(p, QRectF(8, (r.height() - 32) / 2, 32, 32), self.acct.name, self.acct.avatar, self.current)
        x = 8 + 32 + 10
        right = r.width() - (34 if self.current else 10)
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("white") if self.hasFocus() else q("text"))
        name = p.fontMetrics().elidedText(self.acct.name, Qt.TextElideMode.ElideRight, int(right - x))
        p.drawText(QRectF(x, 8, right - x, 19), Qt.AlignmentFlag.AlignVCenter, name)
        f.setWeight(QFont.Weight.Normal)
        f.setPointSizeF(theme.pt("faint"))
        p.setFont(f)
        p.setPen(q("text3"))
        p.drawText(QRectF(x, 27, right - x, 17), Qt.AlignmentFlag.AlignVCenter, games_text(self.acct.games))
        if self.current:
            icons.draw(p, "tick", QRect(int(r.right()) - 25, (self.height() - 15) // 2, 15, 15), q("blue_ink"))


class LinkRow(PopupRow):
    """A blue text link on the right of the footer."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent, QFont.Weight.DemiBold)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 16, 30)

    def enterEvent(self, e):
        self.setFocus()
        super().enterEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["fade"]))
            p.drawRoundedRect(QRectF(self.rect()), 2, 2)
        p.setPen(QColor("white") if self.hasFocus() else QColor(C["fade_ink"]))
        p.setFont(self.font())
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text())


class AccountMenu(FadingPopup):
    """`chosen(i)` when another account is clicked; `folder()` for the Steam Folder… link, which
    only shows when `folder_text` (the Steam folder, shortened) is given."""

    chosen = Signal(int)
    folder = Signal()

    def __init__(self, accounts: list[Account], current: int, folder_text: str = "", parent=None):
        super().__init__(parent, width=300)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAccessibleName("Steam accounts")
        lay = self.lay
        lay.addWidget(Section("Steam accounts", self))
        self.rows: list[PopupRow] = []
        for i, a in enumerate(accounts):
            row = AccountRow(a, i == current, self)
            row.clicked.connect(lambda _=False, i=i: self._pick(i, current))
            self.rows.append(row)
            lay.addWidget(row)
        if len(accounts) == 1:
            hint = QLabel("This is the only account that has signed in to Steam on this computer. "
                          "Sign in to another one in Steam and it will show here.")
            hint.setWordWrap(True)
            f = QFont(hint.font())
            f.setPointSizeF(theme.pt("faint"))
            hint.setFont(f)
            hint.setStyleSheet(f"color: {C['text3']}; padding: 6px 8px 8px 8px;")
            lay.addWidget(hint)
        if folder_text:
            lay.addWidget(Separator(self))
            foot = QWidget(self)
            fl = QHBoxLayout(foot)
            fl.setContentsMargins(8, 0, 2, 0)
            fl.setSpacing(8)
            where = QLabel()
            f = QFont(where.font())
            f.setPointSizeF(theme.pt("faint"))
            where.setFont(f)
            where.setStyleSheet(f"color: {C['text3']};")
            where.setText(where.fontMetrics().elidedText(folder_text, Qt.TextElideMode.ElideMiddle, 160))
            where.setToolTip(folder_text)
            fl.addWidget(where, 1)
            link = LinkRow("Steam Folder…", foot)
            link.setAccessibleName("Steam Folder…")
            link.clicked.connect(self._folder)
            fl.addWidget(link)
            self.rows.append(link)
            lay.addWidget(foot)

    def _rows(self):
        return self.rows

    def _pick(self, i: int, current: int):
        self.close()
        if i != current:
            self.chosen.emit(i)

    def _folder(self):
        self.close()
        self.folder.emit()

    def popup_under(self, anchor: QWidget):
        """Open below `anchor`, right edges lined up."""
        self.adjustSize()
        top_right = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 4))
        x = top_right.x() - (self.width() - 2 * SHADOW)
        cur = next((r for r in self.rows if isinstance(r, AccountRow) and r.current), None)
        self._show_at(x, top_right.y(), cur or (self.rows[0] if self.rows else None))
