"""The game list on the left and the start page's cover wall."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
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
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from ..artwork import SLOTS
from .theme import C, q
from .widgets import FlowLayout, ScaledCache, draw_dashed_empty, draw_dots, fill_rect, quiet_button

ROLE_INDEX = Qt.ItemDataRole.UserRole  # index into the shortcut list
ICON = QSize(32, 32)
NEW_BG = "#1474c8"

# key, name for the tooltip, text in the menu
FILTERS = (("all", "All Games", "All"), ("missing", "Missing Art", "Missing Art"), ("check", "To Check", "To Check"))
PIECES = ("Covers", "Wide covers", "Banners", "Logos", "Icons")


def draw_all_games_icon(p: QPainter, rect: QRect, color: QColor) -> None:
    """The shelf icon (the filter button), in the given colour (rendered once per colour)."""
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


def is_ready(win, idx: int) -> bool:
    """The game has picks waiting for Apply."""
    return bool(win.pending.count(win.sfile.shortcuts[idx].appid))


def needs_check(win, sc) -> bool:
    return win.status.get(sc.appid) in ("notfound", "check")


def spoken(win, idx: int) -> str:
    """What a screen reader says for a game row or tile."""
    sc = win.sfile.shortcuts[idx]
    text, tag = status_text(win, idx)
    parts = [sc.name or "unnamed game", text if text == "All artwork" else f"{text} artwork"]
    if tag:
        parts.append(TAGS[tag][0])
    if is_ready(win, idx):
        parts.append("ready to apply")
    return ", ".join(parts)


TAGS = {
    "notfound": ("Not found", "#3b2422", C["bad"]),
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


def draw_meta(p: QPainter, win, idx: int, x: float, y: float, base: QFont, size: str, d: float, gap: float):
    """Coverage dots, then CHECK MATCH-style tag, else "Ready to apply", else "n of 5"."""
    sc = win.sfile.shortcuts[idx]
    f = QFont(base)
    f.setPointSizeF(theme.pt(size))
    lh = QFontMetrics(f).height()
    x = draw_dots(p, x, y + (lh - d) / 2, win.coverage(sc), d=d, gap=gap) + 3
    text, tag = status_text(win, idx)
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
        if option.state & QStyle.StateFlag.State_HasFocus and self.view.hasFocus() and not selected:
            p.setPen(QPen(q("blue"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        icon_rect = QRect(r.x() + 8, r.y() + (r.height() - ICON.height()) // 2, ICON.width(), ICON.height())
        ipath = self.win.slot_path(sc, SLOTS["icon"])
        icon = self.icon_cache.get(ipath, ICON, self.view.viewport().update, self.view.devicePixelRatioF()) if ipath else QPixmap()
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
    """The shelf button inside the search box; blue while a filter is on."""

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
        if enabled and (self.open or hover):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("select"))
            p.drawRoundedRect(r, 3, 3)
        if self.hasFocus() and not self.open:
            p.setPen(QPen(q("blue"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        elif self.on and enabled:
            p.setPen(QPen(q("blue_ink"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        if not enabled:
            color = q("text2")  # the search box fades the whole row
        elif self.open:
            color = QColor("white")
        elif self.on:
            color = q("blue_ink")
        else:
            color = q("text") if hover else q("text2")
        draw_all_games_icon(p, QRect((self.width() - 22) // 2, (self.height() - 22) // 2, 22, 22), color)


class FilterRow(QAbstractButton):
    """One radio row of the filter menu: ring, text, count on the right."""

    def __init__(self, key: str, text: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.count = 0
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("control"))
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)

    def set_count(self, n: int):
        self.count = n
        self.setAccessibleName(f"{self.text()}, {n}")
        self.update()

    def sizeHint(self):
        return QSize(222, max(44, self.fontMetrics().height() + 20))

    def keyPressEvent(self, e):
        self.parent().keyPressEvent(e)  # the menu handles arrows, Enter and Esc

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


class FilterMenu(QFrame):
    """The popup under the filter button: "SHOW" and three radio rows."""

    chosen = Signal(str)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)  # clicking the button again just closes it
        self.setAccessibleName("Show games")
        self.setFixedWidth(232)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(0)
        head = QLabel("SHOW")
        f = QFont(head.font())
        f.setPointSizeF(theme.pt("label"))
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        head.setFont(f)
        head.setStyleSheet(f"color: {C['text3']}; background: transparent;")
        head.setContentsMargins(12, 8, 12, 6)
        lay.addWidget(head)
        self.rows: dict[str, FilterRow] = {}
        for key, _tip, text in FILTERS:
            row = FilterRow(key, text, self)
            row.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.rows[key] = row
            lay.addWidget(row)

    def set_mode(self, mode: str):
        for key, row in self.rows.items():
            row.setChecked(key == mode)

    def popup_at(self, top_right: QPoint):
        self.adjustSize()
        self.move(top_right.x() - self.width(), top_right.y())
        self.show()
        next((r for r in self.rows.values() if r.isChecked()), self.rows["all"]).setFocus()

    def _pick(self, key: str):
        self.set_mode(key)
        self.close()
        self.chosen.emit(key)

    def keyPressEvent(self, e):
        rows = list(self.rows.values())
        cur = next((i for i, r in enumerate(rows) if r.hasFocus()), 0)
        k = e.key()
        if k in (Qt.Key.Key_Down, Qt.Key.Key_Tab):
            rows[(cur + 1) % len(rows)].setFocus()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Backtab):
            rows[(cur - 1) % len(rows)].setFocus()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._pick(rows[cur].key)
        elif k == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.closed.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), q("ground"))
        p.setPen(q("line"))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))


class SearchBox(QFrame):
    """The search field with the filter button inside it on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("searchbox")
        self.setFixedHeight(40)
        self.setStyleSheet(f"""
            #searchbox {{ background: {C['input']}; border: 1px solid transparent; border-radius: 3px; }}
            #searchbox[focus="true"] {{ border-color: {C['blue']}; }}
            #searchbox QLineEdit {{ background: transparent; border: none; min-height: 0; padding: 0 0 0 9px; }}
            #searchbox QLineEdit:disabled {{ color: {C['text3']}; }}
            #searchbox QToolButton {{ min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; }}
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
        cur = self.current_index()
        self.list.blockSignals(True)
        self.list.clear()
        for i, sc in enumerate(self.win.sfile.shortcuts):
            it = QListWidgetItem(sc.name)  # text is read by screen readers; the delegate paints
            it.setData(ROLE_INDEX, i)
            it.setToolTip(sc.name)
            self.list.addItem(it)
        self.select(cur if cur is not None else -1)
        self.list.blockSignals(False)
        self.update_counts()

    def refresh_spoken(self):
        n = len(self.win.sfile.shortcuts)
        for row in range(self.list.count()):
            it = self.list.item(row)
            if it.data(ROLE_INDEX) < n:
                it.setData(Qt.ItemDataRole.AccessibleTextRole, spoken(self.win, it.data(ROLE_INDEX)))

    def counts(self) -> dict[str, int]:
        scs = self.win.sfile.shortcuts
        return {
            "all": len(scs),
            "missing": sum(1 for sc in scs if not all(self.win.coverage(sc))),
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

    def apply_filter(self):
        term = self.search.text().strip().casefold()
        mode = self.filter_mode
        shown = 0
        for row in range(self.list.count()):
            it = self.list.item(row)
            sc = self.win.sfile.shortcuts[it.data(ROLE_INDEX)]
            show = self.empty_reason is None and (not term or term in sc.name.casefold())
            if mode == "missing":
                show = show and not all(self.win.coverage(sc))
            elif mode == "check":
                show = show and needs_check(self.win, sc)
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
            text, link = f"No games match “{self.search.text().strip()}”.", True
        elif self.filter_mode == "check":
            text = "Nothing to check. Every match was exact."
        else:
            text = "No games match."
        self.empty_text.setText(text)
        self.clear_link.setVisible(link)
        self.empty.setVisible(bool(text))

    def current_index(self) -> int | None:
        it = self.list.currentItem()
        return it.data(ROLE_INDEX) if it else None

    def select(self, idx: int):
        """Select a game's row; -1 clears the selection (the start page is showing)."""
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_INDEX) == idx:
                self.list.setCurrentRow(row)
                return
        self.list.setCurrentRow(-1)
        self.list.clearSelection()

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
    GAP = 18  # between rows, and the least space between tiles
    MIN_W = 141 + 14

    def __init__(self, win, view):
        super().__init__()
        self.win = win
        self.view = view
        self.covers = ScaledCache()
        self._picks: dict[int, QPixmap] = {}

    def tile_height(self, font: QFont) -> int:
        f = QFont(font)
        f.setPointSizeF(theme.pt("control"))
        m = QFont(font)
        m.setPointSizeF(theme.pt("label"))
        return self.COVER.height() + 8 + QFontMetrics(f).height() + 4 + QFontMetrics(m).height() + 4 + self.GAP

    def sizeHint(self, option, index):
        return QSize(self.MIN_W, self.tile_height(option.font))

    def pick_cover(self, pm: QPixmap) -> QPixmap:
        key = pm.cacheKey()
        out = self._picks.get(key)
        if out is None:
            if len(self._picks) > 200:
                self._picks.clear()
            out = self._picks[key] = fill_rect(pm, self.COVER, self.view.devicePixelRatioF())
        return out

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        idx = index.data(ROLE_INDEX)
        sc = self.win.sfile.shortcuts[idx]
        x = option.rect.x() + (option.rect.width() - self.COVER.width()) // 2
        r = QRectF(x, option.rect.y() + 2, self.COVER.width(), self.COVER.height())
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focus = bool(option.state & QStyle.StateFlag.State_HasFocus) and self.view.hasFocus()
        pick = self.win.pending.get(sc.appid, "grid")
        new = pick is not None and not pick.clears
        path = None if pick is not None else self.win.cover_path(sc)
        if not new and path is None:
            draw_dashed_empty(p, r, sc.name, plus=False, focus=hover or focus)
        else:
            clip = QPainterPath()
            clip.addRoundedRect(r, 3, 3)
            p.setClipPath(clip)
            if new:
                ppm = self.win.pick_pixmap(sc, "grid")
                pm = self.pick_cover(ppm) if ppm is not None and not ppm.isNull() else None
            else:
                pm = self.covers.get(path, self.COVER, self.view.viewport().update, self.view.devicePixelRatioF())
            if pm is None or pm.isNull():
                p.fillRect(r, q("raised"))  # decoding, or not an image
            else:
                p.drawPixmap(r.topLeft(), pm)
            p.setClipping(False)
            if new:
                draw_tag(p, int(r.x()) + 8, int(r.y()) + 8, "", "New", NEW_BG, "#ffffff")
            if hover or focus:
                p.setPen(QPen(q("blue"), 2 if focus else 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("control"))
        p.setFont(f)
        p.setPen(q("text"))
        lh = QFontMetrics(f).height()
        name = QFontMetrics(f).elidedText(sc.name, Qt.TextElideMode.ElideRight, int(r.width()))
        p.drawText(QRectF(r.left(), r.bottom() + 8, r.width(), lh),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        draw_meta(p, self.win, idx, r.left(), r.bottom() + 8 + lh + 4, option.font, "label", 6, 3)
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
        if self.hasFocus():
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


class CoverWall(QListWidget):
    """Icon-mode list whose columns stretch to use the whole width."""

    menu_requested = Signal(int, QPoint)

    def __init__(self, delegate_cls, win):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setAccessibleName("Cover wall")
        self.delegate = delegate_cls(win, self)
        self.setItemDelegate(self.delegate)
        self.fit()

    def fit(self):
        """Pick the column count that fits and share the spare width between the columns."""
        avail = self.width() - 2 * self.frameWidth() - self.verticalScrollBar().sizeHint().width() - 1
        cols = max(1, avail // self.delegate.MIN_W)
        size = QSize(max(self.delegate.MIN_W, avail // cols), self.delegate.tile_height(self.font()))
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, e):
        self.fit()
        super().resizeEvent(e)

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


class StartPage(QWidget):
    picked = Signal(int)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.piece: int | None = None  # chip that is on: index into PIECES / ORDER
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 16, 0)
        lay.setSpacing(0)
        chips = QWidget()
        self.chip_flow = FlowLayout(chips, spacing=8)
        self.chip_flow.setContentsMargins(0, 0, 0, 0)
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
        nl.setContentsMargins(0, 0, 0, 4)
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
        self.wall = CoverWall(WallDelegate, win)
        self.delegate = self.wall.delegate
        self.wall.itemClicked.connect(lambda it: self.picked.emit(it.data(ROLE_INDEX)))
        self.wall.itemActivated.connect(lambda it: self.picked.emit(it.data(ROLE_INDEX)))
        self.wall.menu_requested.connect(lambda i, pos: self.win.show_game_menu(i, pos))
        lay.addWidget(self.wall, 1)

    def rebuild(self):
        self.wall.clear()
        for i, sc in enumerate(self.win.sfile.shortcuts):
            it = QListWidgetItem(sc.name)
            it.setData(ROLE_INDEX, i)
            it.setToolTip(sc.name)
            self.wall.addItem(it)
        self.refresh()

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
            it.setData(Qt.ItemDataRole.AccessibleTextRole, spoken(win, idx))
            show = self.piece is None or not cov[idx][self.piece]
            it.setHidden(not show)
            shown += show
        if self.piece is None:
            self.note.hide()
        else:
            self.note_text.setText(f"Showing {shown} game{'s' if shown != 1 else ''} missing "
                                   f"{PIECES[self.piece].lower()}")
            self.note.show()
        self.wall.viewport().update()
