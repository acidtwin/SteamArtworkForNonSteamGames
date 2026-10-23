"""The game list on the left and the start page's cover wall."""

from __future__ import annotations

import re
import time

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import icons, motion, theme, workers
from ..artwork import SLOTS, file_is_animated
from .gamemenu import SHADOW, FadingPopup, PopupRow
from .theme import C, q
from .widgets import FlowLayout, ScaledCache, draw_dashed_empty, draw_dots, fill_rect, quiet_button

ROLE_INDEX = Qt.ItemDataRole.UserRole  # index into the shortcut list
ICON = QSize(32, 32)
NEW_BG = C["action"]

# key, name for the tooltip, text in the menu
FILTERS = (("all", "All Games", "All"), ("missing", "Missing Artwork", "Missing Artwork"),
           ("check", "Needs a Look", "Needs a Look"))  # a match to check, or not found
# key, name, icon: how the library shows its games
VIEWS = (("list", "List", "view-list"), ("grid", "Grid", "view-grid"), ("details", "Details", "view-details"))
# key, name, icon: the order the library lists its games in
SORTS = (("az", "A to Z", "sort-az"), ("za", "Z to A", "sort-za"),
         ("missing", "Missing artwork first", "images"), ("recent", "Recently added", "history"))
PIECES = ("Covers", "Wide covers", "Banners", "Logos", "Icons")


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
        p.fillRect(rect, q("input"))
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
    """('3 of 5', 'All artwork' or 'All available', tag or None) for a game."""
    sc = win.sfile.shortcuts[idx]
    return art_text(win, sc), win.status.get(sc.appid)


def art_text(win, sc) -> str:
    """'All artwork', 'All available' (SteamGridDB has no more) or '3 of 5'."""
    have = sum(win.coverage(sc))
    return "All artwork" if have == 5 else "All available" if win.complete(sc) else f"{have} of 5"


def _natural(name: str) -> tuple:
    """A sort key for names that puts "Game 2" before "Game 10", and unnamed games last."""
    parts = [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", name.strip().casefold()) if t]
    return (not parts, parts)


def sorted_indices(win, mode: str) -> list[int]:
    """Indexes into the shortcut list in the order `mode` (a SORTS key) shows them.

    Steam adds new shortcuts to the end of its list, so "recent" is that list backwards.
    """
    scs = win.sfile.shortcuts if win.sfile else []
    order = list(range(len(scs)))
    if mode == "recent":
        return order[::-1]
    by_name = sorted(order, key=lambda i: _natural(scs[i].name))
    if mode == "za":
        named = [i for i in by_name if scs[i].name.strip()]
        return named[::-1] + [i for i in by_name if not scs[i].name.strip()]
    if mode == "missing":
        return sorted(by_name, key=lambda i: -win.coverage(scs[i]).count(False))  # stable: A to Z within
    return by_name


def is_ready(win, idx: int) -> bool:
    """The game has picks waiting for Apply."""
    return bool(win.pending.count(win.sfile.shortcuts[idx].appid))


def needs_check(win, sc) -> bool:
    return win.status.get(sc.appid) in ("notfound", "check")


def spoken(win, idx: int) -> str:
    """What a screen reader says for a game row or tile."""
    sc = win.sfile.shortcuts[idx]
    text, tag = status_text(win, idx)
    parts = [sc.name or "unnamed game", text if text.startswith("All") else f"{text} artwork"]
    if tag:
        parts.append(TAGS[tag][0])
    if is_ready(win, idx):
        parts.append("ready to apply")
    return ", ".join(parts)


def set_spoken(it: QListWidgetItem, text: str):
    """Set an item's screen-reader text, only when it changed (each set repaints and re-sorts)."""
    if it.data(Qt.ItemDataRole.AccessibleTextRole) != text:
        it.setData(Qt.ItemDataRole.AccessibleTextRole, text)


TAGS = {
    "notfound": ("Not found", C["bad_ground"], C["bad"]),
    "getting": ("Getting", C["info_ground"], C["blue_ink"]),
    "check": ("Check match", C["warn_ground"], C["warn"]),
}


def tag_font(base: QFont) -> QFont:
    f = QFont(base)
    f.setPointSizeF(theme.pt("tag"))
    f.setWeight(QFont.Weight.Bold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
    return f


def draw_tag(p: QPainter, x: int, y: int, tag: str, text: str | None = None, bg: str | None = None,
             fg: str | None = None) -> int:
    """A small caps tag with its top-left at (x, y); returns the x after it."""
    t, b, c = TAGS[tag] if tag else ("", "", "")
    text, bg, fg = (text or t).upper(), bg or b, fg or c
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = tag_font(p.font())
    p.setFont(f)
    fm = QFontMetrics(f)
    w = fm.horizontalAdvance(text) + 12
    r = QRectF(x, y, w, fm.height() + 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(r, 2, 2)
    p.setPen(QColor(fg))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    return x + w


def draw_meta(p: QPainter, win, idx: int, x: float, y: float, base: QFont, size: str, d: float, gap: float,
              width: float | None = None):
    """Coverage dots, then CHECK MATCH-style tag, else "Ready to apply", else "n of 5".
    With `width`, it stays inside it: a tag that won't fit after the dots is drawn without them."""
    sc = win.sfile.shortcuts[idx]
    f = QFont(base)
    f.setPointSizeF(theme.pt(size))
    lh = QFontMetrics(f).height()
    text, tag = status_text(win, idx)
    dots_w = 5 * (d + gap) + 3
    tag_w = QFontMetrics(tag_font(base)).horizontalAdvance((TAGS[tag][0] if tag else "").upper()) + 12
    if not (tag and width is not None and dots_w + tag_w > width):
        x = draw_dots(p, x, y + (lh - d) / 2, win.art_states(sc), d=d, gap=gap) + 3
    if tag:
        th = QFontMetrics(tag_font(base)).height() + 4
        draw_tag(p, int(x), int(y + (lh - th) / 2), tag)
        return
    p.save()
    if is_ready(win, idx):
        f.setWeight(QFont.Weight.DemiBold)
        p.setPen(q("blue_ink"))
        text = "Ready to apply"
    else:
        p.setPen(q("text3"))
    p.setFont(f)
    p.drawText(QRectF(x, y, 200, lh), Qt.AlignmentFlag.AlignVCenter, text)
    p.restore()


def list_extra(win, sc) -> tuple[str, str]:
    """The list view's middle line: the pieces waiting to be applied, else the SteamGridDB game it
    matched when that has another name."""
    picks = [(SLOTS[k].label.lower(), win.pending.get(sc.appid, k)) for k in ("grid", "wide", "hero", "logo", "icon")]
    new = [name for name, pick in picks if pick is not None and not pick.clears]
    gone = [name for name, pick in picks if pick is not None and pick.clears]
    if new or gone:
        parts = ([f"New {', '.join(new)}"] if new else []) + ([f"removing {', '.join(gone)}"] if gone else [])
        text = "; ".join(parts)
        return text[0].upper() + text[1:], "blue_ink"
    m = win.match_info(sc)
    if m and m.get("name") and m["name"].casefold() != (sc.name or "").casefold():  # only when it says something
        year = f" ({m['year']})" if m.get("year") else ""
        return f"SteamGridDB: {m['name']}{year}", "text3"
    return "", "text3"


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
        r = option.rect.adjusted(0, 1, -6, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select") if selected else q("raised"))
            p.drawRoundedRect(QRectF(r), 3, 3)
        if option.state & QStyle.StateFlag.State_HasFocus and theme.kbd_focus(self.view) and not selected:
            p.setPen(QPen(q("blue"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        icon_rect = QRect(r.x() + 8, r.y() + (r.height() - ICON.height()) // 2, ICON.width(), ICON.height())
        ipath, contain = self.win.list_icon(sc)  # its icon, else one cut from its other artwork
        icon = self.icon_cache.get(ipath, ICON, self.view.viewport().update, self.view.devicePixelRatioF(),
                                   contain) if ipath else QPixmap()
        draw_game_icon(p, icon_rect, sc.name, icon)
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("white") if selected else q("text"))
        tx = icon_rect.right() + 13
        lh = QFontMetrics(f).height()
        mf = QFont(option.font)
        mf.setPointSizeF(theme.pt("label"))
        mh = QFontMetrics(mf).height()
        top = r.y() + (r.height() - lh - mh - 3) // 2
        name = QFontMetrics(f).elidedText(sc.name or "(unnamed)", Qt.TextElideMode.ElideRight, r.right() - tx - 8)
        p.drawText(QRect(tx, top, r.right() - tx, lh), Qt.AlignmentFlag.AlignVCenter, name)
        draw_meta(p, self.win, idx, tx, top + lh + 3, option.font, "label", 6, 3)
        p.restore()


class FilterButton(QToolButton):
    """The funnel button inside the search box; blue while a filter is on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on = False
        self.open = False
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_state(self, on: bool | None = None, open: bool | None = None):
        if on is not None:
            self.on = on
        if open is not None:
            self.open = open
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        held = enabled and (self.open or self.isDown())  # pressed, or its menu is showing
        if held or hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select"))
            p.drawRoundedRect(r, 3, 3)
        if theme.kbd_focus(self) and not held:
            p.setPen(QPen(q("blue"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        elif held:
            p.setPen(QPen(q("text3"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        elif self.on and enabled:
            p.setPen(QPen(q("blue_ink"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        if not enabled:
            color = q("text2")  # the search box fades the whole row
        elif held:
            color = QColor("white")
        elif self.on:
            color = q("blue_ink")
        else:
            color = q("text") if hover else q("text2")
        icons.draw(p, "filter", QRect((self.width() - 20) // 2, (self.height() - 20) // 2, 20, 20), color)


class FilterRow(PopupRow):
    """One radio row of the filter menu: ring, text, count on the right."""

    def __init__(self, key: str, text: str, parent=None):
        super().__init__(text, parent, QFont.Weight.DemiBold)
        self.key = key
        self.count = 0
        self.setCheckable(True)

    def set_count(self, n: int):
        self.count = n
        self.setAccessibleName(f"{self.text()}, {n}")
        self.update()

    def sizeHint(self):
        return QSize(222, max(44, self.fontMetrics().height() + 20))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        hot = self.underMouse() or self.hasFocus()
        if on or hot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select") if on else q("raised"))
            p.drawRoundedRect(r, 3, 3)
        cy = r.center().y()
        ring = QRectF(11, cy - 9, 18, 18)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(26, 159, 255, 56))
            p.drawEllipse(ring.adjusted(-3, -3, 3, 3))
            p.setBrush(q("blue"))
            p.drawEllipse(ring)
            p.setBrush(QColor("white"))
            p.drawEllipse(QRectF(ring.center().x() - 4, cy - 4, 8, 8))
        else:
            p.setPen(QPen(q("text2") if hot else q("text3"), 1.5))
            p.setBrush(q("input"))
            p.drawEllipse(ring.adjusted(0.75, 0.75, -0.75, -0.75))
        p.setPen(QColor("white") if on else q("text") if hot else q("text2"))
        tx = int(ring.right()) + 11
        p.drawText(QRectF(tx, 0, r.width() - tx, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        p.setPen(q("text") if on else q("text3"))
        p.drawText(QRectF(0, 0, r.width() - 12, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(self.count))


class PopupMenu(FadingPopup):
    """A small popup with a caps heading and radio rows (anything with a `key`)."""

    chosen = Signal(str)

    def __init__(self, heading: str, spoken: str, rows: list, width: int, parent=None):
        super().__init__(parent, width, margin=5)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)  # clicking the button again just closes it
        self.setAccessibleName(spoken)
        head = QLabel(heading)
        f = QFont(head.font())
        f.setPointSizeF(theme.pt("label"))
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        head.setFont(f)
        head.setStyleSheet(f"color: {C['text3']}; background: transparent;")
        head.setContentsMargins(12, 8, 12, 6)
        self.lay.addWidget(head)
        self.rows = {}
        for row in rows:
            row.clicked.connect(lambda _=False, k=row.key: self._pick(k))
            self.rows[row.key] = row
            self.lay.addWidget(row)

    def _rows(self):
        return list(self.rows.values())

    def set_mode(self, mode: str):
        for key, row in self.rows.items():
            row.setChecked(key == mode)

    def popup_at(self, top_right: QPoint):
        """Open with the card's top-right corner at `top_right` (global)."""
        self.adjustSize()
        first = next((r for r in self.rows.values() if r.isChecked()), next(iter(self.rows.values())))
        self._show_at(top_right.x() - (self.width() - 2 * SHADOW), top_right.y(), first)

    def _pick(self, key: str):
        self.set_mode(key)
        self.close()
        self.chosen.emit(key)


class FilterMenu(PopupMenu):
    """The popup under the filter button: "SHOW" and three radio rows."""

    def __init__(self, parent=None):
        super().__init__("SHOW", "Show games", [FilterRow(key, text) for key, _tip, text in FILTERS], 232, parent)


class ViewRow(PopupRow):
    """One row of the view menu: the view's picture, its name, and a tick on the one in use."""

    def __init__(self, key: str, text: str, icon: str, parent=None):
        super().__init__(text, parent, QFont.Weight.DemiBold)
        self.key = key
        self.icon_name = icon
        self.setAccessibleName(text)
        self.setCheckable(True)

    def sizeHint(self):
        return QSize(190, max(40, self.fontMetrics().height() + 18))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        hot = self.underMouse() or self.hasFocus()
        if on or hot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select") if on else q("raised"))
            p.drawRoundedRect(r, 3, 3)
        fg = QColor("white") if on else q("text") if hot else q("text2")
        cy = r.center().y()
        icons.draw(p, self.icon_name, QRectF(12, cy - 9, 18, 18), fg)
        p.setPen(fg)
        p.drawText(QRectF(40, 0, r.width() - 70, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        if on:
            icons.draw(p, "check", QRectF(r.width() - 28, cy - 8, 16, 16), q("blue_ink"))


class ViewMenu(PopupMenu):
    """The popup beside the Home bar: List, Grid or Details."""

    def __init__(self, parent=None):
        super().__init__("VIEW", "Library view", [ViewRow(key, text, icon) for key, text, icon in VIEWS], 200, parent)


class SortMenu(PopupMenu):
    """The popup beside the Home bar: the order games are listed in."""

    def __init__(self, parent=None):
        super().__init__("SORT BY", "Sort games", [ViewRow(key, text, icon) for key, text, icon in SORTS], 250, parent)


class SearchBox(QFrame):
    """The search field with the filter button inside it on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("searchbox")
        self.setFixedHeight(40)
        self.setStyleSheet(f"""
            #searchbox {{ background: {C['input']}; border: 1px solid transparent; border-radius: 3px; }}
            #searchbox[focus="true"] {{ border-color: {C['blue']}; }}
            #searchbox QLineEdit {{ background: transparent; border: none; min-height: 34px; max-height: 34px;
                                    padding: 0 0 0 9px; }}
            #searchbox QLineEdit:disabled {{ color: {C['text3']}; }}
            #searchbox > QToolButton {{ min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; }}
            #searchbox QLineEdit QToolButton {{ min-width: 0; min-height: 0; }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 2, 2)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search your games")
        self.edit.setAccessibleName("Search your games")
        self.edit.setClearButtonEnabled(True)
        for act in self.edit.findChildren(QAction):
            if act.objectName() == "_q_qlineeditclearaction":
                act.setIcon(icons.icon("close", 16, C["text2"]))
        self.edit.installEventFilter(self)
        self.button = FilterButton()
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        self.fade = QGraphicsOpacityEffect(self, opacity=0.45)  # greyed out while there is no list
        self.fade.setEnabled(False)
        self.setGraphicsEffect(self.fade)

    def eventFilter(self, obj, e):
        if e.type() in (e.Type.FocusIn, e.Type.FocusOut):
            self.setProperty("focus", e.type() == e.Type.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return False

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self.fade.setEnabled(not self.isEnabled())


class GameListView(QListWidget):
    """The rows; a right-click (or the menu key) asks the window for the game's menu."""

    menu_requested = Signal(int, QPoint)

    def mousePressEvent(self, e):
        # A right-click only opens that game's menu: picking the row would open its page.
        if e.button() == Qt.MouseButton.RightButton:
            e.accept()
            return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        if e.reason() == e.Reason.Keyboard:
            it = self.currentItem()
            if it is None or it.isHidden():
                return
            pos = self.viewport().mapToGlobal(self.visualItemRect(it).bottomLeft() + QPoint(40, 0))
        else:
            it = self.itemAt(e.pos())
            if it is None:
                return
            pos = e.globalPos()
        self.menu_requested.emit(it.data(ROLE_INDEX), pos)


class HomeButton(QAbstractButton):
    """The slim Home bar: lit while the library is showing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on = False
        self.setText("Home")
        self.setAccessibleName("Home: all games")
        self.setToolTip("All your games")
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)

    def set_on(self, on: bool):
        self.on = on
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        hover = self.underMouse() and self.isEnabled()
        on = self.on and self.isEnabled()  # a disabled bar (no Steam, no games) mustn't look in use
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(q("select") if on else q("raised") if hover else q("input"))
        p.drawRoundedRect(r, 3, 3)
        if theme.kbd_focus(self):
            p.setPen(QPen(q("blue"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        fg = (q("text3") if not self.isEnabled() else QColor("white") if on else q("text") if hover else q("text2"))
        icons.draw(p, "home", QRectF(10, r.center().y() - 8, 16, 16), fg)
        p.setPen(fg)
        p.drawText(QRectF(34, 0, r.width() - 40, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


class ViewButton(QAbstractButton):
    """A square beside the Home bar showing the choice in use (the library view, or its
    order); opens its menu."""

    def __init__(self, options=VIEWS, label: str = "View", spoken: str = "Library view", parent=None):
        super().__init__(parent)
        self.options, self.label, self.spoken = options, label, spoken
        self.view = options[0][0]
        self.open = False
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_view("grid" if options is VIEWS else self.view)

    def set_view(self, view: str):
        self.view = view
        name = next(n for k, n, _ in self.options if k == view)
        self.setToolTip(f"{self.label}: {name}")
        self.setAccessibleName(f"{self.spoken}: {name}")
        self.update()

    def set_open(self, open: bool):
        self.open = open
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        hover = self.underMouse() and self.isEnabled()
        held = self.open or self.isDown()  # pressed, or its menu is showing
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(q("select") if held else q("raised") if hover else q("input"))
        p.drawRoundedRect(r, 3, 3)
        if theme.kbd_focus(self) and not held:
            p.setPen(QPen(q("blue"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        elif held:
            p.setPen(QPen(q("text3"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        icon = next(i for k, _, i in self.options if k == self.view)
        fg = q("text3") if not self.isEnabled() else QColor("white") if held else q("text") if hover else q("text2")
        icons.draw(p, icon, QRectF(r.center().x() - 8, r.center().y() - 8, 16, 16), fg)


class HomeBar(QWidget):
    """Home, full width, with the sort and library view buttons beside it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.home = HomeButton()
        self.sort = ViewButton(SORTS, "Sort", "Sort games")
        self.view = ViewButton()
        lay.addWidget(self.home, 1)
        lay.addWidget(self.sort)
        lay.addWidget(self.view)
        self.sort_menu = SortMenu(self)
        self.sort_menu.closed.connect(lambda: self.sort.set_open(False))
        self.sort.clicked.connect(lambda: self._open(self.sort, self.sort_menu))
        self.menu = ViewMenu(self)
        self.menu.closed.connect(lambda: self.view.set_open(False))
        self.view.clicked.connect(self.open_menu)

    def open_menu(self):
        self._open(self.view, self.menu)

    def _open(self, button: ViewButton, menu: PopupMenu):
        if menu.isVisible():
            menu.close()
            return
        menu.set_mode(button.view)
        button.set_open(True)
        menu.popup_at(button.mapToGlobal(QPoint(button.width(), button.height() + 6)))


class GameList(QWidget):
    """Search (with the filter button inside it), then one row per game."""

    picked = Signal(int)  # shortcut index
    filter_changed = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.filter_mode = "all"
        self.empty_reason: str | None = None
        self.setObjectName("side")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 8, 8)
        lay.setSpacing(10)
        self.searchbox = SearchBox()
        self.search = self.searchbox.edit
        self.search.textChanged.connect(self.apply_filter)
        self.filter_button = self.searchbox.button
        self.filter_button.clicked.connect(self.open_filter_menu)
        self.menu = FilterMenu(self)
        self.menu.chosen.connect(self.set_filter)
        self.menu.closed.connect(lambda: self.filter_button.set_state(open=False))
        lay.addWidget(self.searchbox)
        self.homebar = HomeBar()
        lay.addWidget(self.homebar)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(10, 4, 10, 4)
        el.setSpacing(0)
        self.empty_text = QLabel()
        self.empty_text.setWordWrap(True)
        self.empty_text.setStyleSheet(f"color: {C['text2']};")
        self.empty_text.setContentsMargins(0, 10, 0, 2)
        self.clear_link = quiet_button("Clear search", lambda: self.search.clear())
        self.clear_link.setStyleSheet("padding: 0; text-align: left;")
        el.addWidget(self.empty_text)
        el.addWidget(self.clear_link, 0, Qt.AlignmentFlag.AlignLeft)
        self.empty.hide()
        lay.addWidget(self.empty)

        self.list = GameListView()
        self.list.setAccessibleName("Games")
        self.list.setItemDelegate(GameRowDelegate(win, self.list))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._changed)
        self.list.menu_requested.connect(lambda i, pos: self.win.show_game_menu(i, pos))
        # Holding an arrow key moves through many rows; open the page once the cursor rests.
        self._settle = QTimer(self, singleShot=True, interval=150, timeout=self._emit_current)
        lay.addWidget(self.list, 1)
        self._update_button()

    def rebuild(self):
        cur = self.win.current if self.win.current >= 0 else None  # by game, after a reload re-numbers them
        bar = self.list.verticalScrollBar()
        v = bar.value()
        self.list.blockSignals(True)
        self.list.clear()
        scs = self.win.sfile.shortcuts
        for i in sorted_indices(self.win, self.win.library_sort):
            sc = scs[i]
            it = QListWidgetItem(sc.name)  # text is read by screen readers; the delegate paints
            it.setData(ROLE_INDEX, i)
            it.setToolTip(sc.name)
            self.list.addItem(it)
        self.select(cur if cur is not None else -1)
        self.list.blockSignals(False)
        if cur is None:
            QTimer.singleShot(0, lambda: bar.setValue(min(v, bar.maximum())))
        self.update_counts()

    def order(self) -> list[int]:
        """The shortcut indexes of the rows, top to bottom."""
        return [self.list.item(row).data(ROLE_INDEX) for row in range(self.list.count())]

    def refresh_spoken(self):
        n = len(self.win.sfile.shortcuts)
        for row in range(self.list.count()):
            it = self.list.item(row)
            if it.data(ROLE_INDEX) < n:
                set_spoken(it, spoken(self.win, it.data(ROLE_INDEX)))

    def counts(self) -> dict[str, int]:
        scs = self.win.sfile.shortcuts
        return {
            "all": len(scs),
            "missing": sum(1 for sc in scs if not self.win.complete(sc)),
            "check": sum(1 for sc in scs if needs_check(self.win, sc)),
        }

    def update_counts(self):
        for key, n in self.counts().items():
            self.menu.rows[key].set_count(n)
        self.refresh_spoken()
        self.apply_filter()

    def set_filter(self, mode: str):
        """Show "all", "missing" (games missing any artwork) or "check" (matches to check)."""
        changed = mode != self.filter_mode
        self.filter_mode = mode
        self.menu.set_mode(mode)
        self._update_button()
        self.apply_filter()
        if changed:
            self.filter_changed.emit(mode)

    def set_empty_reason(self, reason: str | None):
        """"nosteam" or "nogames" while there is no list to show; None otherwise."""
        self.empty_reason = reason
        off = reason is not None
        if off:
            self.list.clear()  # the rows index into a shortcut list that is gone
            if self.search.text():
                self.search.clear()
        self.searchbox.setEnabled(not off)
        self.homebar.setEnabled(not off)
        self.apply_filter()

    def open_filter_menu(self):
        if self.menu.isVisible():
            self.menu.close()
            return
        self.update_counts()
        self.menu.set_mode(self.filter_mode)
        box = self.searchbox
        self.filter_button.set_state(open=True)
        self.menu.popup_at(box.mapToGlobal(QPoint(box.width(), box.height() + 6)))

    def _update_button(self):
        name = next(tip for key, tip, _ in FILTERS if key == self.filter_mode)
        self.filter_button.setToolTip(name)
        self.filter_button.setAccessibleName(f"Show: {name}")
        self.filter_button.set_state(on=self.filter_mode != "all")

    def narrowed(self) -> bool:
        """Whether the search or the filter hides any games (the start page shows the same games)."""
        return bool(self.search.text().strip()) or self.filter_mode != "all"

    def matches(self, sc) -> bool:
        """Whether the search and the filter let this game through."""
        term = self.search.text().strip().casefold()
        if term and term not in sc.name.casefold():
            return False
        if self.filter_mode == "missing":
            return not self.win.complete(sc)
        if self.filter_mode == "check":
            return needs_check(self.win, sc)
        return True

    def apply_filter(self):
        term = self.search.text().strip().casefold()
        shown = 0
        for row in range(self.list.count()):
            it = self.list.item(row)
            sc = self.win.sfile.shortcuts[it.data(ROLE_INDEX)]
            show = self.empty_reason is None and self.matches(sc)
            it.setHidden(not show)
            shown += show
        self._show_empty(term, shown)
        self.list.viewport().update()

    def _show_empty(self, term: str, shown: int):
        link = False
        if self.empty_reason == "nosteam":
            text = "Your games show here once Steam is found."
        elif self.empty_reason == "nogames" or not self.win.sfile.shortcuts:
            text = "No non-Steam games in Steam yet."
        elif shown:
            text = ""
        elif term:
            # Short here: the library says it in full, with its own Clear search, and the search
            # box has its own clear button.
            text = "No matches."
        elif self.filter_mode == "check":
            text = "Nothing needs a look. Every game was found and matched exactly."
        else:
            text = "No games match."
        self.empty_text.setText(text)
        self.clear_link.setVisible(link)
        self.empty.setVisible(bool(text))

    def select(self, idx: int):
        """Select a game's row; -1 clears the selection (the start page is showing)."""
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_INDEX) == idx:
                self.list.setCurrentRow(row)
                return
        self.list.setCurrentRow(-1)
        self.list.clearSelection()

    def refresh(self, spoken: bool = True):
        if spoken:
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
    GAP = 18  # between rows, and the least space between tiles
    MIN_W = 141 + 14

    def __init__(self, win, view):
        super().__init__()
        self.win = win
        self.view = view
        self.covers = ScaledCache()
        self.picks: dict = {}
        # Hover lift, as in Steam's library: the hovered cover rises and grows a little over
        # LIFT_MS, and settles back when the mouse leaves. lift[row] runs from 0 to 1.
        self.lift: dict[int, float] = {}
        self._hot = -1
        self._lift_timer = QTimer(self, interval=16, timeout=self._step_lift)

    LIFT_MS = 120

    def _hover(self, row: int, on: bool):
        """Called from paint: start lifting the row the mouse is on, settle the one it left."""
        if on and row != self._hot:
            self._hot = row
        elif not on and row == self._hot:
            self._hot = -1
        else:
            return
        if not self._lift_timer.isActive():
            self._lift_timer.start()

    def _step_lift(self):
        step = self._lift_timer.interval() / self.LIFT_MS
        rows = set(self.lift) | ({self._hot} if self._hot >= 0 else set())
        for row in rows:
            t = self.lift.get(row, 0.0)
            t = min(1.0, t + step) if row == self._hot else max(0.0, t - step)
            if t > 0:
                self.lift[row] = t
            else:
                self.lift.pop(row, None)
            item = self.view.item(row) if row < self.view.count() else None
            if item is not None:
                self.view.viewport().update(self.view.visualItemRect(item).adjusted(-10, -10, 10, 10))
        if all(r == self._hot and t == 1.0 for r, t in self.lift.items()) and (self._hot < 0 or self._hot in self.lift):
            self._lift_timer.stop()  # everything has risen or settled

    def tile_height(self, font: QFont) -> int:
        # Asked on every mouse move over the wall (hit testing): worked out once per font.
        key = (font.key(), theme.pt("control"), theme.pt("label"))
        cached = getattr(self, "_tile_h", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        h = self._tile_height(font)
        self._tile_h = (key, h)
        return h

    def _tile_height(self, font: QFont) -> int:
        f = QFont(font)
        f.setPointSizeF(theme.pt("control"))
        m = QFont(font)
        m.setPointSizeF(theme.pt("label"))
        return self.COVER.height() + 8 + QFontMetrics(f).height() + 4 + QFontMetrics(m).height() + 4 + self.GAP

    def sizeHint(self, option, index):
        # As wide as the wall's cell; hit_rect() keeps clicks and hover to the tile itself.
        return QSize(max(self.MIN_W, self.view.gridSize().width()), self.tile_height(option.font))

    def hit_rect(self, cell: QRect) -> QRect:
        """The cover, its name and its artwork line: the gaps around them are empty wall."""
        return QRect(self.tile_x(cell), cell.y() + 2, self.COVER.width(),
                     self.tile_height(self.view.font()) - self.GAP - 2)

    def tile_x(self, cell: QRect) -> int:
        """Where the cover sits in its cell: the spare width goes between the columns, so the first
        column starts at the wall's left edge (in line with the chips) and the last ends at its right."""
        spare = cell.width() - self.COVER.width()
        cols = max(1, getattr(self.view, "cols", 1))
        if cols == 1:
            return cell.x() + spare // 2
        col = min(cols - 1, max(0, round((cell.x() + self.view.horizontalOffset()) / max(1, cell.width()))))
        return cell.x() + round(spare * col / (cols - 1))

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        r = QRectF(self.tile_x(option.rect), option.rect.y() + 2, self.COVER.width(), self.COVER.height())
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focus = bool(option.state & QStyle.StateFlag.State_HasFocus) and theme.kbd_focus(self.view)
        pick = self.win.pending.get(sc.appid, "grid")
        new = pick is not None and not pick.clears
        self._hover(index.row(), hover)
        if not new and (pick is not None or self.win.cover_path(sc) is None):
            draw_dashed_empty(p, r, sc.name, plus=False, focus=hover or focus)
        else:
            p.save()
            t = self.lift.get(index.row(), 0.0)
            if t > 0:
                e = 1 - (1 - t) ** 3  # eases out, the way Steam's covers rise
                for i in range(4):  # a soft shadow under the raised cover
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(0, 0, 0, int(26 * e)))
                    p.drawRoundedRect(r.adjusted(-i, 2 + i * 1.5, i, 4 + i * 2.5), 4 + i, 4 + i)
                c = r.center()
                p.translate(c.x(), c.y() - 3 * e)
                p.scale(1 + 0.03 * e, 1 + 0.03 * e)
                p.translate(-c.x(), -c.y())
            clip = QPainterPath()
            clip.addRoundedRect(r, 3, 3)
            p.setClipPath(clip)
            pm = small_cover(self, sc, self.COVER, option.rect)
            if pm is None or pm.isNull():
                p.fillRect(r, q("raised"))  # decoding, or not an image
            else:
                p.drawPixmap(r.topLeft(), pm)
            if t > 0:
                p.fillRect(r, QColor(255, 255, 255, int(16 * e)))  # a touch brighter, as Steam does
            p.setClipping(False)
            if new:
                draw_tag(p, int(r.x()) + 8, int(r.y()) + 8, "", "New", NEW_BG, "#ffffff")
            if hover or focus:
                p.setPen(QPen(q("blue"), 2 if focus else 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            p.restore()
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        p.setFont(f)
        p.setPen(q("text"))
        lh = QFontMetrics(f).height()
        name = QFontMetrics(f).elidedText(sc.name, Qt.TextElideMode.ElideRight, int(r.width()))
        p.drawText(QRectF(r.left(), r.bottom() + 8, r.width(), lh),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        draw_meta(p, self.win, idx, r.left(), r.bottom() + 8 + lh + 4, option.font, "label", 6, 3, r.width())
        p.restore()


class Chip(QAbstractButton):
    """A pill: coverage dot, piece name, "13 / 18". Checked = the wall shows games missing it."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.have = 0
        self.total = 0
        self.setText(name)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)
        self._light = QFont(f)
        self._light.setWeight(QFont.Weight.Normal)

    @property
    def full(self) -> bool:
        return self.total > 0 and self.have >= self.total

    def set_values(self, have: int, total: int):
        if getattr(self, "_set", None) == (have, total):
            return  # unchanged: don't make the start page lay out again
        self._set = (have, total)
        self.have, self.total = have, total
        miss = total - have
        lower = self.name.lower()
        self.setToolTip("Every game has one" if self.full else
                        f"Show the {miss} game{'s' if miss != 1 else ''} missing {lower}")
        self.setAccessibleName(f"{self.name}, {have} of {total}")
        self.updateGeometry()
        self.update()

    def _parts(self):
        return f"{self.have} ", f"/ {self.total}"

    def sizeHint(self):
        fm, lm = QFontMetrics(self.font()), QFontMetrics(self._light)
        a, b = self._parts()
        w = 12 + 9 + 9 + fm.horizontalAdvance(self.name) + 9 + fm.horizontalAdvance(a) + lm.horizontalAdvance(b) + 16
        return QSize(w, max(40, fm.height() + 16))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2
        on = self.isChecked()
        hover = self.underMouse()
        fg = q("ground") if on else (q("text") if hover else q("text2"))
        p.setPen(QPen(q("text") if on else q("line"), 1))
        p.setBrush(q("text") if on else q("raised") if hover else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, rad, rad)
        if theme.kbd_focus(self):
            p.setPen(QPen(q("blue"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad - 1, rad - 1)
        cy = r.center().y()
        dot = QRectF(12.75, cy - 3.75, 7.5, 7.5)
        if self.full:
            p.setPen(QPen(q("ground") if on else q("blue_ink"), 1.5))
            p.setBrush(q("blue_ink"))
        else:
            p.setPen(QPen(q("ground") if on else q("text3"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(dot)
        fm = QFontMetrics(self.font())
        x = 12 + 9 + 9
        p.setFont(self.font())
        p.setPen(fg)
        p.drawText(QRectF(x, 0, 1000, self.height()), Qt.AlignmentFlag.AlignVCenter, self.name)
        x += fm.horizontalAdvance(self.name) + 9
        a, b = self._parts()
        p.setPen(q("ground") if on else q("text"))
        p.drawText(QRectF(x, 0, 1000, self.height()), Qt.AlignmentFlag.AlignVCenter, a)
        x += fm.horizontalAdvance(a)
        p.setFont(self._light)
        p.setPen(q("ground") if on else q("text3"))
        p.drawText(QRectF(x, 0, 1000, self.height()), Qt.AlignmentFlag.AlignVCenter, b)


class CoverAnimator(QObject):
    """Plays animated covers in the library, as Steam's library does.

    Only covers painted lately (on screen) play: each is decoded once, at the size it's
    drawn and within a small budget, one at a time in the background. A cover that stops
    being painted (scrolled away, another page shown) stops, and its frames are dropped
    once it has been out of sight for KEEP seconds, or sooner when the total passes CAP.
    """

    BUDGET = 6 * 1024 * 1024  # decoded frames for one cover
    CAP = 64 * 1024 * 1024  # all covers' frames together
    KEEP = 20.0

    def __init__(self, win, view):
        super().__init__(view)
        self.win, self.view = win, view
        self.animated: dict[tuple, bool] = {}  # (file, mtime) -> animated?
        self.anims: dict[tuple, dict] = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def frame(self, path, size: QSize, dpr: float, rect: QRect) -> QPixmap | None:
        """The frame due now for an animated cover drawn in `rect`; None for a still (or not decoded yet)."""
        if not self.win.show_animated:
            if self.anims:
                self.anims.clear()
            return None
        try:
            fk = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return None
        if fk not in self.animated:
            self.animated[fk] = file_is_animated(path)
        if not self.animated[fk]:
            return None
        key = fk + (size.width(), size.height(), dpr)
        now = time.monotonic()
        a = self.anims.get(key)
        if a is None:
            a = self.anims[key] = {"frames": None, "i": 0, "due": 0.0, "bytes": 0}
            bound = QSize(round(size.width() * dpr), round(size.height() * dpr))
            workers.run(motion.decode_frames, str(path), bound, budget=self.BUDGET,
                        on_done=lambda fr, key=key: self._decoded(key, fr, size, dpr),
                        on_error=lambda _m, key=key: self.anims.pop(key, None), pool=motion.decode_pool())
        a["seen"], a["rect"] = now, QRect(rect)
        if self.timer.interval() != 30 or not self.timer.isActive():
            self.timer.start(30)
        return a["frames"][a["i"]][0] if a["frames"] else None

    def _decoded(self, key, frames, size, dpr):
        a = self.anims.get(key)
        if a is None:  # dropped while it decoded
            return
        if len(frames) < 2:
            self.anims.pop(key, None)
            self.animated[key[:2]] = False
            return
        a["frames"] = [(fill_rect(QPixmap.fromImage(img), size, dpr), ms) for img, ms in frames]
        a["bytes"] = sum(img.sizeInBytes() for img, _ in frames)
        a["due"] = time.monotonic() + frames[0][1] / 1000
        self.view.viewport().update(a["rect"])

    def _on_screen(self, a, now) -> bool:
        # Every frame change repaints the cover, which marks it seen again; one that isn't
        # repainted within its frame's time and a little more is off screen.
        ms = a["frames"][a["i"]][1] if a["frames"] else 0
        return now - a["seen"] < ms / 1000 + 0.5

    def _tick(self):
        now = time.monotonic()
        playing = False
        for key, a in list(self.anims.items()):
            if now - a["seen"] > self.KEEP:
                del self.anims[key]
                continue
            if not self._on_screen(a, now):
                continue
            playing = True
            if a["frames"] and now >= a["due"]:
                a["i"] = (a["i"] + 1) % len(a["frames"])
                a["due"] = max(a["due"] + a["frames"][a["i"]][1] / 1000, now)
                self.view.viewport().update(a["rect"])
        total = sum(a["bytes"] for a in self.anims.values())
        if total > self.CAP:  # drop the covers seen longest ago until back under
            for key, a in sorted(self.anims.items(), key=lambda kv: kv[1]["seen"]):
                if total <= self.CAP or self._on_screen(a, now):
                    break
                total -= a["bytes"]
                del self.anims[key]
        if not self.anims:
            self.timer.stop()
        elif not playing:
            self.timer.start(1000)  # nothing on screen: only check now and then for frames to drop


def small_cover(delegate, sc, size: QSize, rect: QRect | None = None) -> QPixmap | None:
    """The game's cover (or its waiting pick) cropped to `size`; None if it has none or it's decoding."""
    win, view = delegate.win, delegate.view
    dpr = view.devicePixelRatioF()
    pick = win.pending.get(sc.appid, "grid")
    if pick is not None:
        if pick.clears:
            return None
        pm = win.pick_pixmap(sc, "grid")
        if pm is None or pm.isNull():
            return None
        key = (pm.cacheKey(), size.width(), size.height(), dpr)
        out = delegate.picks.get(key)
        if out is None:
            if len(delegate.picks) > 200:
                delegate.picks.clear()
            out = delegate.picks[key] = fill_rect(pm, size, dpr)
        return out
    path = win.cover_path(sc)
    if path is None:
        return None
    anim = getattr(view, "anim", None)
    if anim is not None and rect is not None:
        pm = anim.frame(path, size, dpr, rect)
        if pm is not None:
            return pm
    return delegate.covers.get(path, size, view.viewport().update, dpr)


def draw_small_cover(p: QPainter, r: QRectF, pm: QPixmap | None, name: str):
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(r, 2, 2)
    p.setClipPath(clip)
    if pm is not None and not pm.isNull():
        p.drawPixmap(r.topLeft(), pm)
    else:
        p.fillRect(r, q("raised"))
        p.setPen(q("text3"))
        f = QFont(p.font())
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, initials(name)[:1])
    p.restore()


def draw_hover(p: QPainter, view, option, r: QRectF, fill: bool):
    """Row hover and keyboard focus, the same in the list and details views."""
    hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
    focus = bool(option.state & QStyle.StateFlag.State_HasFocus) and theme.kbd_focus(view)
    if fill or hover:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(q("raised") if hover else q("panel"))
        p.drawRoundedRect(r, 3, 3)
    if focus:
        p.setPen(QPen(q("blue"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 3, 3)


class ListDelegate(QStyledItemDelegate):
    """List view: one wide row per game, with its cover, name and artwork dots."""

    COVER = QSize(34, 51)

    def __init__(self, win, view):
        super().__init__()
        self.win, self.view = win, view
        self.covers = ScaledCache()
        self.picks: dict = {}

    def sizeHint(self, option, index):
        return QSize(300, self.COVER.height() + 14)

    def hit_rect(self, cell: QRect) -> QRect:
        return cell.adjusted(0, 2, -8, -2)  # the row as painted, without the gaps between rows

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(0, 2, -8, -2)
        draw_hover(p, self.view, option, r, True)
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        cv = QRectF(r.x() + 8, r.center().y() - self.COVER.height() / 2, self.COVER.width(), self.COVER.height())
        draw_small_cover(p, cv, small_cover(self, sc, self.COVER, option.rect), sc.name)
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(q("text"))
        meta_x = r.right() - 190
        tx = cv.right() + 14
        fm = QFontMetrics(f)
        name = fm.elidedText(sc.name or "(unnamed)", Qt.TextElideMode.ElideRight, int(meta_x - tx - 12))
        p.drawText(QRectF(tx, r.y(), meta_x - tx, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        # Then, in the room the sidebar doesn't have: what's waiting, else the SteamGridDB game it matched.
        ex = tx + fm.horizontalAdvance(name) + 18
        room = meta_x - ex - 16
        extra, colour = list_extra(self.win, sc)
        if extra and room > 60:
            ef = QFont(option.font)
            ef.setPointSizeF(theme.pt("faint"))
            p.setFont(ef)
            p.setPen(q(colour))
            p.drawText(QRectF(ex, r.y(), room, r.height()), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(ef).elidedText(extra, Qt.TextElideMode.ElideRight, int(room)))
        mf = QFont(option.font)
        mf.setPointSizeF(theme.pt("label"))
        mh = QFontMetrics(mf).height()
        draw_meta(p, self.win, idx, meta_x, r.center().y() - mh / 2, option.font, "label", 7, 3)
        p.restore()


DETAIL_COLS = ("Cover", "Wide cover", "Banner", "Logo", "Icon")
DETAIL_TIPS = ("Cover", "Wide cover", "Banner", "Logo", "Icon")
DETAIL_W, STATUS_W = 64, 130
NARROW_W = 40  # piece columns when the view is narrow
NAME_X = 8 + 22 + 12  # where the name column starts: after the row's small cover
NAME_MIN = 140


def detail_columns(width: float) -> tuple[float, list[float], float | None, float]:
    """x of the name column's end, the centre of each piece column, the status column's x
    (None when it doesn't fit: the status goes in the row's tooltip) and a piece column's width."""
    col, status_x = DETAIL_W, width - STATUS_W - 8
    if status_x - DETAIL_W * len(DETAIL_COLS) < NAME_X + NAME_MIN:  # too narrow for everything
        col, status_x = NARROW_W, None
    first = (width - 8 if status_x is None else status_x) - col * len(DETAIL_COLS)
    first = max(first, NAME_X + 40.0)  # past that the columns run off the right instead of over the covers
    return first, [first + col * (i + 0.5) for i in range(len(DETAIL_COLS))], status_x, col


def detail_status(win, idx: int) -> tuple[str, QColor]:
    sc = win.sfile.shortcuts[idx]
    tag = win.status.get(sc.appid)
    if tag in TAGS:
        return TAGS[tag][0], QColor(TAGS[tag][2])
    if is_ready(win, idx):
        return "Ready to apply", q("blue_ink")
    return ("All artwork", q("text2")) if win.complete(sc) else ("Missing artwork", q("text3"))


class DetailsDelegate(QStyledItemDelegate):
    """Details view: a table of which pieces each game has."""

    COVER = QSize(22, 33)

    def __init__(self, win, view):
        super().__init__()
        self.win, self.view = win, view
        self.covers = ScaledCache()
        self.picks: dict = {}

    def sizeHint(self, option, index):
        return QSize(300, 42)

    def hit_rect(self, cell: QRect) -> QRect:
        return cell.adjusted(0, 1, -8, -1)

    def helpEvent(self, event, view, option, index):
        """Narrow, the status column is gone: the tooltip says it instead."""
        if event.type() == event.Type.ToolTip and detail_columns(option.rect.width() - 8)[2] is None:
            idx = index.data(ROLE_INDEX)
            if idx is not None and idx < len(self.win.sfile.shortcuts):
                name = self.win.sfile.shortcuts[idx].name or "(unnamed)"
                QToolTip.showText(event.globalPos(), f"{name}\n{detail_status(self.win, idx)[0]}", view)
                return True
        return super().helpEvent(event, view, option, index)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(0, 0, -8, 0)
        draw_hover(p, self.view, option, r.adjusted(0, 1, 0, -1), False)
        p.setPen(QPen(q("line"), 1))
        p.drawLine(QPointF(r.left() + 4, r.bottom() + 0.5), QPointF(r.right() - 4, r.bottom() + 0.5))
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        cv = QRectF(r.x() + 8, r.center().y() - self.COVER.height() / 2, self.COVER.width(), self.COVER.height())
        draw_small_cover(p, cv, small_cover(self, sc, self.COVER, option.rect), sc.name)
        name_end, centres, status_x, _col = detail_columns(r.width())
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(q("text"))
        tx = cv.right() + 12
        name = QFontMetrics(f).elidedText(sc.name or "(unnamed)", Qt.TextElideMode.ElideRight, int(name_end - tx - 12))
        p.drawText(QRectF(tx, r.y(), name_end - tx, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        cy = r.center().y()
        for have, x in zip(self.win.art_states(sc), centres):
            if have == "none":  # SteamGridDB has none: nothing to get
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(q("text3"))
                p.drawEllipse(QPointF(r.x() + x, cy), 2.0, 2.0)
            elif have:
                icons.draw(p, "check", QRectF(r.x() + x - 8, cy - 8, 16, 16), q("ok"))
            else:
                p.setPen(QPen(q("text3"), 1.5))
                p.drawLine(QPointF(r.x() + x - 4, cy), QPointF(r.x() + x + 4, cy))
        if status_x is not None:
            text, colour = detail_status(self.win, idx)
            sf = QFont(option.font)
            sf.setPointSizeF(theme.pt("faint"))
            p.setFont(sf)
            p.setPen(colour)
            p.drawText(QRectF(r.x() + status_x, r.y(), STATUS_W, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()


class DetailsHeader(QWidget):
    """Column names over the details view, lined up with its rows."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.setFixedHeight(34)  # room for a two-line column name

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:  # the column names are short; say the piece in full
            x0 = self.view.viewport().x() + self.view.x() - self.x()
            _end, centres, _status, col = detail_columns(self.view.viewport().width() - 8)
            for name, x in zip(DETAIL_TIPS, centres):
                if abs(e.pos().x() - (x0 + x)) <= col / 2:
                    QToolTip.showText(e.globalPos(), name, self)
                    return True
            QToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def paintEvent(self, _):
        p = QPainter(self)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("label"))
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        p.setFont(f)
        p.setPen(q("text3"))
        x0 = self.view.viewport().x() + self.view.x() - self.x()
        width = self.view.viewport().width() - 8
        name_end, centres, status_x, col = detail_columns(width)
        h = self.height()
        fm = QFontMetrics(f)
        p.drawText(QRectF(x0 + NAME_X, 0, max(0.0, name_end - NAME_X - 4), h), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText("NAME", Qt.TextElideMode.ElideRight, int(max(0.0, name_end - NAME_X - 4))))
        for label, x in zip(DETAIL_COLS, centres):
            text = label.upper()
            if " " in text and fm.horizontalAdvance(text) > col - 6:
                # Two words wider than the column ("WIDE COVER") go on two lines, so they never run
                # into the next column's name.
                p.drawText(QRectF(x0 + x - col / 2, 0, col, h), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
                           | Qt.TextFlag.TextDontClip, text)
                continue
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(col) + 12)  # may overhang a little
            p.drawText(QRectF(x0 + x - col / 2, 0, col, h), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip, text)
        if status_x is not None:
            p.drawText(QRectF(x0 + status_x, 0, STATUS_W, h), Qt.AlignmentFlag.AlignVCenter, "STATUS")
        p.setPen(QPen(q("line"), 1))
        p.drawLine(QPointF(x0 + 4, h - 0.5), QPointF(x0 + width - 4, h - 0.5))


class CoverWall(QListWidget):
    """The library: a cover grid whose columns stretch to use the whole width, or a list or details table."""

    menu_requested = Signal(int, QPoint)
    GUTTER = 14  # between the covers and the scrollbar, which sits at the page's right edge

    def __init__(self, delegate_cls, win):
        super().__init__()
        self.setViewportMargins(0, 0, self.GUTTER, 0)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setAccessibleName("Cover wall")
        self.delegate = delegate_cls(win, self)
        self.delegates = {"grid": self.delegate, "list": ListDelegate(win, self), "details": DetailsDelegate(win, self)}
        self.view = ""
        self.cols = 1  # grid columns, set by fit()
        self.anim = CoverAnimator(win, self)
        self.set_view("grid")

    def set_view(self, view: str):
        """"grid", "list" or "details"."""
        if view == self.view:
            return
        self.view = view
        grid = view == "grid"
        self.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setItemDelegate(self.delegates[view])
        self.setAccessibleName({"grid": "Cover wall", "list": "Game list", "details": "Games and their artwork"}[view])
        if grid:
            self.fit()
        else:
            self.setGridSize(QSize())
        self.scrollToTop()

    def fit(self):
        """Pick the column count that fits and share the spare width between the columns."""
        if self.view != "grid":
            return
        avail = self.width() - 2 * self.frameWidth() - self.verticalScrollBar().sizeHint().width() - self.GUTTER - 1
        cols = max(1, avail // self.delegate.MIN_W)
        self.cols = cols
        size = QSize(max(self.delegate.MIN_W, avail // cols), self.delegate.tile_height(self.font()))
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, e):
        self.fit()
        super().resizeEvent(e)

    def indexAt(self, pos: QPoint) -> QModelIndex:
        """Only a game's tile or row counts: clicks, hover, tooltips and menus in the gaps find nothing."""
        index = super().indexAt(pos)
        if index.isValid() and not self.delegates[self.view].hit_rect(self.visualRect(index)).contains(pos):
            return QModelIndex()
        return index

    def contextMenuEvent(self, e):
        if e.reason() == e.Reason.Keyboard:
            it = self.currentItem()
            if it is None or it.isHidden():
                return
            rect = self.visualItemRect(it)
            pos = self.viewport().mapToGlobal(QPoint(rect.center().x(), rect.top() + 60))
        else:
            it = self.itemAt(e.pos())
            if it is None:
                return
            pos = e.globalPos()
        self.menu_requested.emit(it.data(ROLE_INDEX), pos)


RIGHT = 24  # the chips' right margin: in line with the covers' right edge (scrollbar + gutter)


class StartPage(QWidget):
    picked = Signal(int)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.piece: int | None = None  # chip that is on: index into PIECES / ORDER
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 0, 0)  # the wall's scrollbar runs down the right edge
        lay.setSpacing(0)
        chips = QWidget()
        self.chip_flow = FlowLayout(chips, spacing=8)
        self.chip_flow.setContentsMargins(0, 0, RIGHT, 0)
        self.chips: list[Chip] = []
        for i, name in enumerate(PIECES):
            c = Chip(name)
            c.clicked.connect(lambda _=False, i=i: self.toggle_piece(i))
            self.chips.append(c)
            self.chip_flow.addWidget(c)
        lay.addWidget(chips)
        lay.addSpacing(12)
        self.note = QWidget()
        nl = QHBoxLayout(self.note)
        nl.setContentsMargins(0, 0, RIGHT, 4)
        nl.setSpacing(4)
        self.note_text = QLabel()
        self.note_text.setStyleSheet(f"color: {C['text2']};")
        nl.addWidget(self.note_text)
        self.show_all = quiet_button("Show all", lambda: self.set_piece(None))
        nl.addWidget(self.show_all)
        nl.addStretch(1)
        self.note.hide()
        lay.addWidget(self.note)
        lay.addSpacing(10)
        self.empty = QWidget()  # when the sidebar's search or filter (and a chip) leave no games
        el = QHBoxLayout(self.empty)
        el.setContentsMargins(0, 6, RIGHT, 0)
        el.setSpacing(4)
        self.empty_text = empty_text = QLabel("No games match this filter.")
        empty_text.setStyleSheet(f"color: {C['text2']};")
        el.addWidget(empty_text)
        self.empty_button = quiet_button("Show all games", self._show_everything)
        el.addWidget(self.empty_button)
        el.addStretch(1)
        self.empty.hide()
        lay.addWidget(self.empty)
        self.wall = CoverWall(WallDelegate, win)
        self.delegate = self.wall.delegate
        self.wall.itemClicked.connect(lambda it: self.picked.emit(it.data(ROLE_INDEX)))
        self.wall.itemActivated.connect(lambda it: self.picked.emit(it.data(ROLE_INDEX)))
        self.wall.menu_requested.connect(lambda i, pos: self.win.show_game_menu(i, pos))
        self.header = DetailsHeader(self.wall)
        self.header.hide()
        lay.addWidget(self.header)
        lay.addWidget(self.wall, 1)

    def set_view(self, view: str):
        self.wall.set_view(view)
        self.header.setVisible(view == "details" and self.empty.isHidden())

    def _show_everything(self):
        self.win.game_list.search.clear()
        self.win.game_list.set_filter("all")
        self.set_piece(None)

    def rebuild(self):
        bar = self.wall.verticalScrollBar()
        v = bar.value()  # a re-sort after Apply keeps the place (choosing a sort scrolls up itself)
        self.wall.clear()
        scs = self.win.sfile.shortcuts
        for i in sorted_indices(self.win, self.win.library_sort):
            sc = scs[i]
            it = QListWidgetItem(sc.name)
            it.setData(ROLE_INDEX, i)
            it.setToolTip(sc.name)
            self.wall.addItem(it)
        self.refresh()
        QTimer.singleShot(0, lambda: bar.setValue(min(v, bar.maximum())))

    def order(self) -> list[int]:
        return [self.wall.item(row).data(ROLE_INDEX) for row in range(self.wall.count())]

    def toggle_piece(self, i: int):
        if self.chips[i].full and self.piece != i:
            self.chips[i].setChecked(False)  # nothing is missing: nothing to show
            return
        self.set_piece(None if self.piece == i else i)

    def set_piece(self, i: int | None):
        """Show only the games missing piece `i` (0 covers .. 4 icons), or everything (None)."""
        self.piece = i
        self.refresh()

    def refresh(self):
        win = self.win
        n = len(win.sfile.shortcuts)
        cov = [win.coverage(sc) for sc in win.sfile.shortcuts]
        counts = [sum(c[i] for c in cov) for i in range(5)]
        if self.piece is not None and counts[self.piece] >= n:
            self.piece = None  # every game has it now
        for i, chip in enumerate(self.chips):
            chip.set_values(counts[i], n)
            chip.setChecked(self.piece == i)
        shown = 0
        for row in range(self.wall.count()):
            it = self.wall.item(row)
            idx = it.data(ROLE_INDEX)
            if idx >= n:
                continue
            set_spoken(it, spoken(win, idx))
            show = (self.piece is None or not cov[idx][self.piece]) and win.game_list.matches(win.sfile.shortcuts[idx])
            it.setHidden(not show)
            shown += show
        if n > 0 and not shown:  # say it the way the sidebar does: a search isn't a filter
            term = win.game_list.search.text().strip()
            self.empty_text.setText(f"No games match “{term}”." if term else "No games match this filter.")
            self.empty_button.setText("Clear search" if term else "Show all games")
        self.empty.setVisible(n > 0 and not shown)
        self.header.setVisible(self.wall.view == "details" and shown > 0)
        if self.piece is None:
            self.note.hide()
        else:
            self.note_text.setText(f"Showing {shown} game{'s' if shown != 1 else ''} missing "
                                   f"{PIECES[self.piece].lower()}")
            self.note.show()
        self.wall.viewport().update()
