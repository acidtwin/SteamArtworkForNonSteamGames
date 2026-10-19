"""The option tray: every SteamGridDB option for one piece, beside the game page."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..artwork import SLOTS
from . import icons, theme
from .theme import q
from .gamepage import draw_new_tag
from .widgets import FlowLayout, draw_animated_tag, draw_badge, draw_checker, fill_rect, label

ORDER = ("grid", "wide", "hero", "logo", "icon")
ROLE_ASSET = Qt.ItemDataRole.UserRole
ROLE_PIX = Qt.ItemDataRole.UserRole + 1
ROLE_INUSE = Qt.ItemDataRole.UserRole + 2
ROLE_PICK = Qt.ItemDataRole.UserRole + 3  # the waiting (not applied) pick
ROLE_NAME = Qt.ItemDataRole.UserRole + 4  # what a screen reader says, before "picked" / "in use"


def thumb_size(slot_key: str) -> QSize:
    return {
        "grid": QSize(100, 150),
        "wide": QSize(150, 70),
        "hero": QSize(310, 100),
        "logo": QSize(150, 70),
        "icon": QSize(64, 64),
    }[slot_key]


class OptionDelegate(QStyledItemDelegate):
    def __init__(self, tray):
        super().__init__()
        self.tray = tray
        self._scaled: dict[tuple, QPixmap] = {}  # thumbnails scaled for the tray, so a repaint only draws

    def scaled(self, pm: QPixmap, s: QSize, logo: bool, dpr: float) -> QPixmap:
        key = (pm.cacheKey(), s.width(), s.height(), logo, dpr)
        out = self._scaled.get(key)
        if out is None:
            if len(self._scaled) > 300:
                self._scaled.clear()
            if logo:  # logos keep their shape, with a margin
                out = pm.scaled(s * 0.86 * dpr, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
                out.setDevicePixelRatio(dpr)
            else:
                out = fill_rect(pm, s, dpr)
            self._scaled[key] = out
        return out

    def sizeHint(self, option, index):
        s = thumb_size(self.tray.slot.key)
        return QSize(s.width() + 8, s.height() + QFontMetrics(option.font).height() + 12)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = thumb_size(self.tray.slot.key)
        r = QRectF(option.rect.x() + 4, option.rect.y() + 4, s.width(), s.height())
        clip = QPainterPath()
        clip.addRoundedRect(r, 3, 3)
        p.setClipPath(clip)
        pm = index.data(ROLE_PIX)
        logo = self.tray.slot.key == "logo"
        if logo:
            draw_checker(p, r)
        else:
            p.fillRect(r, q("raised"))
        if pm is not None and not pm.isNull():
            dpr = p.device().devicePixelRatioF()
            sp = self.scaled(pm, s, logo, dpr)
            if logo:
                w, h = sp.width() / dpr, sp.height() / dpr
                p.drawPixmap(QPointF(r.x() + (s.width() - w) / 2, r.y() + (s.height() - h) / 2), sp)
            else:
                p.drawPixmap(r.topLeft(), sp)
        p.setClipping(False)
        picked = bool(index.data(ROLE_PICK))
        if picked:
            draw_new_tag(p, r)
        elif index.data(ROLE_INUSE):
            draw_badge(p, r)
        a = index.data(ROLE_ASSET)
        if a is not None and a.animated:
            draw_animated_tag(p, r)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if sel or hov or picked:
            p.setPen(QPen(q("blue") if sel or picked else q("text3"), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        a = index.data(ROLE_ASSET)
        f = QFont(option.font)
        f.setPointSizeF(theme.pt("label"))
        p.setFont(f)
        p.setPen(q("text3"))
        cap = f"by {a.author or 'unknown'}" + (f" · {a.style}" if a.style else "")
        cap = QFontMetrics(f).elidedText(cap, Qt.TextElideMode.ElideRight, s.width())
        p.drawText(QRectF(r.left(), r.bottom() + 4, s.width(), QFontMetrics(f).height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cap)
        p.restore()


class OptionTray(QWidget):
    closed = Signal()
    use = Signal(object, object)  # slot, Asset (no longer emitted: picks stage through win.stage_pick)
    remove = Signal(object)  # slot (no longer emitted: clearing stages through win.stage_clear)
    preview = Signal(object, object, object)  # slot, QPixmap or None, Asset or None
    slot_changed = Signal(object)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.slot = SLOTS["grid"]
        self.setObjectName("tray")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        # Wide enough for the five tabs in a row at the user's font size.
        self.setFixedWidth(max(370, theme.text_width("Cover Wide cover Banner Logo Icon") + 180))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.title = label("", "heading")
        head.addWidget(self.title, 1)
        x = QToolButton()
        x.setIcon(icons.icon("close", 18, theme.C["text2"]))
        x.setIconSize(QSize(18, 18))
        x.setToolTip("Close (Esc)")
        x.setAccessibleName("Close options")
        x.clicked.connect(self.close_tray)
        head.addWidget(x)
        lay.addLayout(head)
        self.tabs = QTabBar()
        self.tabs.setExpanding(True)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setObjectName("slottabs")
        self.tabs.setDrawBase(False)
        for key in ORDER:
            self.tabs.addTab(SLOTS[key].label)
        self.tabs.currentChanged.connect(self._tab)
        lay.addWidget(self.tabs)
        self.chip_host = QWidget()
        self.chip_row = FlowLayout(self.chip_host)
        self.chips = QButtonGroup(self)
        self.chips.buttonClicked.connect(lambda *_: self._filter())
        lay.addWidget(self.chip_host)
        self.list = QListWidget()
        self.list.setAccessibleName("Options")
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setSpacing(4)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(OptionDelegate(self))
        self.list.itemEntered.connect(self._hover)
        self.list.viewportEntered.connect(lambda: self.preview.emit(self.slot, None, None))
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda *_: self._use())
        self.list.itemActivated.connect(lambda *_: self._use())
        lay.addWidget(self.list, 1)
        # In the list's place when SteamGridDB has nothing for this piece.
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(12, 0, 12, 0)
        el.setSpacing(8)
        el.addStretch(1)
        self.empty_icon = QLabel()
        self.empty_icon.setPixmap(icons.pixmap("images", 36, theme.C["text3"], self.devicePixelRatioF()))
        el.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = label("", "", wrap=True)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setStyleSheet("font-weight: 600;")
        el.addWidget(self.empty_title)
        self.empty_text = label("Artists add new artwork all the time, so look again later. "
                                "You can still pick another piece above.", "muted", wrap=True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_text)
        el.addStretch(2)
        self.empty.hide()
        lay.addWidget(self.empty, 1)
        self.status = label("", "faint", wrap=True)
        lay.addWidget(self.status)
        self.more = QPushButton("Load more")
        self.more.clicked.connect(self._more)
        lay.addWidget(self.more)
        foot = QHBoxLayout()
        foot.addStretch(0)  # takes the room only while Pick This is hidden, keeping Clear on the right
        self.use_btn = QPushButton("Pick This")
        self.use_btn.setProperty("kind", "confirm")
        self.use_btn.setAccessibleDescription("Picks it for this game; Apply saves it to Steam")
        self.use_btn.clicked.connect(self._use)
        foot.addWidget(self.use_btn, 1)
        self.remove_btn = QPushButton("Clear")
        self.remove_btn.setAccessibleDescription("Removes this piece when you apply")
        self.remove_btn.clicked.connect(self._clear)
        foot.addWidget(self.remove_btn)
        lay.addLayout(foot)
        self.assets = []
        self.page = 0

    def open_slot(self, slot):
        self.slot = slot
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(ORDER.index(slot.key))
        self.tabs.blockSignals(False)
        self.title.setText(slot.label)
        self.use_btn.setText(f"Pick This {slot.label.title()}")
        self.list.setIconSize(thumb_size(slot.key))
        self.assets = []
        self.page = 0
        self.list.clear()
        self._show_empty(False)
        self._chips([])
        self.more.hide()
        self.show()
        self._update_buttons()
        self.slot_changed.emit(slot)
        self.load()

    def load(self):
        game = self.win.current_game()
        if game is None:
            self.status.setText("Match this game to a SteamGridDB title first.")
            return
        self.status.setText("Loading options…")
        slot, page, appid = self.slot, self.page, self.win.current_shortcut().appid
        self.win.fetch_assets(game.id, slot.key, page,
                              lambda assets: self._loaded(slot, page, appid, assets))

    def _loaded(self, slot, page, appid, assets):
        if slot is not self.slot or page != self.page or self.win.current_shortcut().appid != appid:
            return
        if assets is None:
            self.status.setText("Couldn't load options from SteamGridDB.")
            return
        self.assets += assets
        in_use, picked = self._ids()
        for i, a in enumerate(assets):
            name = (f"{slot.label} option {len(self.assets) - len(assets) + i + 1}, by {a.author or 'unknown'}"
                    + (f", {a.style}" if a.style else ""))
            it = QListWidgetItem(name)
            it.setData(ROLE_NAME, name)
            it.setData(ROLE_ASSET, a)
            self._mark(it, in_use, picked)
            it.setToolTip(f"{a.width} × {a.height} · {'animated · ' if a.animated else ''}"
                          f"{a.style or 'style unknown'} · by {a.author or 'unknown'}")
            self.list.addItem(it)
            self.win.load_thumb(a, it)
        self.more.setVisible(len(assets) >= 50)
        self._chips(sorted({a.style for a in self.assets if a.style}))
        self._filter()
        if not self.assets:
            self.empty_title.setText(f"No {slot.label.lower()} for this game on SteamGridDB yet")
            self._show_empty(True)
            self.status.setText("")
        else:
            info = SLOTS[slot.key]
            self.status.setText(f"{len(self.assets)} options. Hover to preview, then "
                                f"“Pick This {info.label.title()}”. Apply saves your picks.")

    def _show_empty(self, empty: bool):
        if empty and self.list.hasFocus():
            self.tabs.setFocus(Qt.FocusReason.OtherFocusReason)  # not on to Clear, as hiding would
        self.list.setVisible(not empty)
        self.empty.setVisible(empty)
        self.status.setVisible(not empty)
        self._update_buttons()

    def _more(self):
        self.page += 1
        self.more.hide()
        self.load()

    def _chips(self, styles: list[str]):
        for b in self.chips.buttons():
            self.chips.removeButton(b)
            b.deleteLater()
        for name in ["all"] + styles:
            b = QPushButton(name.replace("_", " ").capitalize() if name != "all" else "All")
            b.setProperty("kind", "chip")
            b.setProperty("style_key", name)
            b.setCheckable(True)
            b.setChecked(name == "all")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chips.addButton(b)
            self.chip_row.addWidget(b)
        self.chip_host.setVisible(len(styles) > 1)
        self.chip_row.invalidate()

    def _filter(self):
        b = self.chips.checkedButton()
        want = b.property("style_key") if b else "all"
        for i in range(self.list.count()):
            a = self.list.item(i).data(ROLE_ASSET)
            self.list.item(i).setHidden(want != "all" and a.style != want)

    def _tab(self, i):
        self.preview.emit(self.slot, None, None)
        self.open_slot(SLOTS[ORDER[i]])

    def _hover(self, item):
        self.preview.emit(self.slot, item.data(ROLE_PIX), item.data(ROLE_ASSET))

    def leaveEvent(self, e):
        self.preview.emit(self.slot, None, None)
        super().leaveEvent(e)

    def _selected(self, *_):
        self._update_buttons()

    def _pick(self):
        sc = self.win.current_shortcut()
        return self.win.pending.get(sc.appid, self.slot.key) if sc else None

    def _ids(self):
        """The id of the option on disk now, and of the waiting pick (None when there isn't one)."""
        sc = self.win.current_shortcut()
        applied = self.win.config.get_applied(sc.appid, self.slot.key) if sc else None
        in_use = applied.get("id") if applied and self.win.slot_path(sc, self.slot) else None
        pick = self._pick()
        picked = pick.asset.id if pick is not None and not pick.clears else None
        return in_use, picked

    def _mark(self, it: QListWidgetItem, in_use, picked):
        a = it.data(ROLE_ASSET)
        it.setData(ROLE_INUSE, a.id == in_use and picked is None and not self._clearing())
        it.setData(ROLE_PICK, a.id == picked)
        it.setText(it.data(ROLE_NAME) + (", picked" if a.id == picked else ", in use" if it.data(ROLE_INUSE) else ""))

    def _clearing(self) -> bool:
        pick = self._pick()
        return pick is not None and pick.clears

    def _update_buttons(self):
        it = self.list.currentItem()
        self.use_btn.setEnabled(it is not None and not it.data(ROLE_INUSE) and not it.data(ROLE_PICK))
        sc = self.win.current_shortcut()
        pick = self._pick()
        on_disk = sc is not None and self.win.slot_path(sc, self.slot) is not None
        self.remove_btn.setEnabled((on_disk or pick is not None) and not (pick is not None and pick.clears))
        # Nothing to pick: no Pick This button, and Clear only while there's something to clear.
        empty = self.empty.isVisibleTo(self)
        self.use_btn.setVisible(not empty)
        self.remove_btn.setVisible(not empty or self.remove_btn.isEnabled())

    def _use(self):
        it = self.list.currentItem()
        if it is not None and not it.data(ROLE_INUSE) and not it.data(ROLE_PICK):
            self.win.stage_pick(self.slot, it.data(ROLE_ASSET))
            self.mark_in_use()

    def _clear(self):
        """Same as the Clear pill: drop a new pick when nothing is on disk, else mark the piece for clearing."""
        sc = self.win.current_shortcut()
        pick = self._pick()
        if pick is not None and not pick.clears and self.win.slot_path(sc, self.slot, fresh=True) is None:
            self.win.unstage(self.slot)
        else:
            self.win.stage_clear(self.slot)
        self.mark_in_use()

    def mark_in_use(self):
        """Re-mark the in-use option and the waiting pick, after any change."""
        in_use, picked = self._ids()
        for i in range(self.list.count()):
            self._mark(self.list.item(i), in_use, picked)
        self.list.viewport().update()
        self._update_buttons()

    def close_tray(self):
        self.preview.emit(self.slot, None, None)
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_tray()
        else:
            super().keyPressEvent(e)
