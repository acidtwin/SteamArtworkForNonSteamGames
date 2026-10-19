"""A game's page: the match card, then one labelled row per piece of artwork with its options beside it."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..artwork import SLOTS, Slot
from ..sgdb import Asset
from . import icons, theme
from .theme import C, q
from .widgets import ArtView, fill_rect, label, load_pixmap, quiet_button

WAIT = C.get("wait", "#e3b341")
NEW_BG = C.get("action", "#1474c8")


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


def draw_new_tag(p: QPainter, rect: QRectF, text: str = "NEW"):
    """The blue tag in the top-left corner of a picked piece that isn't applied yet."""
    p.save()
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("tag"))
    f.setWeight(QFont.Weight.Bold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
    p.setFont(f)
    fm = QFontMetricsF(f)
    r = QRectF(rect.left() + 8, rect.top() + 8, fm.horizontalAdvance(text) + 14, fm.height() + 4)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(NEW_BG))
    p.drawRoundedRect(r, 2, 2)
    p.setPen(QColor("white"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


class OptionView(ArtView):
    """One option in a row's strip; says when its thumbnail arrives."""

    loaded = Signal()

    def set_pixmap(self, pm: QPixmap | None, backdrop: QPixmap | None = None):
        super().set_pixmap(pm, backdrop)
        self.loaded.emit()


class CurrentArt(ArtView):
    """The piece in use (or waiting), with a frosted "Clear" pill on hover.

    `clicked` opens the tray as before; `cleared` fires from the pill or the
    Delete key. `hover` and `pill_hot` are plain flags so they can be set
    directly (tests, screenshots).
    """

    cleared = Signal()

    def __init__(self, size: QSize, empty_text: str = "", checker: bool = False, parent=None):
        super().__init__(size, empty_text, checker, parent)
        self.clearable = False
        self.new = False
        self.hover = False
        self.pill_hot = False
        self._key_focus = False
        self.setMouseTracking(True)
        self.setAccessibleDescription("Press Enter to open the options")

    def set_clearable(self, on: bool):
        self.clearable = on
        self.setAccessibleDescription("Press Enter to open the options" + (", Delete to clear it" if on else ""))
        self.update()

    def _small(self) -> bool:
        return self.width() < 110  # the icon: just the ✕ in a circle

    def pill_rect(self) -> QRectF:
        h = 34.0
        if self._small():
            w = h
        else:
            w = 11 + 13 + 6 + QFontMetricsF(self._pill_font()).horizontalAdvance("Clear") + 14
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _pill_font(self) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("body") * 0.96)
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def _showing_pill(self) -> bool:
        return self.clearable and (self.hover or self._key_focus)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.pixmap.isNull() and self.backdrop.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.new:
            draw_new_tag(p, r)
        if not self._showing_pill():
            return
        clip = QPainterPath()
        clip.addRoundedRect(r, self.round, self.round)
        p.fillPath(clip, QColor(10, 13, 18, 107))
        pr = self.pill_rect()
        p.setPen(Qt.PenStyle.NoPen)
        for i, a in enumerate((26, 16, 8)):  # soft drop shadow
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(pr.adjusted(-i, -i + 3, i, i + 3), pr.height() / 2 + i, pr.height() / 2 + i)
        if self.pill_hot:
            p.setBrush(QColor(239, 91, 76, 230))
        else:
            p.setBrush(QColor(23, 26, 33, 184))
            p.setPen(QPen(QColor(255, 255, 255, 41), 1))
        p.drawRoundedRect(pr.adjusted(0.5, 0.5, -0.5, -0.5), pr.height() / 2, pr.height() / 2)
        if self._small():
            icons.draw(p, "clear", QRectF(pr.center().x() - 6.5, pr.center().y() - 6.5, 13, 13), "#ffffff")
        else:
            icons.draw(p, "clear", QRectF(pr.left() + 11, pr.center().y() - 6.5, 13, 13), "#ffffff")
            p.setFont(self._pill_font())
            p.setPen(QColor("white"))
            p.drawText(QRectF(pr.left() + 30, pr.top(), pr.width() - 30, pr.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Clear")

    def enterEvent(self, e):
        self.hover = True
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover = self.pill_hot = False
        super().leaveEvent(e)

    def focusInEvent(self, e):
        self._key_focus = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._key_focus = False
        super().focusOutEvent(e)

    def mouseMoveEvent(self, e):
        hot = self._showing_pill() and self.pill_rect().contains(e.position())
        if hot != self.pill_hot:
            self.pill_hot = hot
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton and self._showing_pill()
                and self.pill_rect().contains(e.position())):
            self.cleared.emit()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.clearable:
            self.cleared.emit()
        else:
            super().keyPressEvent(e)


class ArtworkRow(QWidget):
    choose = Signal(object, object)  # slot, Asset (unused: options stage through win.stage_pick)
    open_tray = Signal(object)  # slot
    thumb_loaded = Signal()  # the first option's thumbnail changed

    def __init__(self, win, slot: Slot):
        super().__init__()
        self.win, self.slot = win, slot
        info = INFO[slot.key]
        lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        lay.setContentsMargins(0, 16, 0, 16)
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
        self.art = CurrentArt(info.art, info.empty, checker=slot.key == "logo")
        self.art.setToolTip(f"See all {slot.label.lower()} options")
        self.art.setAccessibleName(f"{slot.label} in use")
        self.art.clicked.connect(lambda: self.open_tray.emit(self.slot))
        self.art.cleared.connect(self.clear_piece)
        mid.addWidget(self.art)
        self.caption = label("", "faint")
        mid.addWidget(self.caption)
        mid.addStretch(1)
        mid.setContentsMargins(0, 0, 0, 0)
        mw = QWidget()
        mw.setLayout(mid)
        # Wide enough for the longest caption, semibold, at any font size.
        mw.setFixedWidth(max(info.art.width(), 124, round(theme.text_width("Removed when you apply") * 0.98)))
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

    def pick(self):
        """The waiting change for this piece of the open game, if any."""
        sc = self.win.current_shortcut()
        return self.win.pending.get(sc.appid, self.slot.key) if sc else None

    def _pick_pixmap(self, sc, key: str, pick) -> QPixmap | None:
        pm = self.win.pick_pixmap(sc, key)
        if (pm is None or pm.isNull()) and key == self.slot.key:
            # Until the full image is loaded, the option's thumbnail stands in.
            for view, a in zip(self.thumbs, self.assets):
                if a.id == pick.asset.id and not view.pixmap.isNull():
                    return view.pixmap
        return pm

    def _backdrop(self, sc) -> QPixmap | None:
        """The banner a logo is drawn over: a waiting banner pick wins over the one on disk."""
        if self.slot.key != "logo" or not self.on_banner.isChecked():
            return None
        hp = self.win.pending.get(sc.appid, "hero")
        if hp is not None:
            return None if hp.clears else self.win.pick_pixmap(sc, "hero")
        hero = self.win.slot_path(sc, SLOTS["hero"])
        return load_pixmap(hero) if hero else None

    def _set_caption(self, text: str, tone: str = ""):
        self.caption.setText(text)
        style = {"new": f"color: {C['blue_ink']}; font-weight: 600;", "rm": f"color: {WAIT}; font-weight: 600;"}
        self.caption.setStyleSheet(style.get(tone, ""))

    def show_current(self):
        sc = self.win.current_shortcut()
        path = self.win.slot_path(sc, self.slot)
        pick = self.pick()
        self.missing.setVisible(path is None and (pick is None or pick.clears))
        backdrop = self._backdrop(sc)
        if pick is not None and pick.clears:
            pm = None
            backdrop = None
            self._set_caption("Removed when you apply" if path else "Nothing set", "rm" if path else "")
        elif pick is not None:
            pm = self._pick_pixmap(sc, self.slot.key, pick)
            self._set_caption("Not applied yet", "new")
        else:
            pm = load_pixmap(path) if path else None
            applied = self.win.config.get_applied(sc.appid, self.slot.key)
            if path is None:
                self._set_caption("Nothing set")
            elif applied and applied.get("author"):
                self._set_caption(f"In use · by {applied['author']}")
            else:
                self._set_caption("In use")
        self.art.new = pick is not None and not pick.clears
        self.art.set_pixmap(pm, backdrop)
        self.art.set_clearable(pick is not None and not pick.clears or (pick is None and path is not None))
        self.art.describe(f"{self.slot.label}: {self.caption.text()}")
        applied = self.win.config.get_applied(sc.appid, self.slot.key)
        for view, a in zip(self.thumbs, self.assets):
            chosen = pick is not None and not pick.clears and pick.asset.id == a.id
            view.selected = chosen
            view.badge = pick is None and bool(applied and applied.get("id") == a.id and path is not None)
            view.setToolTip(f"{a.width} × {a.height} · {a.style or 'style unknown'} · by {a.author or 'unknown'}\n"
                            + ("Click to un-pick it" if chosen else "Click to pick it"))
            view.update()

    def clear_piece(self):
        """The Clear pill: drop a new pick when there's nothing on disk, else mark the piece for clearing."""
        sc = self.win.current_shortcut()
        pick = self.pick()
        if pick is not None and not pick.clears and self.win.slot_path(sc, self.slot) is None:
            self.win.unstage(self.slot)
        else:
            self.win.stage_clear(self.slot)
        self.show_current()

    def _option_clicked(self, a: Asset):
        pick = self.pick()
        if pick is not None and not pick.clears and pick.asset.id == a.id:
            self.win.unstage(self.slot)
        else:
            self.win.stage_pick(self.slot, a)
        self.show_current()

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
            v = OptionView(info.thumb, "", checker=self.slot.key == "logo")
            v.describe(f"{self.slot.label} option {i + 1} of {len(self.assets)}, by {a.author or 'unknown'}"
                       + (f", {a.style}" if a.style else ""))
            v.setAccessibleDescription("Press Enter to pick it")
            v.clicked.connect(lambda a=a: self._option_clicked(a))
            v.hovered.connect(lambda on, v=v: self._preview(on, v))
            if i == 0:
                v.loaded.connect(self.thumb_loaded.emit)
            v.loaded.connect(lambda a=a: self._thumb_arrived(a))
            self.strip.insertWidget(self.strip.count() - 1, v)
            self.thumbs.append(v)
            self.win.load_thumb(a, v)
        self._fit()
        self.show_current()

    def _thumb_arrived(self, a: Asset):
        pick = self.pick()
        if pick is not None and not pick.clears and pick.asset.id == a.id and not self._previewing:
            self.show_current()

    def first_thumb(self) -> QPixmap | None:
        return self.thumbs[0].pixmap if self.thumbs and not self.thumbs[0].pixmap.isNull() else None

    def _preview(self, on: bool, view: ArtView):
        if on and not view.pixmap.isNull():
            backdrop = self.art.backdrop if self.slot.key == "logo" else None
            self.art.new = False
            self.art.set_pixmap(view.pixmap, backdrop)
            self._set_caption("Previewing")
            self._previewing = True
        elif self._previewing:
            self._previewing = False
            self.show_current()

    def _clear_strip(self):
        had = bool(self.thumbs)
        for v in self.thumbs:
            self.strip.removeWidget(v)
            v.hide()
            v.deleteLater()
        self.thumbs = []
        self.assets = []
        if had:
            self.thumb_loaded.emit()


class CoverThumb(QWidget):
    """The 40 × 60 cover on the match card: the first cover option, else a plain block."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 60)
        self.pixmap: QPixmap | None = None

    def set_pixmap(self, pm: QPixmap | None):
        self.pixmap = pm
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 3, 3)
        p.setClipPath(clip)
        if self.pixmap is not None and not self.pixmap.isNull():
            dpr = self.devicePixelRatioF()
            pm = fill_rect(self.pixmap, self.size() * dpr)
            pm.setDevicePixelRatio(dpr)
            p.drawPixmap(0, 0, pm)
        else:
            g = QLinearGradient(QPointF(0, 0), QPointF(self.width() * 0.4, self.height()))
            g.setColorAt(0, q("raised"))
            g.setColorAt(1, QColor("#141a22"))
            p.fillRect(self.rect(), g)


class MatchCard(QWidget):
    """Which SteamGridDB game the artwork comes from, and the buttons to fix it."""

    confirm = Signal()
    change = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "finding"
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 12, 10)
        lay.setSpacing(14)
        self.cover = CoverThumb()
        lay.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
        info = QVBoxLayout()
        info.setSpacing(3)
        info.setContentsMargins(0, 0, 0, 0)
        self.kicker = label("")
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("caption"))
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        self.kicker.setFont(f)
        self.name = label("")
        self.name.setTextFormat(Qt.TextFormat.RichText)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("heading") * 0.95)
        f.setWeight(QFont.Weight.Bold)
        self.name.setFont(f)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta = label("")
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("faint"))
        self.meta.setFont(f)
        self.meta.setStyleSheet(f"color: {C['text3']};")
        for w in (self.kicker, self.name, self.meta):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            info.addWidget(w)
        lay.addLayout(info, 1)
        self.yes_btn = QPushButton("Yes, That's It")
        self.yes_btn.setProperty("kind", "confirm")
        self.yes_btn.setAccessibleDescription("Keep this SteamGridDB game as the match")
        self.yes_btn.clicked.connect(self.confirm.emit)
        lay.addWidget(self.yes_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.change_btn = QPushButton("Change Game")
        self.change_btn.setAccessibleDescription("Search SteamGridDB for the right game")
        self.change_btn.clicked.connect(self.change.emit)
        lay.addWidget(self.change_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_state(self, state: str, name: str, year: str = "", candidates: int = 0):
        """state: "exact", "check", "notfound" or "finding"."""
        self.state = state
        kicker, colour, meta = {
            "exact": ("ARTWORK FROM STEAMGRIDDB", C["text3"], "Artwork comes from this game on SteamGridDB."),
            "check": ("CHECK THIS MATCH", WAIT,
                      f"We found {candidates} games with a similar name and picked this one." if candidates > 1
                      else "We picked the closest name we found."),
            "notfound": ("NOT FOUND ON STEAMGRIDDB", C["bad"], "Search for it by another name."),
            "finding": ("FINDING THIS GAME ON STEAMGRIDDB…", C["text3"], ""),
        }[state]
        self.kicker.setText(kicker)
        self.kicker.setStyleSheet(f"color: {colour};")
        small = theme.pt("body")
        yr = (f"&nbsp;&nbsp;<span style='color:{C['text3']}; font-weight:400; font-size:{small}pt'>"
              f"{escape(str(year))}</span>") if year else ""
        self.name.setText(f"{escape(name)}{yr}")
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))
        self.yes_btn.setVisible(state == "check")
        self.change_btn.setVisible(state != "finding")
        self.change_btn.setText("Search by Another Name" if state == "notfound" else "Change Game")
        self.setAccessibleName(f"{kicker.capitalize()}: {name}")
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        p.fillPath(path, q("panel"))
        check = self.state == "check"
        if check:
            g = QLinearGradient(r.topLeft(), QPointF(r.left() + r.width() / 2, r.top()))
            g.setColorAt(0, QColor(227, 179, 65, 20))
            g.setColorAt(1, QColor(227, 179, 65, 0))
            p.fillPath(path, g)
        p.setPen(QPen(QColor("#4a4026") if check else q("line"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        if check:
            p.setClipPath(path)
            p.fillRect(QRectF(0, 0, 3, r.height()), QColor(WAIT))


class GamePage(QWidget):
    fill = Signal()  # kept for compatibility; the content bar has the buttons now
    change_match = Signal()
    confirm_match = Signal()

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
        lay.setContentsMargins(24, 14, 24, 24)
        lay.setSpacing(0)

        self.card = MatchCard()
        self.card.confirm.connect(self.confirm_match.emit)
        self.card.change.connect(self.change_match.emit)
        lay.addWidget(self.card)

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
        lay.addSpacing(14)
        lay.addWidget(self.keybar)
        self.keygap = QWidget()
        self.keygap.setFixedHeight(14)
        self.keygap.hide()
        lay.addWidget(self.keygap)

        self.rows: dict[str, ArtworkRow] = {}
        for key in ("grid", "wide", "hero", "logo", "icon"):
            line = QFrame()
            line.setObjectName("rowline")
            line.setFixedHeight(1)
            lay.addWidget(line)
            row = ArtworkRow(win, SLOTS[key])
            row.open_tray.connect(win.open_tray)
            self.rows[key] = row
            lay.addWidget(row)
        self.rows["grid"].thumb_loaded.connect(self._card_cover)
        lay.addStretch(1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        compact = self.width() < 860
        for row in self.rows.values():
            row.set_compact(compact)

    def show_game(self):
        self.update_match()
        for row in self.rows.values():
            row.show_current()

    def update_match(self):
        sc = self.win.current_shortcut()
        m = self.win.match_info(sc)
        status = self.win.status.get(sc.appid)
        if status == "notfound" and not m:
            self.card.set_state("notfound", sc.name or "(unnamed)")
        elif m:
            check = status == "check"
            self.card.set_state("check" if check else "exact", m["name"], m.get("year") or "",
                                self.win.match_candidates(sc) if check else 0)
        else:
            self.card.set_state("finding", sc.name or "(unnamed)")
        self._card_cover()

    def _card_cover(self):
        self.card.cover.set_pixmap(self.rows["grid"].first_thumb() if self.card.state in ("exact", "check") else None)

    def refresh_current(self):
        for row in self.rows.values():
            row.show_current()

    def set_all_messages(self, text: str):
        self.keybar.hide()
        self.keygap.hide()
        for row in self.rows.values():
            row.set_message(text)

    def show_key_prompt(self):
        self.keybar.show()
        self.keygap.show()
        for row in self.rows.values():
            row.set_message("Options appear here once a key is added.")
