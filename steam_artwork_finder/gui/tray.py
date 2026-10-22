"""The See all panel: Steam's official piece and every SteamGridDB option for one piece, sliding in
over the game page.

It opens on the piece whose "See all" was clicked. Hovering an option previews it on the
page behind; clicking one picks it. Options on screen are drawn from the full-size
artwork (shrunk as it decodes) and animated ones play their whole animation, looped."""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QImage, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..artwork import SLOTS
from . import icons, motion, theme
from .scrim import scrim_of
from .theme import q
from ..steamstore import SteamAsset
from .gamepage import describe, draw_new_tag, draw_steam_tag
from .widgets import _cache as pixmap_cache
from .widgets import (FlowLayout, _largest_frame, draw_animated_tag, draw_badge, draw_checker, draw_failed, draw_loading,
                      fade, fill_rect, label, pulse, retry_text)
from . import workers

ROLE_ASSET = Qt.ItemDataRole.UserRole
ROLE_PIX = Qt.ItemDataRole.UserRole + 1
ROLE_INUSE = Qt.ItemDataRole.UserRole + 2
ROLE_PICK = Qt.ItemDataRole.UserRole + 3  # the waiting (not applied) pick
ROLE_NAME = Qt.ItemDataRole.UserRole + 4  # what a screen reader says, before "picked" / "in use"
ROLE_FULL = Qt.ItemDataRole.UserRole + 5  # the option drawn from its full-size artwork, once loaded
ROLE_LOAD = Qt.ItemDataRole.UserRole + 6  # ("loading", retry n) or ("failed", retry fn) while it has no picture

# The grid is as wide as four covers; each piece fills a row with this many options.
COLUMNS = {"grid": 4, "wide": 3, "hero": 2, "logo": 3, "icon": 6}
SHAPE = {"grid": 900 / 600, "wide": 430 / 920, "hero": 620 / 1920, "logo": 70 / 150, "icon": 1.0}  # height / width
CELL_PAD = 8 + 4 * 2  # around each option: the delegate's margin and the list's spacing
GRID_W = 4 * (108 + CELL_PAD)  # four 108 px covers
ANIM_BUDGET = 12 * 1024 * 1024  # bytes of frames one playing option may keep (up to MAX play at once)


def thumb_size(slot_key: str) -> QSize:
    w = GRID_W // COLUMNS[slot_key] - CELL_PAD
    return QSize(w, round(w * SHAPE[slot_key]))


def decode_fit(path: str, w: int, h: int, cover: bool) -> QImage:
    """Worker: an image file shrunk as it decodes to cover (or, for logos, fit in) `w`×`h`,
    keeping its shape. Only the first frame of an animation."""
    reader = QImageReader(path)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    _largest_frame(reader)
    size = reader.size()
    if size.isValid() and w > 0 and h > 0:
        pick = max if cover else min
        scale = pick(w / size.width(), h / size.height())
        if scale < 1:
            reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
    return reader.read()


_full: dict[tuple, QPixmap] = {}  # (asset id, w, h) -> full-quality picture, newest last
FULL_KEEP = 160


def _best(full: QPixmap | None, thumb: QPixmap | None) -> QPixmap | None:
    return full if full is not None and not full.isNull() else thumb


def _full_for(asset_id: int) -> QPixmap | None:
    """The sharpest full-quality picture of an option loaded so far, if any."""
    best = None
    for (aid, w, h), pm in _full.items():
        if aid == asset_id and (best is None or pm.width() * pm.height() > best.width() * best.height()):
            best = pm
    return best


def style_name(style: str) -> str:
    """SteamGridDB's style key as people read it: "white_logo" -> "White logo"."""
    return style.replace("_", " ").capitalize()

class OptionDelegate(QStyledItemDelegate):
    def __init__(self, tray):
        super().__init__()
        self.tray = tray
        self._scaled: dict[tuple, QPixmap] = {}  # thumbnails scaled for the tray, so a repaint only draws
        self._shimmered: set[int] = set()  # options painted as loading, whose picture then fades in
        self._since: dict[int, float] = {}  # option -> when its picture arrived, while it fades in

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
        p.setClipRect(option.rect)  # nothing an option draws reaches past its own cell
        # Clear the whole cell first: a caption from an earlier layout (before filtering, say)
        # mustn't show through the new one.
        p.fillRect(option.rect, q("tray"))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = thumb_size(self.tray.slot.key)
        r = QRectF(option.rect.x() + 4, option.rect.y() + 4, s.width(), s.height())
        clip = QPainterPath()
        clip.addRoundedRect(r, 3, 3)
        p.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        pm = _best(index.data(ROLE_FULL), index.data(ROLE_PIX))
        a = index.data(ROLE_ASSET)
        live = self.tray.playing.frame(a)  # an animated option playing: its frame now
        logo = self.tray.slot.key == "logo"
        if live is not None:
            pm = live
        has = pm is not None and not pm.isNull()
        load = None if has else index.data(ROLE_LOAD)
        aid = a.id if a is not None else None
        shown = 1.0
        if has and aid in self._shimmered:  # seen loading: its picture fades in
            self._shimmered.discard(aid)
            self._since[aid] = time.monotonic()
        if has and aid in self._since:
            shown = fade(self._since[aid])
            if shown >= 1.0:
                del self._since[aid]
        if load is not None and load[0] == "failed":
            draw_failed(p, r, hot=bool(option.state & QStyle.StateFlag.State_MouseOver))
        elif not has or shown < 1.0:
            # Still on its way (or just arrived and fading in): the tile shimmers.
            if not has:
                self._shimmered.add(aid)
            draw_loading(p, r, pulse().phase(), QColor("#1f2a38") if logo else None)
            pulse().want(self.tray.list.viewport(), option.rect)
            if has and logo:
                draw_checker(p, r)
        elif logo:
            draw_checker(p, r)
        else:
            p.fillRect(r, q("raised"))
        if has:
            p.setOpacity(shown)
            dpr = p.device().devicePixelRatioF()
            # A live frame comes ready to draw (see _Playing._frame); the rest are scaled once and kept.
            sp = live if live is not None else self.scaled(pm, s, logo, dpr)
            if logo:
                w, h = sp.width() / dpr, sp.height() / dpr
                p.drawPixmap(QPointF(r.x() + (s.width() - w) / 2, r.y() + (s.height() - h) / 2), sp)
            else:
                p.drawPixmap(r.topLeft(), sp)
            p.setOpacity(1.0)
        p.setClipRect(option.rect)
        picked = bool(index.data(ROLE_PICK))
        if picked:
            draw_new_tag(p, r)
        elif index.data(ROLE_INUSE):
            draw_badge(p, r)
        a = index.data(ROLE_ASSET)
        official = isinstance(a, SteamAsset)
        if official and not picked:
            draw_steam_tag(p, r)
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
        fm = QFontMetrics(f)
        cap = "Official, from Steam" if official else f"by {a.author or 'unknown'}"
        if not official and a.style and fm.horizontalAdvance(f"{cap} · {style_name(a.style)}") <= s.width():
            cap += f" · {style_name(a.style)}"  # the style only when it fits whole: the author comes first
        if load is not None and load[0] == "failed":
            cap = "Couldn't load · Retry"
            p.setPen(q("blue_ink"))
        elif load is not None and load[1]:
            cap = retry_text(load[1], s.width(), fm) or cap
            p.setPen(q("warn"))
        cap = fm.elidedText(cap, Qt.TextElideMode.ElideRight, s.width())
        p.drawText(QRectF(r.left(), r.bottom() + 4, s.width(), QFontMetrics(f).height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cap)
        p.restore()


class _Playing(QObject):
    """Plays the animated options the panel has on screen, and sharpens the still ones.

    An option in view loads its full-size artwork: a still is decoded shrunk to fit (kept
    in `_full`), and an animation is decoded whole, every frame shrunk as it decodes,
    through motion's decode pool, then looped. Until that's ready an animated option plays
    SteamGridDB's small preview. Scrolled away, filtered out, or the panel closed, and it
    stops and lets go of its frames. Nothing loads while the list scrolls: only the options
    in view once it has settled, so ones merely passed over cost nothing."""

    MAX = 20  # at most this many play at once: a full view of covers is four rows of four

    def __init__(self, tray):
        super().__init__(tray)
        self.tray = tray
        # asset id -> {"m": Motion, "pm": latest frame, "src": what it plays, "next": the full one decoding}
        self.motions: dict[int, dict] = {}
        self._asked: set = set()  # full-size downloads or decodes already under way
        self.items: dict[int, QListWidgetItem] = {}  # asset id -> its option in the list
        self._thumbs: set[int] = set()  # animated options whose first picture was asked for
        self.timer = QTimer(self, interval=300)
        self.timer.timeout.connect(self.sweep)

    def reset(self):
        """The list was emptied."""
        self.stop_all()
        self.items.clear()
        self._thumbs.clear()

    def frame(self, a) -> QPixmap | None:
        e = self.motions.get(a.id) if a is not None else None
        return e["pm"] if e is not None else None

    def _in_view(self):
        """The options on screen, looking only near the visible rows rather than at every one."""
        lst = self.tray.list
        view = lst.viewport().rect()
        n = lst.count()
        if not n:
            return
        first = None
        cell = lst.sizeHintForRow(0) if n else 0
        step = max(8, view.width() // 12)
        for y in (view.top() + 1, view.top() + cell // 2, view.top() + cell):
            for x in range(view.left() + 1, view.right(), step):
                idx = lst.indexAt(QPoint(x, y))
                if idx.isValid() and (first is None or idx.row() < first):
                    first = idx.row()
            if first is not None:
                break
        start = first or 0
        back = COLUMNS[self.tray.slot.key]  # a row partly above the probes: step back a whole row
        while start > 0 and back > 0:
            start -= 1
            if not lst.item(start).isHidden():
                back -= 1
        for i in range(start, n):
            it = lst.item(i)
            if it.isHidden():
                continue
            r = lst.visualItemRect(it)
            if r.top() > view.bottom():
                break
            a = it.data(ROLE_ASSET)
            if a is not None and r.intersects(view):
                yield it, a

    def sweep(self):
        t = self.tray
        lst = t.list
        if not (t.isVisible() and lst.isVisible()):
            self.stop_all()
            return
        if t._settle.isActive():
            return  # mid-scroll: it sweeps again once the list settles
        win = t.win
        want: dict[int, tuple] = {}
        for it, a in self._in_view():
            if isinstance(a, SteamAsset):
                continue  # Steam's own piece is already its full picture
            if a.animated and it.data(ROLE_PIX) is None and a.id not in self._thumbs:
                self._thumbs.add(a.id)
                win.load_thumb(a, it)  # its preview file, and a first picture to stand still with
            if a.animated and win.show_animated:
                if len(want) >= self.MAX:
                    continue
                full = win.full_files.get(a.id)
                if full is None:
                    self._fetch_file(a)
                src = ("frames", full) if full is not None else win.motion_files.get(a.id)
                if src is not None:
                    want[a.id] = src
            elif it.data(ROLE_FULL) is None:
                self._sharpen(it, a)
        for aid in [k for k in self.motions if k not in want]:
            self._stop(aid)
        dpr = lst.devicePixelRatioF()
        size = thumb_size(t.slot.key)
        bound = QSize(round(size.width() * dpr), round(size.height() * dpr))
        for aid, src in want.items():
            e = self.motions.get(aid)
            if e is None:
                e = self.motions[aid] = {"m": None, "pm": None, "src": None, "next": None}
                self._play(aid, e, src, bound)
            elif src != e["src"] and (e["next"] is None or e["next"][0] != src):
                self._play(aid, e, src, bound)  # the full animation: swapped in once it shows a frame

    def _play(self, aid, e, src, bound):
        kind, path = src
        # Frames made to measure (cut as the delegate cuts), so they're drawn as they are; logos keep their shape.
        m = motion.make(kind, path, bound, ANIM_BUDGET, crop=False if self.tray.slot.key == "logo" else "middle")
        if e["m"] is None:
            e["m"], e["src"] = m, src
        else:
            if e["next"] is not None:
                e["next"][1].retire()
            e["next"] = (src, m)
        m.frame.connect(lambda pm, aid=aid, m=m: self._frame(aid, m, pm))
        m.start()

    def _frame(self, aid, m, pm):
        e = self.motions.get(aid)
        if e is None:
            return
        if e["next"] is not None and e["next"][1] is m:  # the full one is ready: it takes over
            old = e["m"]
            e["src"], e["m"] = e["next"]
            e["next"] = None
            old.retire()
        elif e["m"] is not m:
            return
        it = self.items.get(aid)
        if it is not None and it.data(ROLE_PIX) is None:
            it.setData(ROLE_PIX, pm)  # its first picture stands in when it stops
        # Scaled once as it comes, not on every repaint (a glide repaints every 16 ms).
        s = thumb_size(self.tray.slot.key)
        dpr = self.tray.list.devicePixelRatioF()
        if self.tray.slot.key == "logo":
            pm = pm.scaled(s * 0.86 * dpr, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
        else:
            pm = fill_rect(pm, s, dpr)
        e["pm"] = pm
        self._repaint(aid)

    def _repaint(self, aid):
        it = self.items.get(aid)
        if it is not None:  # repaint just that option
            lst = self.tray.list
            lst.viewport().update(lst.visualItemRect(it))

    def _fetch_file(self, a):
        key = ("file", a.id)
        win = self.tray.win
        if key in self._asked or a.id in win._full_loading:  # already on its way (for here or the page)
            return
        self._asked.add(key)

        def done(path, aid=a.id):
            win.full_files[aid] = path
            self._asked.discard(key)

        workers.run_retrying(workers.cached_file, a.url, win.sgdb.download, on_done=done,
                             on_error=lambda _m: None)  # stays on the preview; not asked again this session

    def _sharpen(self, it, a):
        """Load an option's full-size artwork, shrunk to the panel (or to the page's preview, if bigger)."""
        t = self.tray
        size = thumb_size(t.slot.key)
        row = t.win.page.rows.get(t.slot.key) if getattr(t.win, "page", None) else None
        if row is not None:
            art = row.art.size()
            if art.width() > size.width():
                size = QSize(art.width(), round(art.width() * SHAPE[t.slot.key]))
        dpr = t.list.devicePixelRatioF()
        w, h = round(size.width() * dpr), round(size.height() * dpr)
        cached = _full.get((a.id, w, h))
        if cached is not None:
            it.setData(ROLE_FULL, cached)
            return
        key = ("still", a.id, w, h)
        if key in self._asked:
            return
        self._asked.add(key)
        cover = t.slot.key != "logo"

        def decoded(img, aid=a.id):
            self._asked.discard(key)  # once _full lets it go, it can be loaded again
            if img.isNull():
                return
            pm = QPixmap.fromImage(img)
            _full[(aid, w, h)] = pm
            while len(_full) > FULL_KEEP:
                _full.pop(next(iter(_full)))
            item = self.items.get(aid)
            if item is not None:
                item.setData(ROLE_FULL, pm)

        def got(path):
            workers.run(decode_fit, path, w, h, cover, kind="cpu", on_done=decoded,
                        on_error=lambda _m: self._asked.discard(key))

        workers.run_retrying(workers.cached_file, a.url, t.win.sgdb.download, on_done=got,
                             on_error=lambda _m: None)  # one that still fails isn't asked for again this session

    def _stop(self, aid):
        e = self.motions.pop(aid, None)
        if e is not None:
            for m in (e["m"], e["next"][1] if e["next"] else None):
                if m is not None:
                    m.retire()

    def stop_all(self):
        for aid in list(self.motions):
            self._stop(aid)

    def start(self):
        self.timer.start()
        self.sweep()

    def stop(self):
        self.timer.stop()
        self.stop_all()


SLIDE_IN, SLIDE_OUT = 180, 140  # ms, with the fade (the app's other fades take 120 to 140)
SHADOW_W = 18


class _Shadow(QWidget):
    """A soft shadow down the tray's left edge, so it reads as lying over the page."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.strength = 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(self.width(), 0, 0, 0)
        g.setColorAt(0, QColor(0, 0, 0, round(110 * self.strength)))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g)


class _Ghost(QWidget):
    """A still picture of the tray that slides and fades in its place, so nothing inside the
    tray (its list, thumbnails still arriving, hover previews) lays out or repaints mid-slide."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.pm = QPixmap()
        self.opacity = 1.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setOpacity(self.opacity)
        p.drawPixmap(0, 0, self.pm)


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
        self.area: QWidget | None = None  # the part of the window it lies over (set by place_over)
        self._shadow: _Shadow | None = None
        self._ghost: _Ghost | None = None
        self._open = False  # shown, or sliding in
        self._swapping = False  # hiding the tray behind its ghost, not closing it
        self._entering = False  # filling or sliding in: thumbnails wait until it's in place
        self._held: list[tuple] = []  # (asset, item) whose thumbnails wait
        self._t = 1.0  # 0 = out of sight on the right, 1 = in place
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._settled)
        self.playing = _Playing(self)
        self._leaving = False
        self.setFixedWidth(self._width_for_covers())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.title = label("", "heading")
        head.addWidget(self.title)
        self.status = label("", "faint", wrap=True)  # how many options, or loading, or what went wrong
        head.addWidget(self.status, 1, Qt.AlignmentFlag.AlignVCenter)
        x = QToolButton()
        x.setIcon(icons.icon("close", 18, theme.C["text2"]))
        x.setIconSize(QSize(18, 18))
        x.setToolTip("Close (Esc)")
        x.setAccessibleName("Close options")
        x.clicked.connect(self.close_tray)
        head.addWidget(x)
        lay.addLayout(head)
        self.chip_host = QWidget()
        self.chip_row = FlowLayout(self.chip_host)
        self.chips = QButtonGroup(self)
        self.chips.buttonClicked.connect(lambda *_: self._filter())
        lay.addWidget(self.chip_host)
        self.list = QListWidget()
        self.list.setAccessibleName("Options")
        self.list.setAccessibleDescription("Choose one to pick it; Apply saves your picks to Steam")
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setSpacing(4)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        # As on the cover wall: left to itself the list steps a whole row per wheel line, so
        # one click of the wheel jumped about three rows instead of gliding.
        self.list.verticalScrollBar().setSingleStep(24)
        self.list.setItemDelegate(OptionDelegate(self))
        # Opaque, so nothing behind the panel (the page's preview playing, say) can show through it.
        self.list.setStyleSheet(f"QListWidget {{ background: {theme.C['tray']}; border: none; outline: none; }}")
        self.list.setDragEnabled(False)  # a click picks: a slight drag mustn't pick an option up instead
        self.list.setDragDropMode(QListView.DragDropMode.NoDragDrop)
        # The page previews the option the pointer rests on, not each one the list scrolls under it.
        self._hover_wait = QTimer(self, singleShot=True, interval=140)
        self._hover_wait.timeout.connect(self._hover_now)
        self._hovered = None  # the option the page is previewing
        self.list.itemEntered.connect(lambda _it: self._hover_wait.start())
        self.list.viewportEntered.connect(lambda: self._hover_wait.start())
        # While it scrolls, wait for it to settle before starting animations or loads, so the glide stays smooth.
        self._settle = QTimer(self, singleShot=True, interval=120)
        self._settle.timeout.connect(lambda: self.playing.sweep())
        self._settle.timeout.connect(self._resume)
        self._settle.timeout.connect(self._hover_now)
        self._gen = 0  # counts openings, so a late answer for an earlier one is ignored
        self.list.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemClicked.connect(self._use)  # a click picks it
        self.list.itemActivated.connect(self._use)  # so does Enter (and a double click: already picked)
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
        self.empty_text = label("Artists add new artwork all the time, so look again later.", "muted", wrap=True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_text)
        el.addStretch(2)
        self.empty.hide()
        lay.addWidget(self.empty, 1)
        self.more = QPushButton("Load more")
        self.more.clicked.connect(self._more)
        lay.addWidget(self.more)
        foot = QHBoxLayout()
        foot.setSpacing(12)
        self.hint = label("", "faint", wrap=True)
        foot.addWidget(self.hint, 1)
        self.remove_btn = QPushButton("Clear")
        self.remove_btn.setAccessibleDescription("Removes this piece when you apply")
        self.remove_btn.clicked.connect(self._clear)
        foot.addWidget(self.remove_btn)
        lay.addLayout(foot)
        self.assets = []
        self.page = 0

    def _width_for_covers(self) -> int:
        """Wide enough for four covers to a row (three wide covers or logos, two banners, six icons)."""
        sp = 4  # the list's spacing, around every option
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        frame = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth) * 2
        return 16 * 2 + frame + bar + GRID_W + sp + 8

    def open_slot(self, slot):
        if slot is not self.slot:
            self.preview.emit(self.slot, None, None)
        self.slot = slot
        self.title.setText(slot.label)
        self.hint.setText(f"Hover to see it on the page. Click to pick this {slot.label.lower()}; "
                          "Apply saves your picks.")
        self.list.setIconSize(thumb_size(slot.key))
        self.assets = []
        self.page = 0
        self._gen += 1
        self.playing.reset()
        self.list.clear()
        self._hovered = None
        self._show_empty(False)
        self._chips([])
        self.more.hide()
        self._update_buttons()
        self._held.clear()
        if not self.is_open() and self.area is not None:
            self._entering = True  # fill it before picturing it, holding back what's slow
            self.load()
            self._appear()
        else:
            self._appear()
            self.load()
        self.slot_changed.emit(slot)

    def load(self):
        game = self.win.current_game()
        steam = self.win.steam_option(self.slot.key) if self.page == 0 else None
        if steam is not None:
            self._add([steam])
        if game is None:
            self.status.setText("Steam's official artwork. Match this game on SteamGridDB for more options."
                                if steam is not None else "Match this game to a SteamGridDB title first.")
            return
        self.status.setText("Loading options…")
        slot, page, appid, gen = self.slot, self.page, self.win.current_shortcut().appid, self._gen
        self.win.fetch_assets(game.id, slot.key, page,
                              lambda assets: self._loaded(slot, page, appid, assets, game.id, gen))

    def _loaded(self, slot, page, appid, assets, gid=None, gen=None):
        sc = self.win.current_shortcut()
        if slot is not self.slot or page != self.page or sc is None or sc.appid != appid:
            return  # another piece, game or page now (or gone home)
        if gen is not None and gen != self._gen:
            return  # opened again since (animated turned on or off, say): a newer load is coming
        if gid is not None and getattr(self.win.current_game(), "id", None) != gid:
            return  # matched to another SteamGridDB game since
        if assets is None:
            self.status.setText("Couldn't load options from SteamGridDB.")
            return
        self._add(assets)
        self.more.setVisible(len(assets) >= 50)
        self._chips(sorted({a.style for a in self.assets if a.style}, key=lambda st: (st != "official", st)),
                    any(a.animated for a in self.assets))
        self._filter()
        if not self.assets:
            self.empty_title.setText(f"No {slot.label.lower()} for this game on SteamGridDB yet")
            self._show_empty(True)
            self.status.setText("")
        else:
            self.status.setText(f"{len(self.assets)} options{self._animated_count()}")

    def _thumb(self, a, it):
        """Load an option's thumbnail; while the tray slides in, only ones already to hand, as
        decoding the rest (or starting video) mid-slide would make it stutter."""
        if getattr(a, "animated", False):
            return  # asked for once it's in view (see _Playing.sweep): it may be a video to start
        if not self._entering:
            self.win.load_thumb(a, it)
            return
        pm = pixmap_cache.get(("thumb", getattr(a, "thumb", None)))
        if pm is not None:
            it.setData(ROLE_PIX, pm)
        else:
            self._held.append((a, it))

    def _animated_count(self) -> str:
        """", 3 animated" (or ", none animated") where SteamGridDB was asked for animated options."""
        if not (self.slot.animates and self.win.show_animated):
            return ""
        n = sum(a.animated for a in self.assets)
        so_far = " so far" if self.more.isVisibleTo(self) else ""
        return f", {n} animated{so_far}" if n else f", none animated{so_far}"

    def _add(self, assets):
        slot = self.slot
        self.assets += assets
        in_use, picked = self._ids()
        for i, a in enumerate(assets):
            n = len(self.assets) - len(assets) + i + 1
            name = (f"{slot.label} option {n}, Steam's official artwork" if isinstance(a, SteamAsset) else
                    f"{slot.label} option {n}, by {a.author or 'unknown'}" + (f", {style_name(a.style)}" if a.style else ""))
            it = QListWidgetItem(name)
            it.setData(ROLE_NAME, name)
            it.setData(ROLE_ASSET, a)
            self._mark(it, in_use, picked)
            it.setToolTip(describe(a))
            full = _full_for(a.id)
            if full is not None:
                it.setData(ROLE_FULL, full)
            self.list.addItem(it)
            self.playing.items[a.id] = it
            self._thumb(a, it)

    def _show_empty(self, empty: bool):
        if empty and self.list.hasFocus():
            self.setFocus(Qt.FocusReason.OtherFocusReason)  # not on to Clear, as hiding would; Esc still closes
        self.list.setVisible(not empty)
        self.empty.setVisible(empty)
        self.hint.setVisible(not empty)
        self._update_buttons()

    def _more(self):
        self.page += 1
        self.more.hide()
        self.load()

    def _chips(self, styles: list[str], animated: bool = False):
        for b in self.chips.buttons():
            self.chips.removeButton(b)
            self.chip_row.removeWidget(b)  # out of the layout now, not when it's deleted
            b.hide()
            b.deleteLater()
        for name in ["all"] + (["animated"] if animated else []) + styles:
            b = QPushButton(style_name(name) if name != "all" else "All")
            b.setProperty("kind", "chip")
            b.setProperty("style_key", name)
            b.setCheckable(True)
            b.setChecked(name == "all")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chips.addButton(b)
            self.chip_row.addWidget(b)
        self.chip_host.setVisible(len(styles) > 1 or animated)
        self.chip_row.invalidate()

    def _filter(self):
        b = self.chips.checkedButton()
        want = b.property("style_key") if b else "all"
        for i in range(self.list.count()):
            a = self.list.item(i).data(ROLE_ASSET)
            if want == "animated":
                self.list.item(i).setHidden(not a.animated)
            else:
                self.list.item(i).setHidden(want != "all" and a.style != want)
        QTimer.singleShot(0, self.playing.sweep)  # once the list has laid itself out again

    def _scrolled(self, *_):
        self._hover_wait.stop()  # no previews while it moves
        self.playing.timer.stop()  # nor loads: only what's in view once it settles
        self._settle.start()

    def _resume(self):
        if self.isVisible() and self._open and not self._leaving:
            self.playing.timer.start()

    def _hover_now(self):
        """Preview the option under the pointer once it has rested there (and scrolling has settled)."""
        if self._settle.isActive() or not self.isVisible():
            return
        vp = self.list.viewport()
        pos = vp.mapFromGlobal(QCursor.pos())
        it = self.list.itemAt(pos) if vp.rect().contains(pos) else None
        if it is not None and it.isHidden():
            it = None
        self._hover(it)

    def _hover(self, item):
        """Hovering an option previews it on the page behind (dimmed, but in view), at full quality once loaded."""
        if item is self._hovered:
            return
        self._hovered = item
        if item is None:
            self.preview.emit(self.slot, None, None)
        else:
            self.preview.emit(self.slot, _best(item.data(ROLE_FULL), item.data(ROLE_PIX)), item.data(ROLE_ASSET))

    def leaveEvent(self, e):
        self._hover_wait.stop()
        self._hover(None)
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
        sc = self.win.current_shortcut()
        pick = self._pick()
        on_disk = sc is not None and self.win.slot_path(sc, self.slot) is not None
        self.remove_btn.setEnabled((on_disk or pick is not None) and not (pick is not None and pick.clears))
        # Nothing to pick: Clear only while there's something to clear.
        empty = self.empty.isVisibleTo(self)
        self.remove_btn.setVisible(not empty or self.remove_btn.isEnabled())

    def _use(self, it: QListWidgetItem | None = None):
        """Pick the option clicked (or chosen with Enter). Choosing the one in use again drops the waiting pick."""
        it = it or self.list.currentItem()
        if it is None:
            return
        load = it.data(ROLE_LOAD)
        if load is not None and load[0] == "failed" and it.data(ROLE_PIX) is None:
            load[1]()  # couldn't load: a click tries again rather than picking it
            return
        if it.data(ROLE_INUSE) or it.data(ROLE_PICK):
            return
        in_use, _picked = self._ids()
        if it.data(ROLE_ASSET).id == in_use and not self._clearing():
            self.win.unstage(self.slot)
        else:
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
        if self._leaving or not self._open:
            return
        self._hover_wait.stop()
        self._hovered = None
        self.preview.emit(self.slot, None, None)
        self._disappear()
        self.closed.emit()

    # -- lying over the page ------------------------------------------------

    def place_over(self, area: QWidget, host: QWidget | None = None):
        """Lie over the right-hand side of `area`, sliding in and out, without taking any room
        from it, so the page underneath keeps its layout. The tray lives in `host` (the main
        window), above the dimmed backdrop that covers everything else while it's open."""
        self.area = area
        self.host = host or area.window()
        self.setParent(self.host)
        self._shadow = _Shadow(self.host)
        self._shadow.hide()
        self._ghost = _Ghost(self.host)
        self._ghost.hide()
        area.installEventFilter(self)
        self.hide()

    def is_open(self) -> bool:
        if self.area is None:
            return self.isVisible()
        return self._open and not self._leaving

    def _place(self):
        """The tray sits in place; its ghost and shadow follow the slide."""
        if self.area is None:
            return
        r = QRect(self.area.mapTo(self.host, QPoint(0, 0)), self.area.size())
        x = r.right() + 1 - self.width()
        self.setGeometry(x, r.y(), self.width(), r.height())
        x += round((1 - self._t) * (self.width() + SHADOW_W))
        self._ghost.setGeometry(x, r.y(), self.width(), r.height())
        self._shadow.setGeometry(x - SHADOW_W, r.y(), SHADOW_W, r.height())
        self._shadow.strength = self._t
        self._shadow.update()

    def _snapshot(self):
        """Picture the tray as it will look, for its ghost to slide."""
        self._place()
        # A hidden tray lays itself out lazily; settle everything first so the picture is right.
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
        self.layout().activate()
        self.list.doItemsLayout()
        self._ghost.pm = self.grab()

    def _hide_quietly(self):
        self._swapping = True
        self.hide()
        self._swapping = False

    def _appear(self):
        if self.area is None:
            self.show()
            return
        if self.is_open():
            self.raise_()
            return
        self._leaving = False
        if not self._open:
            self._t = 0.0
        self._open = True
        self._snapshot()
        self._hide_quietly()
        self._step(self._t)
        scrim = scrim_of(self)
        if scrim is not None:
            scrim.cover(self, self.close_tray)  # the rest of the window dims, and a click there closes it
        self._shadow.show()
        self._ghost.show()
        self._shadow.raise_()
        self._ghost.raise_()
        self._run(1.0, SLIDE_IN, QEasingCurve.Type.OutCubic)

    def _disappear(self):
        if self.area is None:
            self.hide()
            return
        self._leaving = True
        self._uncover()
        self.playing.stop()
        if self.isVisible():
            self._ghost.pm = self.grab()
            self._hide_quietly()
            self._ghost.show()
            self._ghost.raise_()
        self._run(0.0, SLIDE_OUT, QEasingCurve.Type.InCubic)

    def _run(self, end: float, ms: int, curve):
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(curve)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(end)
        self._anim.start()

    def _step(self, v):
        self._t = float(v)
        self._ghost.opacity = self._t
        self._place()  # a move only: the ghost just redraws its picture

    def _settled(self):
        self._ghost.hide()
        self._ghost.pm = QPixmap()
        if self._leaving:
            self._leaving = self._open = self._entering = False
            self._held.clear()
            self._shadow.hide()
        else:
            self._place()
            self.show()
            self.raise_()
            self._shadow.raise_()
            self.list.setFocus(Qt.FocusReason.PopupFocusReason)  # Esc and the arrows work straight away
            self.playing.start()
            self._entering = False
            held, self._held = self._held, []
            for a, it in held:
                self.win.load_thumb(a, it)

    def close_now(self):
        """Gone at once, no slide (leaving the game page)."""
        if self.isVisible():
            self.hide()
        else:
            self._reset()

    def _reset(self):
        self._anim.stop()
        self.playing.stop()
        self._entering = False
        self._held.clear()
        self._open = self._leaving = False
        self._t = 0.0
        for w in (self._shadow, self._ghost):
            if w is not None:
                w.hide()
        self._uncover()

    def _uncover(self):
        scrim = scrim_of(self)
        if scrim is not None:
            scrim.uncover(self)

    def hideEvent(self, e):
        if not self._swapping and not e.spontaneous():  # closed outright (not the window minimising)
            self._reset()
        super().hideEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.area and e.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._place()
        return False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_tray()
        else:
            super().keyPressEvent(e)
