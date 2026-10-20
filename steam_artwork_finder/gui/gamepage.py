"""A game's page: a banner header (the game, its SteamGridDB match and its buttons), then one
labelled row per piece of artwork with its options beside it."""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from html import escape

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QFontMetricsF, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFileDialog,
    QMenu,
    QLabel,
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
from ..steamstore import SteamAsset
from . import icons, localfile, theme
from .chrome import _Elided
from .theme import C, q
from .widgets import ArtView, fill_rect, label, load_pixmap, quiet_button

WAIT = C["wait"]
NEW_BG = C["action"]


@dataclass(frozen=True)
class SlotInfo:
    where: str
    size: str
    art: QSize  # the piece as shown in its row
    thumb: QSize  # one option in the strip
    empty: str


INFO = {
    "grid": SlotInfo("Your library grid and collections.", "600 × 900", QSize(124, 186), QSize(80, 120), "No cover"),
    "wide": SlotInfo("Recent games shelf and Big Picture.", "920 × 430", QSize(230, 108), QSize(196, 92), "No wide cover"),
    "hero": SlotInfo("The wide image at the top of the game's page.", "1920 × 620", QSize(230, 74), QSize(240, 78), "No banner"),
    "logo": SlotInfo("Laid over the banner on the game's page.", "Transparent PNG", QSize(230, 74), QSize(140, 53), "No logo"),
    "icon": SlotInfo("The game list, and desktop shortcuts.", "256 × 256", QSize(96, 96), QSize(64, 64), "No icon"),
}
STRIP = 6
ART_W = max(i.art.width() for i in INFO.values())  # every row's middle column, so the strips line up


def _alpha(name: str, a: int) -> QColor:
    """A theme colour, see-through."""
    c = q(name)
    c.setAlpha(a)
    return c


def draw_new_tag(p: QPainter, rect: QRectF, text: str = "NEW", bg: QColor | None = None):
    """The blue tag in the top-left corner of a picked piece that isn't applied yet
    (or, in `bg`, another tag in the same place, like "STEAM")."""
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
    p.setBrush(bg if bg is not None else QColor(NEW_BG))
    p.drawRoundedRect(r, 2, 2)
    p.setPen(QColor("white"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


def describe(a: Asset) -> str:
    """An option's tooltip line: its size, style and who made it."""
    if isinstance(a, SteamAsset):
        return f"{a.width} × {a.height} · Steam's official artwork, from the game's store page"
    return (f"{a.width} × {a.height} · {'animated · ' if a.animated else ''}"
            f"{a.style or 'style unknown'} · by {a.author or 'unknown'}")


def draw_steam_tag(p: QPainter, rect: QRectF):
    """The dark tag marking Steam's own official artwork."""
    draw_new_tag(p, rect, "STEAM", QColor(11, 16, 22, 215))


class OptionView(ArtView):
    """One option in a row's strip; says when its thumbnail arrives."""

    loaded = Signal()
    official = False  # Steam's own artwork: tagged "STEAM"

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.official and not self.selected:
            p = QPainter(self)
            draw_steam_tag(p, QRectF(self.rect()))

    def set_pixmap(self, pm: QPixmap | None, backdrop: QPixmap | None = None):
        super().set_pixmap(pm, backdrop)
        self.loaded.emit()

    def frame_arrived(self):
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
            p.setBrush(_alpha("off", 230))
        else:
            p.setBrush(_alpha("ground", 184))
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


class GhostStrip(QWidget):
    """Stands in for the option strip while there are no options: faint dashed
    outlines in the options' shape, fading out, with the message in a pill."""

    def __init__(self, thumb: QSize, parent=None):
        super().__init__(parent)
        self.thumb = thumb
        self.text = ""
        self.icon = ""
        self.setFixedHeight(thumb.height() + 6)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("body"))
        self.setFont(f)

    def setText(self, text: str, icon: str = ""):
        self.text = text
        self.icon = icon
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        tw, th = self.thumb.width(), self.thumb.height()
        y = (self.height() - th) / 2
        x = 0.75
        while x + tw <= w + 1:  # whole outlines only, like the options' strip
            # fade from 100% to nothing across the strip
            a = max(0.0, 1.0 - max(0.0, x - w * 0.3) / (w * 0.7))
            p.setBrush(_alpha("raised", round(64 * a)))
            pen = QPen(_alpha("dash", round(255 * a)), 1.5, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRoundedRect(QRectF(x, y + 0.75, tw - 1.5, th - 1.5), 3, 3)
            x += tw + 10
        if not self.text:
            return
        fm = QFontMetricsF(self.font())
        pad, gap, isz = 14, 8, 16
        avail = w - 16 - pad * 2 - ((isz + gap) if self.icon else 0) - 8
        text = fm.elidedText(self.text, Qt.TextElideMode.ElideRight, max(40, avail))
        pw = pad * 2 + fm.horizontalAdvance(text) + ((isz + gap) if self.icon else 0)
        ph = max(32, fm.height() + 14)
        pill = QRectF(16, (self.height() - ph) / 2, min(pw, w - 16), ph)
        p.setPen(QPen(q("line"), 1))
        p.setBrush(QColor(23, 29, 37, 235))
        p.drawRoundedRect(pill, ph / 2, ph / 2)
        tx = pill.x() + pad
        if self.icon:
            icons.draw(p, self.icon, QRect(round(tx), round(pill.center().y() - isz / 2), isz, isz), q("blue_ink"))
            tx += isz + gap
        p.setPen(q("text2"))
        p.drawText(QRectF(tx, pill.y(), pill.right() - tx, ph), Qt.AlignmentFlag.AlignVCenter, text)


class CustomImageButton(QPushButton):
    """"Custom Image" under a row's name: use a picture from your own files.

    Click it or press Enter to choose a file, Ctrl+V to paste a picture, or right-click
    for both; a picture dropped anywhere on the row lights it up.
    """

    chosen = Signal()
    pasted = Signal()

    def __init__(self, what: str, parent=None):
        super().__init__("Custom Image", parent)
        self.setIcon(icons.icon("folder", 16, C["blue_ink"]))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(f"Use a custom {what}")
        self.setAccessibleDescription("Press Enter to choose a file, or Control V to paste a picture")
        self.setToolTip(f"Use your own {what}: choose a picture from your files.\n"
                        "You can also drop one on this row, or paste one with Ctrl+V.")
        self.clicked.connect(self.chosen.emit)

    def set_drop_hot(self, on: bool):
        self.setText("Drop It Here" if on else "Custom Image")
        self.setProperty("drop", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Paste):
            self.pasted.emit()
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        pick = QAction("Choose File…", menu)
        pick.triggered.connect(self.chosen.emit)
        paste = QAction("Paste Picture", menu)
        paste.setShortcut(QKeySequence.StandardKey.Paste)
        paste.setEnabled(localfile.usable(QApplication.clipboard().mimeData()))
        paste.triggered.connect(self.pasted.emit)
        menu.addAction(pick)
        menu.addAction(paste)
        menu.exec(e.globalPos())
        menu.deleteLater()


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
        self.file_btn = CustomImageButton(slot.label.lower())
        self.file_btn.chosen.connect(self.choose_file)
        self.file_btn.pasted.connect(self.paste_file)
        left.addSpacing(8)
        left.addWidget(self.file_btn, 0, Qt.AlignmentFlag.AlignLeft)
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
        # As wide as the widest piece in every row, so the strips start in line, and wide
        # enough for the longest caption, semibold, at any font size.
        mw.setFixedWidth(max(ART_W, round(theme.text_width("Removed when you apply") * 0.98)))
        self.mid_col = mw
        lay.addWidget(mw)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addStretch(0)  # grows while a message shows, so the message sits mid-row
        self._right = right
        top = QHBoxLayout()
        self.count = label("", "faint")
        self.count.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.count, 1)
        self.see_all = quiet_button("See all", lambda: self.open_tray.emit(self.slot))
        self.see_all.setAccessibleName(f"See all {slot.label.lower()} options")
        top.addWidget(self.see_all)
        right.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(10)
        options = QVBoxLayout()
        options.setSpacing(0)
        options.setContentsMargins(0, 0, 0, 0)
        body.addLayout(options, 1)
        self.message = GhostStrip(info.thumb)
        options.addWidget(self.message)
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
        options.addWidget(scroll)
        right.addLayout(body)
        right.addStretch(1)
        self.setAcceptDrops(True)
        lay.addLayout(right, 1)

        self.assets: list[Asset] = []
        self.steam_assets: list[Asset] = []
        self._sgdb: tuple = ("loading",)
        self.thumbs: list[ArtView] = []
        self.total = 0
        self._previewing = False
        self.gen = 0  # counts option loads, so a late answer to an earlier one is ignored
        # A strip option is previewed on the piece once the pointer rests on it, as in See all,
        # so sweeping across the strip doesn't start a decode for every option passed over.
        self._rest = QTimer(self, singleShot=True, interval=140)
        self._rest.timeout.connect(self._preview_now)
        self._rest_view: ArtView | None = None

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
        style = {"new": f"color: {C['blue_ink']}; font-weight: 600;", "rm": f"color: {WAIT}; font-weight: 600;",
                 "preview": f"color: {C['blue']}; font-weight: 700;"}
        self.caption.setStyleSheet(style.get(tone, ""))

    def show_current(self):
        sc = self.win.current_shortcut()
        path = self.win.slot_path(sc, self.slot)
        pick = self.pick()
        self.missing.setVisible(path is None and (pick is None or pick.clears))
        backdrop = self._backdrop(sc)
        spec, animated = (None, None), False
        if pick is not None and pick.clears:
            pm = None
            backdrop = None
            self._set_caption("Removed when you apply" if path else "Nothing set", "rm" if path else "")
        elif pick is not None:
            pm = self._pick_pixmap(sc, self.slot.key, pick)
            self._set_caption("From the game's .exe · not applied yet" if pick.asset.style == "exe"
                              else "Not applied yet", "new")
            spec = self.win.motion_spec(pick.asset, self.art.size(), crop=not self.art.checker)
            animated = pick.asset.animated
        else:
            pm = load_pixmap(path) if path else None
            spec = self.win.file_motion_spec(path, self.art.size())
            animated = spec[0] is not None
            applied = self.win.config.get_applied(sc.appid, self.slot.key)
            if path is None:
                self._set_caption("Nothing set")
            elif applied and applied.get("from_exe"):
                self._set_caption("In use · from the game's .exe")
            elif applied and applied.get("file"):
                self._set_caption("In use · custom image")
            elif applied and applied.get("steam"):
                self._set_caption("In use · from Steam")
            elif applied and applied.get("author"):
                self._set_caption(f"In use · by {applied['author']}")
            else:
                self._set_caption("In use")
        self.art.new = pick is not None and not pick.clears
        self.art.animated = animated
        playing = spec[0] is not None and spec[0] == self.art.motion_spec[0]
        self.art.set_motion(*spec)
        if not playing or self.art.pixmap.isNull():
            self.art.set_pixmap(pm, backdrop)
        else:  # playing: keep the frame on show, but take a new banner behind a logo
            self.art.set_pixmap(self.art.pixmap, backdrop)
        self.art.set_clearable(pick is not None and not pick.clears or (pick is None and path is not None))
        self.art.describe(f"{self.slot.label}: {self.caption.text()}")
        applied = self.win.config.get_applied(sc.appid, self.slot.key)
        for view, a in zip(self.thumbs, self.assets):
            chosen = pick is not None and not pick.clears and pick.asset.id == a.id
            view.selected = chosen
            view.badge = pick is None and bool(applied and applied.get("id") == a.id and path is not None)
            view.setToolTip(describe(a) + "\n" + ("Click to un-pick it" if chosen else "Click to pick it"))
            view.update()

    def clear_piece(self):
        """The Clear pill: drop a new pick when there's nothing on disk, else mark the piece for clearing."""
        sc = self.win.current_shortcut()
        pick = self.pick()
        if pick is not None and not pick.clears and self.win.slot_path(sc, self.slot, fresh=True) is None:
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

    # -- your own files ------------------------------------------------------

    def choose_file(self):
        start = self.win.config.data.get("last_image_folder", "")
        if not start or not Path(start).is_dir():
            start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        path, _ = QFileDialog.getOpenFileName(self.window(), f"Choose a {self.slot.label.lower()}", start,
                                              localfile.FILTER)
        if path:
            self.win.stage_file(self.slot, path=path)

    def paste_file(self):
        src = localfile.source(QApplication.clipboard().mimeData())
        if src is None:
            self.win.toast.show_message("There's no picture on the clipboard to paste.")
            return
        self.win.stage_file(self.slot, **src)

    def dragEnterEvent(self, e):
        if localfile.usable(e.mimeData()) and self.win.current_shortcut() is not None:
            e.acceptProposedAction()
            self.file_btn.set_drop_hot(True)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.file_btn.set_drop_hot(False)

    def dropEvent(self, e):
        self.file_btn.set_drop_hot(False)
        src = localfile.source(e.mimeData())
        if src is None:
            e.ignore()
            return
        e.acceptProposedAction()
        self.win.stage_file(self.slot, **src)

    def _toggle_banner(self, on: bool):
        self.win.config.data["logo_on_banner"] = on
        self.win.config.save()
        self.show_current()
        self.win.page._banner()  # the logo on the page's own banner too

    # -- options -----------------------------------------------------------

    def set_message(self, text: str, icon: str = "info"):
        """What SteamGridDB has to say instead of options; Steam's own still show beside it."""
        self._sgdb = ("message", text, icon)
        self._render()

    def set_loading(self):
        """Options are on their way; results from any earlier request are now stale (see gen)."""
        self.gen += 1
        self._sgdb = ("loading",)
        self._render()

    def set_assets(self, assets: list[Asset], more: bool):
        self._sgdb = ("assets", assets, more)
        self._render()

    def set_steam(self, assets: list[Asset]):
        """Steam's official artwork for this piece (none, or one), shown first."""
        self.steam_assets = list(assets)
        self._render()

    def _render(self):
        kind = self._sgdb[0]
        sgdb = self._sgdb[1] if kind == "assets" else []
        if self.steam_assets or sgdb:
            self._show_assets(sgdb, kind == "assets" and self._sgdb[2])
        elif kind == "assets":
            self._show_message(f"No {self.slot.label.lower()} on SteamGridDB yet.")
        elif kind == "loading":
            self._show_message("Loading options from SteamGridDB…", "")
        else:
            self._show_message(self._sgdb[1], self._sgdb[2])

    def _show_message(self, text: str, icon: str = "info"):
        self._clear_strip()
        self.count.setText("")
        self.count.hide()
        self.see_all.hide()
        self.scroll.hide()
        self.message.setText(text, icon)
        self.message.show()
        self._right.setStretch(0, 1)

    def _show_assets(self, sgdb: list[Asset], more: bool):
        self._clear_strip()
        steam = self.steam_assets
        self.assets = (steam + sgdb)[:STRIP]
        n = len(sgdb)
        self.message.hide()
        self._right.setStretch(0, 0)
        self.count.show()
        self.scroll.show()
        self.total = len(steam) + (n if not more else 50)
        shown = f"{n}+" if more else str(n)
        parts = []
        if steam:
            parts.append("<b>Official</b> from Steam")
        kind = self._sgdb[0]
        if kind == "assets" and n:
            parts.append(f"<b>{shown} option{'s' if n != 1 else ''}</b> on SteamGridDB")
        elif kind == "assets":
            parts.append("none on SteamGridDB yet")
        elif kind == "loading":
            parts.append("loading SteamGridDB…")
        self.count.setText(" · ".join(parts))
        self.count.setToolTip("")
        if kind == "message":
            self.count.setToolTip(self._sgdb[1])
        total = f"{self.total}+" if more else str(self.total)
        self.see_all.setText(f"See all {total}")
        self.see_all.show()
        info = INFO[self.slot.key]
        for i, a in enumerate(self.assets):
            v = OptionView(info.thumb, "", checker=self.slot.key == "logo")
            v.animated = a.animated
            v.official = isinstance(a, SteamAsset)
            v.describe(f"{self.slot.label} option {i + 1} of {len(self.assets)}, "
                       + ("Steam's official artwork" if v.official else f"by {a.author or 'unknown'}")
                       + (", animated" if a.animated else "") + (f", {a.style}" if a.style else ""))
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
        # Moving from one option to the next doesn't flash the piece back in between either.
        self._rest_view = view if on else None
        self._rest.start()

    def _preview_now(self):
        view = self._rest_view
        if view is None:
            if self._previewing:
                self._previewing = False
                self.show_current()
            return
        if not view.pixmap.isNull():
            backdrop = self.art.backdrop if self.slot.key == "logo" else None
            self.art.new = False
            self.art.animated = view.animated
            asset = next((a for v, a in zip(self.thumbs, self.assets) if v is view), None)
            self.art.set_motion(*self.win.motion_spec(asset, self.art.size(), crop=not self.art.checker))
            self.art.set_pixmap(view.pixmap, backdrop)
            self._set_caption("Previewing", "preview")
            self._previewing = True

    def _clear_strip(self):
        self._rest.stop()
        self._rest_view = None
        had = bool(self.thumbs)
        for v in self.thumbs:
            self.strip.removeWidget(v)
            v.drop_motion()  # its decode, if still waiting, never runs
            v.hide()
            v.deleteLater()
        self.thumbs = []
        self.assets = []
        if had:
            self.thumb_loaded.emit()


class CoverThumb(QWidget):
    """The 40 × 60 cover on the match card: the first cover option, else a plain block."""

    def __init__(self, parent=None, size: QSize = QSize(40, 60)):
        super().__init__(parent)
        self.setFixedSize(size)
        self.pixmap: QPixmap | None = None
        self.glyph = ""  # an icon name drawn on the plain block, e.g. "key"
        self._scaled: tuple | None = None  # (pixmap key, dpr, the pixmap cropped to fit)

    def set_pixmap(self, pm: QPixmap | None, glyph: str = ""):
        if glyph == self.glyph and (pm is self.pixmap or pm is not None and self.pixmap is not None
                                    and pm.cacheKey() == self.pixmap.cacheKey()):
            return  # the same picture (a refresh): keep it scaled
        self.pixmap = pm
        self.glyph = glyph
        self._scaled = None
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
            key = (self.pixmap.cacheKey(), dpr)
            if self._scaled is None or self._scaled[0] != key:
                self._scaled = (key, fill_rect(self.pixmap, self.size(), dpr))
            p.drawPixmap(0, 0, self._scaled[1])
        else:
            g = QLinearGradient(QPointF(0, 0), QPointF(self.width() * 0.4, self.height()))
            g.setColorAt(0, q("raised"))
            g.setColorAt(1, QColor("#141a22"))
            p.fillRect(self.rect(), g)
            if self.glyph:
                icons.draw(p, self.glyph, QRect((self.width() - 18) // 2, (self.height() - 18) // 2, 18, 18), q("text3"))


class _Layer(QObject):
    """One animated picture on the match card (its banner, logo or cover), played only while
    the card is on show; `on_frame` takes each picture as it's due."""

    def __init__(self, card: QWidget, on_frame):
        super().__init__(card)
        self.card, self.on_frame = card, on_frame
        self.key = None
        self.m = None

    def set(self, key=None, factory=None) -> bool:
        """Play this (`factory()` makes the Motion; no key stops). False when it's already playing."""
        if key is not None and key == self.key:
            return False
        if self.m is not None:
            self.m.retire()  # nothing it was still decoding reaches the card
            self.m = None
        self.key = key if factory is not None else None
        if self.key is not None:
            self.m = factory()
            self.m.frame.connect(self.on_frame)
            if self.card.isVisible():
                self.m.start()
        return True

    def start(self):
        if self.m is not None:
            self.m.start()

    def stop(self):
        if self.m is not None:
            self.m.stop()


BANNER_H = 210
# Frames kept for the page's own banner: it's shown big, so its frames are big, and a
# smaller budget would drop so many that it stutters. Still one decode at a time.
CARD_BUDGET = 96 * 1024 * 1024
COMPACT_GAP = 12  # above the buttons when they move to a row of their own (the banner grows to fit)
INFO_MIN = 280  # the least room for the name and match line beside the buttons
# Colour sweeps for a game with no banner yet (dark, deep, bright, light), picked by name.

BLANK_BANNERS = (
    ("#0e1030", "#3a3f9a", "#b06ad2", "#ffc8e6"),  # violet to pink
    ("#0b1d2e", "#145a8a", "#1a9fff", "#9fe3ff"),  # Steam blue
    ("#1a0e12", "#7a1a4a", "#ff5a3c", "#ffb43c"),  # ember
    ("#08201c", "#0e5a4a", "#3cd28c", "#d6ffb0"),  # jade
    ("#1c0e2a", "#5a1f8a", "#ff5ab0", "#ffd0a0"),  # sunset
    ("#101a2a", "#2a4a7a", "#6a7cff", "#e0c8ff"),  # dusk
    ("#201406", "#7a4a0e", "#e3a33c", "#fff0b0"),  # gold
    ("#0a1a24", "#0e4a5a", "#2ab8c8", "#ffb0c8"),  # lagoon
)


class MatchCard(QWidget):
    """The top of a game page, like a Steam game page: the game's banner fading into the page,
    with its cover, its name in Steam, which SteamGridDB game the artwork comes from, and the
    page's buttons (Get All Artwork, Apply)."""

    confirm = Signal()
    change = Signal()
    add_key = Signal()
    home = Signal()
    get_all = Signal()
    apply = Signal()
    rename = Signal()
    unrename = Signal()
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "finding"
        self.banner: QPixmap | None = None
        self.blurred = False
        self._scaled: tuple | None = None
        self.logo: QPixmap | None = None  # laid over the banner, as on Steam's game page
        self._logo_scaled: tuple | None = None
        self.layers = {"banner": _Layer(self, self._banner_frame), "logo": _Layer(self, self._logo_frame),
                       "cover": _Layer(self, lambda pm: self.cover.set_pixmap(pm))}
        self.setFixedHeight(BANNER_H)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 10, 24, 16)  # in line with the rows below
        outer.setSpacing(0)
        self.back = QPushButton("Library")
        self.back.setObjectName("bannerback")
        self.back.setIcon(icons.icon("back", 14, "white"))
        self.back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back.setToolTip("Back to all games")
        self.back.setAccessibleName("Library: back to all games")
        self.back.clicked.connect(self.home.emit)
        outer.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.cover = CoverThumb(size=QSize(72, 108))
        # Shadows are painted by the card: a graphics effect would make the whole page
        # repaint on every scroll step instead of sliding, which stutters.
        row.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignBottom)
        info = QVBoxLayout()
        info.setSpacing(4)
        info.addStretch(1)
        self.title = _Elided()  # a long name ends in "…" rather than widening the page
        f = QFont(self.font())
        f.setPointSizeF(theme.pt("title") * 0.95)
        f.setWeight(QFont.Weight.Bold)
        self.title.setFont(f)
        self.title.setStyleSheet("color: white; background: transparent;")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.title)
        # The match line wraps when it's narrow; "Change Game" is a link at its end.
        self.meta = QLabel()
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.meta.setWordWrap(True)
        pol = self.meta.sizePolicy()
        pol.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.meta.setSizePolicy(pol)
        self.meta.setStyleSheet("background: transparent;")
        self.meta.setContentsMargins(0, 4, 0, 5)  # where the old one-line row with a button put it
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                          | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.meta.linkActivated.connect(lambda href: (self.confirm if href == "confirm" else self.change).emit())
        self._match = ("", "", 0)
        self._steam = ("off", "")
        info.addWidget(self.meta)
        row.addLayout(info, 1)

        self.acts = QWidget()  # beside the name, or on a row of its own when narrow (see _fit)
        acts = QHBoxLayout(self.acts)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(8)
        self.key_btn = QPushButton("Add API Key")
        self.key_btn.setProperty("kind", "confirm")
        self.key_btn.setAccessibleDescription("Add a free SteamGridDB API key")
        self.key_btn.clicked.connect(self.add_key.emit)
        acts.addWidget(self.key_btn)
        # A game stuck with a title like "game.exe" is offered its SteamGridDB name.
        self.rename_btn = QPushButton()
        self.rename_btn.setProperty("kind", "confirm")
        self.rename_btn.clicked.connect(self.rename.emit)
        self.rename_btn.hide()
        acts.addWidget(self.rename_btn)
        self.unrename_btn = QPushButton("Keep Old Name")
        self.unrename_btn.setObjectName("bannerghost")
        self.unrename_btn.clicked.connect(self.unrename.emit)
        self.unrename_btn.hide()
        acts.addWidget(self.unrename_btn)
        self.get_btn = QPushButton("Get All Artwork")
        self.get_btn.setObjectName("bannerget")  # or "bannerghost" while something else comes first (_style_get)
        self.get_btn.setIcon(icons.icon("download", 15, "white"))
        self.get_btn.clicked.connect(self.get_all.emit)
        acts.addWidget(self.get_btn)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setProperty("kind", "go")
        self.apply_btn.clicked.connect(self.apply.emit)
        acts.addWidget(self.apply_btn)
        row.addWidget(self.acts, 0, Qt.AlignmentFlag.AlignBottom)
        self.row, self.outer = row, outer
        self.compact = False
        outer.addLayout(row)
        self.setStyleSheet(f"""
            #bannerback {{ background: transparent; color: white; font-weight: 600; padding: 0 6px;
                           min-height: 28px; text-align: left; }}
            #bannerback:hover {{ background: rgba(0,0,0,.25); }}
            #bannerghost {{ background: rgba(23,26,33,.72); color: {C['text']}; }}
            #bannerghost:hover {{ background: rgba(61,76,94,.9); }}
            #bannerghost:disabled {{ background: rgba(23,26,33,.5); color: {C['text3']}; }}
            #bannerget {{ background: {C['get']}; color: white; }}
            #bannerget:hover {{ background: {C['get_hover']}; }}
            #bannerget:disabled {{ background: rgba(23,26,33,.5); color: {C['text3']}; }}
            QPushButton[kind="go"]:disabled {{ background: rgba(23,26,33,.5); color: {C['text3']}; }}
        """)

    def set_game(self, name: str):
        """The game's own name, as it shows in Steam."""
        self.title.setText(name)
        self.title.setAccessibleName(name)
        self.update()  # a game with no banner gets its own colours

    def set_rename(self, old: str, suggest: str | None, staged: bool):
        """Offer to retitle a game called `old` to `suggest`, or (`staged`) to take that back."""
        self.unrename_btn.setVisible(staged)
        self.unrename_btn.setToolTip(f"Keep the name “{old}”")
        self.unrename_btn.setAccessibleDescription(f"Keep the name {old} instead")
        self.rename_btn.setVisible(bool(suggest) and not staged)
        if suggest:
            short = QFontMetricsF(self.rename_btn.font()).elidedText(suggest, Qt.TextElideMode.ElideRight, 220)
            self.rename_btn.setText(f"Rename to {short}")
            self.rename_btn.setToolTip(f"“{old}” doesn't say which game this is. "
                                       f"Steam will call it “{suggest}” once you apply.")
            self.rename_btn.setAccessibleDescription(f"Rename {old} to {suggest} in Steam when you apply")
        self._fit()

    def set_actions(self, can_get: bool, changes: int, busy: bool):
        self.get_btn.setEnabled(can_get and not busy)
        self.apply_btn.setText(f"Apply ({changes} Change{'' if changes == 1 else 's'})" if changes else "Apply")
        self.apply_btn.setEnabled(bool(changes) and not busy)
        self._style_get()
        self._fit()

    def _style_get(self):
        """One filled button at a time: Get All Artwork is green only when it's the one thing to do,
        and a quiet ghost while there are changes to apply or a match to check."""
        name = "bannerghost" if self.apply_btn.isEnabled() or self.state in ("check", "key") else "bannerget"
        if self.get_btn.objectName() != name:
            self.get_btn.setObjectName(name)
            self.get_btn.style().unpolish(self.get_btn)
            self.get_btn.style().polish(self.get_btn)

    def _fit(self):
        """Put the buttons on a row of their own, under the cover and name, when they don't fit beside them."""
        m = self.outer.contentsMargins()
        need = m.left() + m.right() + self.cover.width() + 2 * self.row.spacing() + INFO_MIN + self.acts.sizeHint().width()
        self.set_compact(self.width() < need)

    def set_compact(self, compact: bool):
        if compact == self.compact:
            return
        self.compact = compact
        if compact:
            self.row.removeWidget(self.acts)
            self.acts.layout().setContentsMargins(0, COMPACT_GAP, 0, 0)
            self.outer.addWidget(self.acts, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self.outer.removeWidget(self.acts)
            self.acts.layout().setContentsMargins(0, 0, 0, 0)
            self.row.addWidget(self.acts, 0, Qt.AlignmentFlag.AlignBottom)
        self.setFixedHeight(BANNER_H + (COMPACT_GAP + self.apply_btn.sizeHint().height() if compact else 0))

    def set_banner(self, pm: QPixmap | None, blurred: bool = False, spec=(None, None)):
        """The game's banner, or (blurred) its cover when it has no banner to show yet. `spec`
        (key, factory) plays an animated banner; the same one again carries on playing."""
        if not self.layers["banner"].set(*spec) and self.banner is not None:
            return  # playing on: keep the frame on show
        if pm is self.banner and blurred == self.blurred:
            return
        self.banner, self.blurred, self._scaled = pm, blurred, None
        self.update()

    def _banner_frame(self, pm: QPixmap):
        self.banner, self.blurred, self._scaled = pm, False, None
        self.update()

    def set_logo(self, pm: QPixmap | None, spec=(None, None)):
        if not self.layers["logo"].set(*spec) and self.logo is not None:
            return
        self._logo_frame(pm)

    def _logo_frame(self, pm: QPixmap | None):
        self.logo, self._logo_scaled = pm, None
        self.update()

    def set_cover(self, pm: QPixmap | None, glyph: str = "", spec=(None, None)):
        if not self.layers["cover"].set(*spec) and self.cover.pixmap is not None:
            return
        self.cover.set_pixmap(pm, glyph)

    def showEvent(self, e):
        for layer in self.layers.values():
            layer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        for layer in self.layers.values():
            layer.stop()  # nothing plays where it can't be seen
        super().hideEvent(e)

    def _logo_box(self) -> QRectF:
        """Where the logo goes: the right of the banner, between the back button and the buttons."""
        top = self.back.geometry().bottom() + 10
        bottom = self.acts.mapTo(self, QPoint(0, 0)).y() - 12 if self.acts.isVisible() else self.height() - 70
        bottom = min(bottom, self.cover.mapTo(self, QPoint(0, 0)).y() + self.cover.height())
        w = self.width() * 0.4
        return QRectF(self.width() - 24 - w, top, w, max(24, bottom - top))

    def _logo_art(self) -> tuple[QRectF, QPixmap] | None:
        if self.logo is None or self.logo.isNull() or self.blurred or self.banner is None:
            return None
        box = self._logo_box()
        dpr = self.devicePixelRatioF()
        key = (self.logo.cacheKey(), box.size().toSize(), dpr)
        if self._logo_scaled is None or self._logo_scaled[0] != key:
            pm = self.logo.scaled(box.size().toSize() * dpr, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self._logo_scaled = (key, pm)
        pm = self._logo_scaled[1]
        w, h = pm.width() / dpr, pm.height() / dpr
        return QRectF(box.right() - w, box.center().y() - h / 2, w, h), pm

    def set_state(self, state: str, name: str, year: str = "", candidates: int = 0):
        """state: "exact", "check", "notfound", "finding" or "key" (no API key yet)."""
        self.state = state
        self._match = (name, year, candidates)
        self._render_meta()
        self._style_get()
        self._fit()
        self.update()

    def set_steam(self, state: str, name: str = ""):
        """Steam's official artwork: state "found" (from the store game `name`), "none", "finding",
        "error" or "off". It shares the match line, so one Change Game covers both."""
        self._steam = (state, name)
        self._render_meta()

    def _render_meta(self):
        from ..steamstore import norm_name

        state = self.state
        name, year, candidates = self._match
        steam = self._steam[1] if self._steam[0] == "found" else ""
        yr = f"&nbsp;<span style='color:{C['text2']}'>{escape(str(year))}</span>" if year else ""
        game = f"<b style='color:white'>{escape(name)}</b>{yr}"
        on_steam = f"<b style='color:white'>{escape(steam)}</b> on Steam"
        same = steam and norm_name(steam) == norm_name(name)
        exact = f"Artwork from {game} on SteamGridDB"
        if steam:
            exact += " and Steam" if same else f" and {on_steam}"
        text, colour = {
            "exact": (exact + "&nbsp;·", C["text2"]),
            "check": ((f"We picked {game} from {candidates} similar names. Is it right?" if candidates > 1
                       else f"We picked {game}, the closest name we found. Is it right?") + "&nbsp;·", WAIT),
            "notfound": ((f"Official art from {on_steam}, not found on SteamGridDB&nbsp;·" if steam
                          else "Not found on SteamGridDB&nbsp;·"), C["text2"] if steam else C["bad"]),
            "finding": ("Finding this game on SteamGridDB…", C["text2"]),
            "key": ((f"Official art from {on_steam}. Add a free SteamGridDB API key for more&nbsp;·" if steam
                     else "Add a free SteamGridDB API key to find this game's artwork."), C["text2"]),
        }[state]
        link = ""
        if state not in ("finding", "key") or steam:
            words = ("Search by Another Name" if state == "notfound" and not steam else "Change Game").replace(" ", "&nbsp;")
            a = f"<a style='color:{C['blue_ink']}; text-decoration:none; font-weight:600'"
            if state == "check":  # a quiet link, so Apply or Get All Artwork stays the one filled button
                link = f"&nbsp;&nbsp;{a} href='confirm'>Yes,&nbsp;That's&nbsp;It</a>&nbsp;&nbsp;·"
            link += f"&nbsp;&nbsp;{a} href='change'>{words}</a>"
        self.meta.setText(f"<span style='color:{colour}'>{text}</span>{link}")
        self.key_btn.setVisible(state == "key")
        plain = {"exact": f"Artwork from {name} on SteamGridDB" + (" and Steam" if steam else ""),
                 "check": f"Check this match: {name}",
                 "notfound": "Not found on SteamGridDB", "finding": "Finding this game on SteamGridDB",
                 "key": "SteamGridDB API key needed"}[state]
        self.setAccessibleName(plain)

    def resizeEvent(self, e):
        self._scaled = None
        self._logo_scaled = None
        super().resizeEvent(e)
        self._fit()
        self.resized.emit()

    def _art(self) -> QPixmap | None:
        if self.banner is None or self.banner.isNull():
            return None
        dpr = self.devicePixelRatioF()
        key = (self.size(), dpr)
        if self._scaled is None or self._scaled[0] != key:
            if self.blurred:  # shrink then stretch: a cheap, soft blur
                small = fill_rect(self.banner, QSize(48, max(1, round(48 * self.height() / max(1, self.width())))),
                                  top=True)
                pm = small.scaled(self.size() * dpr, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
            else:
                pm = fill_rect(self.banner, self.size(), dpr, top=True)  # Steam keeps the top of a banner
            self._scaled = (key, pm)
        return self._scaled[1]

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, q("ground"))
        art = self._art()
        if art is not None:
            p.setOpacity(0.75 if self.blurred else 1.0)
            p.drawPixmap(0, 0, art)
            p.setOpacity(1.0)
        else:
            # No artwork yet: a bright sweep of colour, the same one for a game every time.
            sweep = BLANK_BANNERS[zlib.crc32(self.title.text().encode()) % len(BLANK_BANNERS)]
            a = 110 * math.pi / 180  # CSS-style 110deg, like the mockup
            dx, dy = math.sin(a) * r.width() / 2, -math.cos(a) * r.height() / 2
            g = QLinearGradient(r.center() - QPointF(dx, dy), r.center() + QPointF(dx, dy))
            for at, colour in zip((0, 0.4, 0.75, 1), sweep):
                g.setColorAt(at, QColor(colour))
            p.fillRect(r, g)
        side = QLinearGradient(r.topLeft(), QPointF(r.width() * 0.65, 0))
        side.setColorAt(0, QColor(0, 0, 0, 120))
        side.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(r, side)
        stage = q("ground")  # the page behind the rows
        fade = QLinearGradient(r.topLeft(), r.bottomLeft())
        for at, alpha in ((0, 0), (0.35, 40), (0.7, 170), (1, 255)):
            c = QColor(stage)
            c.setAlpha(alpha)
            fade.setColorAt(at, c)
        p.fillRect(r, fade)
        logo = self._logo_art()
        if logo is not None:
            p.drawPixmap(logo[0].topLeft(), logo[1])
        cv = QRectF(self.cover.geometry())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(12, 0, -2):  # the cover's soft shadow, as fading rings
            p.setBrush(QColor(0, 0, 0, round(150 * (1 - i / 12) ** 2)))
            p.drawRoundedRect(cv.adjusted(-i, -i + 6, i, i + 6), 3 + i, 3 + i)
        edge = {"check": QColor(WAIT), "key": q("blue"), "notfound": QColor(C["bad"])}.get(self.state)
        if edge is not None:  # a thin coloured line along the top when something needs you
            p.fillRect(QRectF(0, 0, r.width(), 3), edge)


class GamePage(QWidget):
    fill = Signal()  # kept for compatibility; the banner header has the buttons now
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
        top = QVBoxLayout(body)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)

        self.card = MatchCard()
        self.card.confirm.connect(self.confirm_match.emit)
        self.card.change.connect(self.change_match.emit)
        self.card.add_key.connect(win.ask_api_key)
        self.card.home.connect(win.go_home)
        self.card.get_all.connect(win.fill_current)
        self.card.apply.connect(lambda: win.apply_games([win.current_shortcut().appid]))
        self.card.rename.connect(win.stage_rename)
        self.card.unrename.connect(win.unstage_rename)
        # After a resize settles, an animated banner decodes again at the new size.
        self._resize_timer = QTimer(self, singleShot=True, interval=300)
        self._resize_timer.timeout.connect(self._card_resized)
        self.card.resized.connect(self._resize_timer.start)
        top.addWidget(self.card)

        lay = QVBoxLayout()
        lay.setContentsMargins(24, 6, 24, 24)
        lay.setSpacing(0)
        top.addLayout(lay)

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
        self.rows["hero"].thumb_loaded.connect(self._banner)
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
        if not self.win.config.api_key:
            self.card.set_state("key", sc.name or "(unnamed)")
        elif status == "notfound" and not m:
            self.card.set_state("notfound", sc.name or "(unnamed)")
        elif m:
            check = status == "check"
            self.card.set_state("check" if check else "exact", m["name"], m.get("year") or "",
                                self.win.match_candidates(sc) if check else 0)
        else:
            self.card.set_state("finding", sc.name or "(unnamed)")
        self.card.set_game(self.win.shown_name(sc))
        staged = self.win.pending.name(sc.appid) is not None
        self.card.set_rename(sc.name, self.win.rename_suggestion(sc), staged)
        self._card_cover()

    def _card_cover(self):
        """The cover beside the name: the game's cover, its waiting pick, else the first option."""
        sc = self.win.current_shortcut()
        pm = self._current_art(sc, "grid") if sc else None
        spec = self._motion(sc, "grid", self.card.cover.size()) if pm is not None else (None, None)
        if pm is None and (self.card.state in ("exact", "check") or self.rows["grid"].steam_assets):
            pm = self.rows["grid"].first_thumb()
        self.card.set_cover(pm, "key" if self.card.state == "key" else "", spec)
        self._banner()

    def _current_art(self, sc, key: str) -> QPixmap | None:
        """What the game will have for this piece once applied: a waiting pick, else what's set now."""
        pick = self.win.pending.get(sc.appid, key)
        if pick is not None:
            return None if pick.clears else self.win.pick_pixmap(sc, key)
        path = self.win.slot_path(sc, SLOTS[key])
        pm = load_pixmap(path) if path else None
        return pm if pm is not None and not pm.isNull() else None

    def _banner(self):
        """The game's banner (or its waiting pick), else its cover blurred, else a colour sweep:
        only artwork the game will have, never an option it hasn't picked."""
        sc = self.win.current_shortcut()
        if sc is None:
            return
        pm = self._current_art(sc, "hero")
        if pm is None:
            self.card.set_logo(None)
            return self.card.set_banner(self._current_art(sc, "grid"), blurred=True)
        self.card.set_banner(pm, spec=self._motion(sc, "hero", self.card.size()))
        # Its logo over it, as Steam's game page has it (unless the Logo row says not to).
        if self.win.config.data.get("logo_on_banner", True):
            logo = self._current_art(sc, "logo")
            self.card.set_logo(logo, self._motion(sc, "logo", QSize(self.card.width() * 2 // 5, 140)) if logo else (None, None))
        else:
            self.card.set_logo(None)

    def _motion(self, sc, key: str, size: QSize) -> tuple:
        """(key, factory) to play this piece where it's animated (a waiting pick, else what's on
        disk), for the match card; nothing for a still or with animation turned off.

        It decodes the full file at the size it's shown. The banner's frames are made exactly
        the card's size in device pixels, cropped as it's drawn, so they're drawn pixel for
        pixel (no second scaling to blur them), and a banner too big to hold every frame of
        streams rather than dropping frames. After a resize (see _card_resized) it decodes
        again, sharp at the new size, and on a screen of another scale. The cover is a fixed
        size, made to measure; the logo's box rounds up so it carries on across small resizes."""
        if not self.win.show_animated:
            return None, None
        hero = key == "hero"
        if key == "logo":
            size = QSize(-(-size.width() // 160) * 160, -(-size.height() // 40) * 40)
        how = ({"budget": CARD_BUDGET, "crop": True, "stream": True} if hero
               else {"crop": "middle"} if key == "grid" else {})
        pick = self.win.pending.get(sc.appid, key)
        if pick is not None:
            asset = getattr(pick, "asset", None)
            spec = (None, None) if pick.clears else self.win.full_motion_spec(asset, size, **how)
        else:
            spec = self.win.file_motion_spec(self.win.slot_path(sc, SLOTS[key]), size, **how)
        dpr = round(self.devicePixelRatioF() * 100)
        return (None, None) if spec[0] is None else ((spec[0], size.width(), size.height(), dpr), spec[1])

    def showEvent(self, e):
        super().showEvent(e)
        handle = self.window().windowHandle()
        if handle is not None and not getattr(self, "_screen_hooked", False):
            # Moved to a screen of another scale: the card's animations decode again, sharp there.
            self._screen_hooked = True
            handle.screenChanged.connect(lambda *_: self._resize_timer.start())

    def _card_resized(self):
        if self.isVisible() and self.win.current_shortcut() is not None:
            self._banner()

    def refresh_current(self):
        for row in self.rows.values():
            row.show_current()
        self._card_cover()

    def set_all_messages(self, text: str):
        for row in self.rows.values():
            row.set_message(text)

    def show_key_prompt(self):
        self.update_match()
        for row in self.rows.values():
            row.set_message("Options show once a key is added.", "key")
