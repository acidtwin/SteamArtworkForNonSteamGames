"""A game's page: one labelled row per piece of artwork, with its options beside it."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..artwork import SLOTS, Slot
from ..sgdb import Asset
from . import theme
from .widgets import ArtView, label, load_pixmap, quiet_button


@dataclass(frozen=True)
class SlotInfo:
    where: str
    size: str
    art: QSize  # the piece as shown in its row
    thumb: QSize  # one option in the strip
    empty: str


INFO = {
    "grid": SlotInfo("Your library grid and collections.", "600 × 900", QSize(124, 186), QSize(88, 132), "No cover"),
    "wide": SlotInfo("Recent games shelf and Big Picture.", "920 × 430", QSize(248, 116), QSize(196, 92), "No wide cover"),
    "hero": SlotInfo("The wide image at the top of the game's page.", "1920 × 620", QSize(300, 97), QSize(240, 78), "No banner"),
    "logo": SlotInfo("Laid over the banner on the game's page.", "Transparent PNG", QSize(300, 97), QSize(200, 76), "No logo"),
    "icon": SlotInfo("The game list, and desktop shortcuts.", "256 × 256", QSize(96, 96), QSize(64, 64), "No icon"),
}
STRIP = 6


class ArtworkRow(QWidget):
    choose = Signal(object, object)  # slot, Asset
    open_tray = Signal(object)  # slot

    def __init__(self, win, slot: Slot):
        super().__init__()
        self.win, self.slot = win, slot
        info = INFO[slot.key]
        lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        lay.setContentsMargins(0, 18, 0, 18)
        lay.setSpacing(20)
        self.box = lay

        left = QVBoxLayout()
        left.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(label(slot.label, "heading"))
        self.missing = label("MISSING", "tagmiss")
        head.addWidget(self.missing)
        head.addStretch(1)
        left.addLayout(head)
        left.addWidget(label(info.where, "muted", wrap=True))
        left.addWidget(label(info.size, "faint"))
        self.on_banner = None
        self.missing.setAccessibleName("Missing")
        if slot.key == "logo":
            self.on_banner = QCheckBox("Show it on the banner")
            self.on_banner.setChecked(win.config.data.get("logo_on_banner", True))
            self.on_banner.toggled.connect(self._toggle_banner)
            left.addSpacing(6)
            left.addWidget(self.on_banner)
        left.addStretch(1)
        left.setContentsMargins(0, 0, 0, 0)
        lw = QWidget()
        lw.setLayout(left)
        self.left_width = max(196, theme.text_width("The wide image at the top of") + 12)  # fits the copy at any font size
        lw.setFixedWidth(self.left_width)
        self.left_col = lw
        lay.addWidget(lw)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        self.art = ArtView(info.art, info.empty, checker=slot.key == "logo")
        self.art.setToolTip(f"See all {slot.label.lower()} options")
        self.art.setAccessibleName(f"{slot.label} in use")
        self.art.clicked.connect(lambda: self.open_tray.emit(self.slot))
        mid.addWidget(self.art)
        self.caption = label("", "faint")
        mid.addWidget(self.caption)
        mid.addStretch(1)
        mid.setContentsMargins(0, 0, 0, 0)
        mw = QWidget()
        mw.setLayout(mid)
        mw.setFixedWidth(max(info.art.width(), 124))
        self.mid_col = mw
        lay.addWidget(mw)

        right = QVBoxLayout()
        right.setSpacing(8)
        top = QHBoxLayout()
        self.count = label("", "faint")
        self.count.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.count, 1)
        self.see_all = quiet_button("See all", lambda: self.open_tray.emit(self.slot))
        self.see_all.setAccessibleName(f"See all {slot.label.lower()} options")
        top.addWidget(self.see_all)
        right.addLayout(top)
        self.message = label("", "empty", wrap=True)
        self.message.setMinimumWidth(120)
        right.addWidget(self.message)
        self.strip_host = QWidget()
        self.strip = QHBoxLayout(self.strip_host)
        self.strip.setContentsMargins(0, 0, 0, 4)
        self.strip.setSpacing(10)
        self.strip.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self.strip_host)
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(80)
        scroll.setFixedHeight(info.thumb.height() + 6)
        self._viewport = scroll.viewport()
        self._viewport.installEventFilter(self)
        self.scroll = scroll
        right.addWidget(scroll)
        right.addStretch(1)
        lay.addLayout(right, 1)

        self.assets: list[Asset] = []
        self.thumbs: list[ArtView] = []
        self.total = 0
        self._previewing = False

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Resize and obj is self._viewport:
            self._fit()
        return super().eventFilter(obj, e)

    def _fit(self):
        """Show only the options that fit; the count on "See all" covers the rest.

        Nothing is ever clipped: on a 1280 px screen a wide-cover row shows
        two whole options and "See all 12", not two and a half.
        """
        if not self.thumbs:
            return
        info = INFO[self.slot.key]
        gap = self.strip.spacing()
        width = self.scroll.viewport().width()
        n = max(1, (width + gap) // (info.thumb.width() + gap))
        for i, v in enumerate(self.thumbs):
            v.setVisible(i < n)
        hidden = self.total - min(n, len(self.thumbs))
        shown = f"{self.total}+" if self.total >= 50 else str(self.total)
        self.see_all.setText(f"See all {shown}" if hidden <= 0 else f"See {hidden} more")

    def set_compact(self, compact: bool):
        """Stack name, art and options when the page is narrow (tray open, small window)."""
        direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        if self.box.direction() == direction:
            return
        self.box.setDirection(direction)
        self.box.setSpacing(10 if compact else 20)
        if compact:
            self.left_col.setMinimumWidth(0)
            self.left_col.setMaximumWidth(16777215)
        else:
            self.left_col.setFixedWidth(self.left_width)

    # -- current art -------------------------------------------------------

    def show_current(self):
        sc = self.win.current_shortcut()
        path = self.win.slot_path(sc, self.slot)
        self.missing.setVisible(path is None)
        pm = load_pixmap(path) if path else None
        backdrop = None
        if self.slot.key == "logo" and self.on_banner.isChecked():
            hero = self.win.slot_path(sc, SLOTS["hero"])
            backdrop = load_pixmap(hero) if hero else None
        self.art.set_pixmap(pm, backdrop)
        applied = self.win.config.get_applied(sc.appid, self.slot.key)
        if path is None:
            self.caption.setText("Nothing set")
        elif applied and applied.get("author"):
            self.caption.setText(f"In use · by {applied['author']}")
        else:
            self.caption.setText("In use")
        self.art.describe(f"{self.slot.label}: {'nothing set' if path is None else self.caption.text()}")
        for view, a in zip(self.thumbs, self.assets):
            view.badge = bool(applied and applied.get("id") == a.id and path is not None)
            view.update()

    def _toggle_banner(self, on: bool):
        self.win.config.data["logo_on_banner"] = on
        self.win.config.save()
        self.show_current()

    # -- options -----------------------------------------------------------

    def set_message(self, text: str):
        self._clear_strip()
        self.count.setText("")
        self.see_all.hide()
        self.scroll.hide()
        self.message.setText(text)
        self.message.show()

    def set_loading(self):
        self.set_message("Loading options from SteamGridDB…")

    def set_assets(self, assets: list[Asset], more: bool):
        self._clear_strip()
        self.assets = assets[:STRIP]
        n = len(assets)
        if n == 0:
            self.set_message(f"SteamGridDB has no {self.slot.label.lower()} for this game yet.")
            return
        self.message.hide()
        self.scroll.show()
        self.total = n if not more else 50
        shown = f"{n}+" if more else str(n)
        self.count.setText(f"<b>{shown} option{'s' if n != 1 else ''}</b> on SteamGridDB")
        self.see_all.setText(f"See all {shown}")
        self.see_all.show()
        info = INFO[self.slot.key]
        for i, a in enumerate(self.assets):
            v = ArtView(info.thumb, "", checker=self.slot.key == "logo")
            v.setToolTip(f"{a.width} × {a.height} · {a.style or 'style unknown'} · by {a.author or 'unknown'}\n"
                         "Click to use it")
            v.describe(f"{self.slot.label} option {i + 1} of {len(self.assets)}, by {a.author or 'unknown'}"
                       + (f", {a.style}" if a.style else ""))
            v.setAccessibleDescription("Press Enter to use it")
            v.clicked.connect(lambda a=a: self.choose.emit(self.slot, a))
            v.hovered.connect(lambda on, a=a, v=v: self._preview(on, v))
            self.strip.insertWidget(self.strip.count() - 1, v)
            self.thumbs.append(v)
            self.win.load_thumb(a, v)
        self._fit()
        self.show_current()

    def _preview(self, on: bool, view: ArtView):
        if on and not view.pixmap.isNull():
            backdrop = self.art.backdrop if self.slot.key == "logo" else None
            self.art.set_pixmap(view.pixmap, backdrop)
            self.caption.setText("Previewing")
            self._previewing = True
        elif self._previewing:
            self._previewing = False
            self.show_current()

    def _clear_strip(self):
        for v in self.thumbs:
            v.deleteLater()
        self.thumbs = []
        self.assets = []


class GamePage(QWidget):
    fill = Signal()
    change_match = Signal()

    def __init__(self, win):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(24)
        outer.addWidget(self.scroll)
        body = QWidget()
        self.scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(0)

        top = QHBoxLayout()
        self.title = label("", "title")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.title, 1)
        self.fill_btn = QPushButton("Fill missing")
        self.fill_btn.setAccessibleDescription("Downloads the top-rated option for every missing piece of this game")
        self.fill_btn.clicked.connect(self.fill.emit)
        top.addWidget(self.fill_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        self.match = label("", "muted")
        self.match.setTextFormat(Qt.TextFormat.RichText)
        meta.addWidget(self.match)
        self.match_btn = quiet_button("Not this game?", self.change_match.emit)
        meta.addWidget(self.match_btn)
        meta.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(meta)

        # Shown instead of five identical row messages when there is no API key yet.
        self.keybar = QWidget()
        self.keybar.setObjectName("keybar")
        self.keybar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        kb = QHBoxLayout(self.keybar)
        kb.setContentsMargins(16, 10, 10, 10)
        kb.addWidget(label("To see artwork options you need a free SteamGridDB API key.", "", wrap=True), 1)
        self.key_btn = QPushButton("Add API key")
        self.key_btn.setProperty("kind", "confirm")
        self.key_btn.clicked.connect(win.ask_api_key)
        kb.addWidget(self.key_btn)
        self.keybar.hide()
        lay.addSpacing(12)
        lay.addWidget(self.keybar)

        self.rows: dict[str, ArtworkRow] = {}
        for key in ("grid", "wide", "hero", "logo", "icon"):
            line = QFrame()
            line.setObjectName("rowline")
            line.setFixedHeight(1)
            lay.addSpacing(10)
            lay.addWidget(line)
            row = ArtworkRow(win, SLOTS[key])
            row.choose.connect(win.apply_option)
            row.open_tray.connect(win.open_tray)
            self.rows[key] = row
            lay.addWidget(row)
        lay.addStretch(1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        compact = self.width() < 860
        for row in self.rows.values():
            row.set_compact(compact)

    def show_game(self):
        sc = self.win.current_shortcut()
        self.title.setText(sc.name or "(unnamed)")
        missing = 5 - sum(self.win.coverage(sc))
        self.fill_btn.setText(f"Fill {missing} missing")
        self.fill_btn.setVisible(missing > 0)
        self.fill_btn.setEnabled(not self.win.busy)
        self.update_match()
        for row in self.rows.values():
            row.show_current()

    def update_match(self):
        sc = self.win.current_shortcut()
        m = self.win.match_info(sc)
        status = self.win.status.get(sc.appid)
        if m:
            extra = "  <span style='color:#e3b341'>(best guess, please check)</span>" if status == "check" else ""
            year = f" ({m['year']})" if m.get("year") else ""
            self.match.setText(f"Matched to <b>{m['name']}</b>{year} on SteamGridDB{extra}")
            self.match_btn.setText("Not this game?")
        elif status == "notfound":
            self.match.setText("<span style='color:#ef7b6c'>Not found on SteamGridDB</span>")
            self.match_btn.setText("Search by another name")
        else:
            self.match.setText("Not matched to a SteamGridDB game yet.")
            self.match_btn.setText("Find it")

    def refresh_current(self):
        for row in self.rows.values():
            row.show_current()
        sc = self.win.current_shortcut()
        missing = 5 - sum(self.win.coverage(sc))
        self.fill_btn.setText(f"Fill {missing} missing")
        self.fill_btn.setVisible(missing > 0)

    def set_all_messages(self, text: str):
        self.keybar.hide()
        for row in self.rows.values():
            row.set_message(text)

    def show_key_prompt(self):
        self.keybar.show()
        for row in self.rows.values():
            row.set_message("Options appear here once a key is added.")
