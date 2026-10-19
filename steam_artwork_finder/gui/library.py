"""The game list on the left and the start page's cover wall."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from ..artwork import SLOTS
from .theme import C, q
from .widgets import ScaledCache, draw_dashed_empty, draw_dots, label

ROLE_INDEX = Qt.ItemDataRole.UserRole  # index into the shortcut list, or -1 for "All games"
ICON = QSize(32, 32)


def draw_all_games_icon(p: QPainter, rect: QRect, color: QColor) -> None:
    """The shelf icon for the "All games" row, in the given colour (rendered once per colour)."""
    icons.draw(p, "all-games", rect, color)


_PALETTE = ["#2d5a8c", "#6b3f8c", "#3f7e1d", "#8c5a2d", "#8c2d3f", "#2d7e7a", "#5a5a8c", "#7e6b1d"]


def initials(name: str) -> str:
    words = [w for w in name.replace(":", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[1][0]).upper()


def draw_game_icon(p: QPainter, rect: QRect, name: str, icon: QPixmap | None):
    """`icon` is a ready scaled pixmap, None while it decodes, or null if there is none."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), 3, 3)
    p.setClipPath(path)
    if icon is not None and not icon.isNull():
        p.fillRect(rect, QColor("#11151a"))
        p.drawPixmap(rect, icon)
    else:
        p.fillRect(rect, q("raised"))
        if icon is not None:  # nothing to show: the game's initial
            p.setPen(q("text2"))
            f = QFont(p.font())
            f.setPointSizeF(theme.pt("faint"))
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, initials(name)[:1])
    p.restore()


def status_text(win, idx: int) -> tuple[str, str | None]:
    """('3 of 5' or 'All artwork', tag or None) for a game."""
    sc = win.sfile.shortcuts[idx]
    tag = win.status.get(sc.appid)
    have = sum(win.coverage(sc))
    return ("All artwork" if have == 5 else f"{have} of 5"), tag


def spoken(win, idx: int) -> str:
    """What a screen reader says for a game row or tile."""
    sc = win.sfile.shortcuts[idx]
    text, tag = status_text(win, idx)
    parts = [sc.name or "unnamed game", text if text == "All artwork" else f"{text} artwork"]
    if tag:
        parts.append(TAGS[tag][0])
    return ", ".join(parts)


TAGS = {
    "notfound": ("Not found", "#3b2422", C["bad"]),
    "getting": ("Getting", C["info_ground"], C["blue_ink"]),
    "check": ("Check match", C["warn_ground"], C["warn"]),
}


def draw_tag(p: QPainter, x: int, y: int, tag: str) -> int:
    text, bg, fg = TAGS[tag]
    p.save()
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("tag"))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    w = QFontMetrics(f).horizontalAdvance(text.upper()) + 10
    r = QRect(x, y, w, QFontMetrics(f).height() + 4)
    p.fillRect(r, QColor(bg))
    p.setPen(QColor(fg))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text.upper())
    p.restore()
    return x + w


class GameRowDelegate(QStyledItemDelegate):
    def __init__(self, win, view):
        super().__init__()
        self.win = win
        self.view = view
        self.icon_cache = ScaledCache()

    def sizeHint(self, option, index):
        fm = QFontMetrics(option.font)
        return QSize(200, max(50, fm.height() * 2 + 18))

    def paint(self, p: QPainter, option, index):
        p.save()
        r = option.rect.adjusted(6, 1, -6, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select") if selected else q("raised"))
            p.drawRoundedRect(QRectF(r), 3, 3)
        idx = index.data(ROLE_INDEX)
        icon_rect = QRect(r.x() + 8, r.y() + (r.height() - ICON.height()) // 2, ICON.width(), ICON.height())
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("white") if selected else q("text"))
        tx = icon_rect.right() + 12
        if idx == -1:
            draw_all_games_icon(p, icon_rect, QColor("white") if selected else q("text2"))
            p.setPen(QColor("white") if selected else q("text"))
            p.drawText(QRect(tx, r.y(), r.width() - tx, r.height()), Qt.AlignmentFlag.AlignVCenter, "All games")
            p.restore()
            return
        sc = self.win.sfile.shortcuts[idx]
        ipath = self.win.slot_path(sc, SLOTS["icon"])
        icon = self.icon_cache.get(ipath, ICON, self.view.viewport().update) if ipath else QPixmap()
        draw_game_icon(p, icon_rect, sc.name, icon)
        lh = QFontMetrics(f).height()
        name = QFontMetrics(f).elidedText(sc.name or "(unnamed)", Qt.TextElideMode.ElideRight, r.right() - tx - 8)
        p.drawText(QRect(tx, r.y() + 6, r.right() - tx, lh), Qt.AlignmentFlag.AlignVCenter, name)
        states = self.win.coverage(sc)
        y2 = r.y() + 6 + lh + 4
        x = draw_dots(p, tx, y2 + 4, states)
        text, tag = status_text(self.win, idx)
        if tag:
            draw_tag(p, int(x) + 6, y2, tag)
        else:
            sf = QFont(option.font)
            sf.setPointSizeF(theme.pt("caption"))
            p.setFont(sf)
            p.setPen(q("text3"))
            p.drawText(QRect(int(x) + 6, y2 - 1, 160, lh), Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()


class GameList(QWidget):
    """Search, filter buttons, then "All games" and one row per game."""

    picked = Signal(int)  # shortcut index, or -1 for the start page

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("side")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 8, 8)
        lay.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your games")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        lay.addWidget(self.search)
        seg = QHBoxLayout()
        seg.setSpacing(2)
        self.filters = QButtonGroup(self)
        self.filter_buttons = {}
        for key in ("all", "missing", "check"):
            b = QPushButton()
            b.setProperty("kind", "seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.filters.addButton(b)
            self.filter_buttons[key] = b
            seg.addWidget(b)
        self.filter_buttons["all"].setChecked(True)
        self.filters.buttonClicked.connect(lambda *_: self.apply_filter())
        lay.addLayout(seg)
        self.list = QListWidget()
        self.list.setAccessibleName("Games")
        self.list.setItemDelegate(GameRowDelegate(win, self.list))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._changed)
        # Holding an arrow key moves through many rows; open the page once the cursor rests.
        self._settle = QTimer(self, singleShot=True, interval=150, timeout=self._emit_current)
        lay.addWidget(self.list, 1)

    def rebuild(self):
        cur = self.current_index()
        self.list.blockSignals(True)
        self.list.clear()
        it = QListWidgetItem("All games")
        it.setData(ROLE_INDEX, -1)
        it.setToolTip("All games")
        self.list.addItem(it)
        for i, sc in enumerate(self.win.sfile.shortcuts):
            it = QListWidgetItem(sc.name)  # text is read by screen readers; the delegate paints
            it.setData(ROLE_INDEX, i)
            it.setToolTip(sc.name)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.update_counts()
        self.select(cur if cur is not None else -1)

    def refresh_spoken(self):
        n = len(self.win.sfile.shortcuts)
        for row in range(1, self.list.count()):
            it = self.list.item(row)
            if it.data(ROLE_INDEX) < n:
                it.setData(Qt.ItemDataRole.AccessibleTextRole, spoken(self.win, it.data(ROLE_INDEX)))

    def update_counts(self):
        n = len(self.win.sfile.shortcuts)
        missing = sum(1 for sc in self.win.sfile.shortcuts if not all(self.win.coverage(sc)))
        check = sum(1 for sc in self.win.sfile.shortcuts if self.win.status.get(sc.appid) in ("notfound", "check"))
        for key, text, count in (("all", "All", n), ("missing", "Missing art", missing), ("check", "To check", check)):
            self.filter_buttons[key].setText(f"{text}  {count}")
            self.filter_buttons[key].setAccessibleName(f"Show {text.lower()}, {count}")
        self.refresh_spoken()
        self.apply_filter()

    def apply_filter(self):
        term = self.search.text().strip().casefold()
        mode = next(k for k, b in self.filter_buttons.items() if b.isChecked())
        for row in range(self.list.count()):
            it = self.list.item(row)
            idx = it.data(ROLE_INDEX)
            if idx == -1:
                it.setHidden(bool(term))
                continue
            sc = self.win.sfile.shortcuts[idx]
            show = not term or term in sc.name.casefold()
            if mode == "missing":
                show = show and not all(self.win.coverage(sc))
            elif mode == "check":
                show = show and self.win.status.get(sc.appid) in ("notfound", "check")
            it.setHidden(not show)
        self.list.viewport().update()

    def current_index(self) -> int | None:
        it = self.list.currentItem()
        return it.data(ROLE_INDEX) if it else None

    def select(self, idx: int):
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_INDEX) == idx:
                self.list.setCurrentRow(row)
                return
        self.list.setCurrentRow(0)

    def refresh(self):
        self.refresh_spoken()
        self.list.viewport().update()

    def _changed(self, cur, _prev):
        if cur is not None:
            self._settle.start()

    def _emit_current(self):
        cur = self.list.currentItem()
        if cur is not None:
            self.picked.emit(cur.data(ROLE_INDEX))


class WallDelegate(QStyledItemDelegate):
    COVER = QSize(141, 212)

    def __init__(self, win, view):
        super().__init__()
        self.win = win
        self.view = view
        self.covers = ScaledCache()

    def sizeHint(self, option, index):
        return QSize(self.COVER.width() + 14, self.COVER.height() + QFontMetrics(option.font).height() * 2 + 24)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        r = QRectF(option.rect.x() + 7, option.rect.y() + 6, self.COVER.width(), self.COVER.height())
        path = self.win.cover_path(sc)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if path is None:
            draw_dashed_empty(p, r, sc.name, plus=False, focus=hover)
        else:
            pm = self.covers.get(path, self.COVER, self.view.viewport().update)
            clip = QPainterPath()
            clip.addRoundedRect(r, 3, 3)
            p.setClipPath(clip)
            if pm is None or pm.isNull():
                p.fillRect(r, q("raised"))  # decoding, or not an image
            else:
                p.drawPixmap(r.topLeft(), pm)
            p.setClipping(False)
            if hover:
                p.setPen(q("blue"))
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        p.setFont(f)
        p.setPen(q("text"))
        lh = QFontMetrics(f).height()
        name = QFontMetrics(f).elidedText(sc.name, Qt.TextElideMode.ElideRight, int(r.width()))
        p.drawText(QRectF(r.left(), r.bottom() + 6, r.width(), lh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        y2 = r.bottom() + 6 + lh + 4
        x = draw_dots(p, r.left(), y2 + 5, self.win.coverage(sc), d=6, gap=3)
        text, tag = status_text(self.win, idx)
        sf = QFont(option.font)
        sf.setPointSizeF(theme.pt("label"))
        p.setFont(sf)
        p.setPen(q("text3"))
        p.drawText(QRectF(x + 4, y2, 100, lh), Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()


class StartPage(QWidget):
    picked = Signal(int)

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 16, 0)
        lay.setSpacing(8)
        lay.addWidget(label("Your non-Steam games", "title"))
        self.found = label("", "muted", wrap=True)
        lay.addWidget(self.found)
        self.summary = label("", "summary", wrap=True)  # wraps at large font sizes or narrow panes
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setContentsMargins(16, 12, 16, 12)
        lay.addSpacing(6)
        lay.addWidget(self.summary)
        self.wall = QListWidget()
        self.wall.setViewMode(QListView.ViewMode.IconMode)
        self.wall.setResizeMode(QListView.ResizeMode.Adjust)
        self.wall.setMovement(QListView.Movement.Static)
        self.wall.setUniformItemSizes(True)
        self.wall.setMouseTracking(True)
        self.wall.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.wall.verticalScrollBar().setSingleStep(24)
        self.wall.setAccessibleName("Cover wall")
        self.delegate = WallDelegate(win, self.wall)
        self.wall.setItemDelegate(self.delegate)
        self.wall.setSpacing(4)
        self.wall.itemClicked.connect(lambda it: self.picked.emit(it.data(ROLE_INDEX)))
        self.wall.itemActivated.connect(lambda it: self.picked.emit(it.data(ROLE_INDEX)))
        lay.addSpacing(8)
        lay.addWidget(self.wall, 1)

    def rebuild(self):
        self.wall.clear()
        for i, sc in enumerate(self.win.sfile.shortcuts):
            it = QListWidgetItem(sc.name)
            it.setData(ROLE_INDEX, i)
            it.setToolTip(sc.name)
            self.wall.addItem(it)
        self.refresh()

    def refresh(self):
        win = self.win
        n = len(win.sfile.shortcuts)
        who = win.user.label if win.user else ""
        where = str(win.user.userdata.parent.parent).replace(str(win.home), "~") if win.user else ""
        self.found.setText(f"Found Steam for <b>{who}</b> at <code>{where}</code>  ·  "
                           f"{n} non-Steam game{'s' if n != 1 else ''}")
        for row in range(self.wall.count()):
            it = self.wall.item(row)
            if it.data(ROLE_INDEX) < n:
                it.setData(Qt.ItemDataRole.AccessibleTextRole, spoken(win, it.data(ROLE_INDEX)))
        names = ("Covers", "Wide covers", "Banners", "Logos", "Icons")
        counts = [0] * 5
        for sc in win.sfile.shortcuts:
            for i, on in enumerate(win.coverage(sc)):
                counts[i] += on
        self.summary.setText("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;".join(
            f"<span style='color:{C['text2']}'>{name}</span> <b>{c}</b> "
            f"<span style='color:{C['text2']}'>of {n}</span>" for name, c in zip(names, counts)))
        self.wall.viewport().update()
