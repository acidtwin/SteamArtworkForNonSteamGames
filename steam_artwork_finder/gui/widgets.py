"""Small building blocks shared by the pages."""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QImageReader, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QWidget

from . import icons, motion, theme, workers
from .theme import C, q

_cache: dict[tuple, QPixmap] = {}
FULL_CACHE = 80  # full-size pixmaps kept (the open game's pieces, recent thumbnails)


def _largest_frame(reader: QImageReader) -> None:
    """Point the reader at the biggest image in the file. An .ico holds several
    sizes and Qt reads the first, often 16 or 32 px, which blurs when scaled up."""
    if reader.imageCount() <= 1:
        return
    best, area = 0, -1
    for i in range(reader.imageCount()):
        if not reader.jumpToImage(i):
            break
        s = reader.size()
        if s.width() * s.height() > area:
            best, area = i, s.width() * s.height()
    reader.jumpToImage(best)


def _read_image(path: Path | None, data: bytes | None) -> QImage:
    if path is not None:
        reader = QImageReader(str(path))
    else:
        buf = QBuffer()
        buf.setData(QByteArray(data))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buf)
    reader.setAutoTransform(True)
    _largest_frame(reader)
    return reader.read()


def remember(key, pm: QPixmap):
    """Keep `pm` in the full-size cache under `key` (as load_pixmap does)."""
    if key is None or pm.isNull():
        return
    while len(_cache) >= FULL_CACHE:
        _cache.pop(next(iter(_cache)))
    _cache[key] = pm


def read_image(data: bytes) -> QImage:
    """Worker: decode picture bytes (off the GUI thread, so a page of thumbnails arriving doesn't stutter)."""
    return _read_image(None, data)


def load_pixmap(path: Path | None = None, data: bytes | None = None, key=None) -> QPixmap:
    """A full-size pixmap from a file or bytes, cached."""
    if path is not None:
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return QPixmap()
    if key is not None and key in _cache:
        return _cache[key]
    pm = QPixmap.fromImage(_read_image(path, data)) if path is not None or data else QPixmap()
    remember(key, pm)
    return pm


def decode_scaled(path: str, w: int, h: int, contain: bool = False) -> QImage:
    """Worker: decode an image file straight to a `w`×`h` cover-cropped QImage
    (or, with `contain`, the whole picture centred on a clear `w`×`h`).

    Never holds the full-size image longer than the decode itself, so a
    300-game wall costs a few megabytes instead of hundreds.
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    _largest_frame(reader)
    size = reader.size()
    if size.isValid() and w > 0 and h > 0:
        if contain:
            scale = min(w / size.width(), h / size.height())
            reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
        else:
            scale = max(w / size.width(), h / size.height())
            reader.setScaledSize(QSize(max(w, round(size.width() * scale)), max(h, round(size.height() * scale))))
    img = reader.read()
    if img.isNull():
        return img
    if contain:
        out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.drawImage((w - img.width()) // 2, (h - img.height()) // 2, img)
        painter.end()
        return out
    x = (img.width() - w) // 2
    y = (img.height() - h) // 2
    return img.copy(x, y, w, h)


class ScaledCache:
    """Thumbnails decoded off the GUI thread, keyed by path, mtime and size.

    `get` returns the pixmap if ready; otherwise it starts the decode and
    returns None, and calls `on_ready` later so the view can repaint.
    """

    def __init__(self, budget: int = 96 * 1024 * 1024):
        self.ready: OrderedDict[tuple, QPixmap] = OrderedDict()  # oldest first
        self.pending: set[tuple] = set()
        self.budget, self.used = budget, 0
        self._notify: list = []  # repaints asked for by decodes that landed, run together

    @staticmethod
    def _bytes(pm: QPixmap) -> int:
        return pm.width() * pm.height() * 4

    def _put(self, key, pm: QPixmap):
        for k in [k for k in self.ready if k[0] == key[0] and k[1] != key[1]]:
            self.used -= self._bytes(self.ready.pop(k))  # an older version of a replaced file
        old = self.ready.pop(key, None)
        if old is not None:
            self.used -= self._bytes(old)
        self.ready[key] = pm
        self.used += self._bytes(pm)
        while self.used > self.budget and len(self.ready) > 1:
            _, gone = self.ready.popitem(last=False)
            self.used -= self._bytes(gone)

    def _ready_soon(self, on_ready):
        # Many thumbnails land at once when a wall first shows: one repaint for the batch.
        if on_ready is None:
            return
        if not self._notify:
            QTimer.singleShot(16, self._flush)
        if on_ready not in self._notify:
            self._notify.append(on_ready)

    def _flush(self):
        calls, self._notify = self._notify, []
        for fn in calls:
            try:
                fn()
            except RuntimeError:  # its view was deleted meanwhile
                pass

    def get(self, path: Path, size: QSize, on_ready=None, dpr: float = 1.0,
            contain: bool = False) -> QPixmap | None:
        """`dpr` is the screen's scale, so the decode has enough pixels to stay sharp;
        `contain` fits the whole picture in, rather than cropping it to fill."""
        w, h = round(size.width() * dpr), round(size.height() * dpr)
        try:
            key = (str(path), path.stat().st_mtime_ns, w, h, contain)
        except OSError:
            return QPixmap()
        pm = self.ready.get(key)
        if pm is not None:
            self.ready.move_to_end(key)
            return pm
        if key in self.pending:
            return None
        self.pending.add(key)

        def done(img: QImage, key=key):
            self.pending.discard(key)
            pm = QPixmap.fromImage(img)  # null if the file wasn't an image
            pm.setDevicePixelRatio(dpr)
            self._put(key, pm)
            self._ready_soon(on_ready)

        workers.run(decode_scaled, key[0], w, h, contain, kind="cpu", on_done=done,
                    on_error=lambda _m, key=key: done(QImage(), key))
        return None

    def forget(self, path: Path):
        s = str(path)
        for k in [k for k in self.ready if k[0] == s]:
            self.used -= self._bytes(self.ready.pop(k))


def fill_rect(pm: QPixmap, target: QSize, dpr: float = 1.0, top: bool = False) -> QPixmap:
    """Scale to cover target, cropping the overflow (like CSS object-fit: cover).

    With `dpr` above 1 the result has that many more pixels, for sharp HiDPI drawing.
    `top` keeps the top of the image and crops only the bottom, as Steam does with a banner."""
    target = QSize(round(target.width() * dpr), round(target.height() * dpr))
    if pm.size() == target:  # made to measure (an animation's frame): drawn as it is
        out = QPixmap(pm)
        out.setDevicePixelRatio(dpr)
        return out
    s = pm.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                  Qt.TransformationMode.SmoothTransformation)
    x = (s.width() - target.width()) // 2
    y = 0 if top else (s.height() - target.height()) // 2
    out = s.copy(x, y, target.width(), target.height())
    out.setDevicePixelRatio(dpr)
    return out


def draw_checker(p: QPainter, rect: QRectF, size: int = 10):
    p.save()
    p.setClipRect(rect)
    p.fillRect(rect, QColor("#232a33"))
    c = QColor("#2b333e")
    y = int(rect.top())
    row = 0
    while y < rect.bottom():
        x = int(rect.left()) + (size if row % 2 else 0)
        while x < rect.right():
            p.fillRect(x, y, size, size, c)
            x += size * 2
        y += size
        row += 1
    p.restore()


def draw_dashed_empty(p: QPainter, rect: QRectF, text: str, plus: bool = True, focus=False,
                      ground: QColor | None = None):
    p.save()
    p.fillRect(rect, ground if ground is not None else QColor("#1f2a38"))
    pen = QPen(q("blue") if focus else QColor("#4a5666"), 1.5, Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)
    p.setPen(q("text2"))
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("control"))
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    if plus:
        pf = QFont(f)
        pf.setPointSizeF(theme.pt("title"))
        pf.setWeight(QFont.Weight.Light)
        p.setFont(pf)
        p.setPen(q("blue_ink"))
        p.drawText(rect.adjusted(0, -28, 0, 0), Qt.AlignmentFlag.AlignCenter, "+")
        p.setFont(f)
        p.setPen(q("text2"))
        p.drawText(rect.adjusted(6, 14, -6, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
    else:
        p.drawText(rect.adjusted(12, 0, -12, -14), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
                   | Qt.TextFlag.TextWordWrap, text)
    p.restore()


def draw_badge(p: QPainter, rect: QRectF, text: str = "IN USE"):
    p.save()
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("tag"))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    w = p.fontMetrics().horizontalAdvance(text) + 10
    h = p.fontMetrics().height() + 4
    r = QRectF(rect.left() + 4, rect.bottom() - h - 4, w, h)
    p.fillRect(r, q("blue"))
    p.setPen(QColor("#0b1a26"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


def draw_animated_tag(p: QPainter, rect: QRectF):
    """A small dark pill in the bottom-right corner marking an animated option: a play
    triangle, with the word when there's room."""
    p.save()
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("tag"))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    fm = p.fontMetrics()
    h = fm.height() + 4
    text = "ANIMATED" if rect.width() >= 120 else ""
    w = h + (fm.horizontalAdvance(text) + 6 if text else 0)
    r = QRectF(rect.right() - w - 4, rect.bottom() - h - 4, w, h)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(11, 16, 22, 200))
    p.drawRoundedRect(r, h / 2, h / 2)
    icons.draw(p, "play", QRectF(r.left() + 3, r.top() + 2, h - 4, h - 4), QColor("white"))
    if text:
        p.setPen(QColor("white"))
        p.drawText(r.adjusted(h - 2, 0, -6, 0), Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


def draw_dots(p: QPainter, x: float, y: float, states: list, d: float = 7, gap: float = 4):
    """One dot per piece: filled (has it), ring (missing), or a small grey dot ("none":
    SteamGridDB has nothing for it, so there's nothing to get)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, on in enumerate(states):
        r = QRectF(x + i * (d + gap), y, d, d)
        if on == "none":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("text3"))
            p.drawEllipse(r.adjusted(d * 0.3, d * 0.3, -d * 0.3, -d * 0.3))
            continue
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("blue_ink"))
        else:
            p.setPen(QPen(q("text3"), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r.adjusted(0.6, 0.6, -0.6, -0.6))
    p.restore()
    return x + len(states) * (d + gap)


# ---- loading and "couldn't load" ---------------------------------------------------

FADE_IN = 0.16  # seconds a picture takes to fade in over its loading shimmer


class _Pulse(QObject):
    """Drives the loading shimmer and fade-ins: about 30 times a second it repaints just what
    asked (each paint of a loading tile asks again), and stops once nothing is loading."""

    PERIOD = 1.6  # seconds for the light band to cross a tile

    def __init__(self):
        super().__init__()
        self._want: dict[int, tuple] = {}
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._tick)

    def phase(self) -> float:
        """0..1 across the band's sweep; shared, so every tile on screen sweeps together, as Steam's do."""
        return (time.monotonic() % self.PERIOD) / self.PERIOD

    def want(self, widget: QWidget, rect: QRect | None = None):
        """Repaint `widget` (or just `rect` of it) on the next tick."""
        entry = self._want.setdefault(id(widget), (widget, []))
        entry[1].append(rect)
        if not self.timer.isActive():
            self.timer.start()

    def _tick(self):
        want, self._want = self._want, {}
        if not want:
            self.timer.stop()
            return
        for w, rects in want.values():
            try:
                if None in rects:
                    w.update()
                else:
                    for r in rects:
                        w.update(r)
            except RuntimeError:  # gone meanwhile
                pass


_pulse: _Pulse | None = None


def pulse() -> _Pulse:
    global _pulse
    if _pulse is None:
        _pulse = _Pulse()
    return _pulse


def fade(since: float | None) -> float:
    """How far a picture that arrived at `since` has faded in (1.0: fully)."""
    if since is None:
        return 1.0
    return min(1.0, (time.monotonic() - since) / FADE_IN)


def draw_loading(p: QPainter, rect: QRectF, phase: float, ground: QColor | None = None):
    """A tile still loading: the plain dark tile with a faint light band drifting across it."""
    p.save()
    p.fillRect(rect, ground if ground is not None else q("raised"))
    w = rect.width()
    band = max(40.0, w * 0.6)
    x = rect.left() - band + phase * (w + 2 * band)
    g = QLinearGradient(QPointF(x - band / 2, rect.top()), QPointF(x + band / 2, rect.top() + band * 0.35))
    g.setColorAt(0.0, QColor(255, 255, 255, 0))
    g.setColorAt(0.5, QColor(255, 255, 255, 15))
    g.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(rect, g)
    p.restore()


def retry_text(n: int, width: float, fm: QFontMetrics) -> str:
    """"Retrying (2 of 3)…", shortened to fit `width`."""
    for text in (f"Retrying ({n} of {workers.RETRIES})…", f"Retry {n} of {workers.RETRIES}", f"{n}/{workers.RETRIES}"):
        if fm.horizontalAdvance(text) <= width:
            return text
    return ""


def draw_retrying(p: QPainter, rect: QRectF, n: int):
    """The retry count, in amber, along the bottom of a loading tile (strip options have no caption)."""
    p.save()
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("label"))
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    fm = QFontMetrics(f)
    text = retry_text(n, rect.width() - 12, fm)
    if text:
        p.setPen(q("warn"))
        p.drawText(rect.adjusted(6, 0, -6, -5), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, text)
    p.restore()


def draw_failed(p: QPainter, rect: QRectF, words: bool = True, hot: bool = False):
    """A tile that couldn't load: a warning mark, and "Couldn't load" over a blue "Retry" where there's room."""
    p.save()
    p.fillRect(rect, QColor("#283240") if hot else QColor("#222a35"))
    f = QFont(p.font())
    f.setPointSizeF(theme.pt("label"))
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    fm = QFontMetrics(f)
    d = 20 if rect.height() >= 60 else 16
    lines = [("Couldn't load", q("text2")), ("Retry", q("blue_ink"))]
    if not words or rect.height() < d + 2 * fm.height() + 10 or fm.horizontalAdvance(lines[0][0]) > rect.width() - 8:
        lines = [("Retry", q("blue_ink"))] if words and rect.height() >= d + fm.height() + 8 else []
    h = d + len(lines) * (fm.height() + 2) + (2 if lines else 0)
    y = rect.top() + (rect.height() - h) / 2
    icons.draw(p, "warning", QRectF(rect.center().x() - d / 2, y, d, d), q("text3"))
    y += d + 4
    for text, color in lines:
        p.setPen(color)
        p.drawText(QRectF(rect.left(), y, rect.width(), fm.height()), Qt.AlignmentFlag.AlignCenter, text)
        y += fm.height() + 2
    p.restore()


FRAME_CACHE = 64 * 1024 * 1024  # bytes of scaled animation frames one ArtView keeps
HIDDEN_DROP_MS = 5000  # an animation hidden this long lets go of its frames (made again when shown)


def _unhook(signal, slot):
    """Disconnect, quietly if it wasn't connected."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass


class ArtView(QWidget):
    """One piece of artwork drawn at a fixed shape, or a dashed empty slot."""

    clicked = Signal()
    hovered = Signal(bool)

    def __init__(self, size: QSize, empty_text: str = "", checker: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(size)
        self.pixmap = QPixmap()
        self.backdrop = QPixmap()  # drawn behind (logo-on-banner)
        self.empty_text = empty_text
        self.checker = checker
        self.badge = False
        self.selected = False
        self.animated = False  # marks an animated option with a small tag
        self.round = 3
        self._scaled: tuple | None = None  # (key, backdrop, art) as drawn at this size
        self._frames: dict = {}  # scaled frames of a looping animation, so each loop only draws
        self._frames_bytes = 0
        self._motion = None
        self._parked: tuple | None = None  # (key, Motion) replaced by a preview, kept paused to go back to
        self._drop_timer: QTimer | None = None
        self.motion_spec: tuple = (None, None)  # (key, factory) of what's playing, for previews
        self.load_state: tuple | None = None  # ("loading", retry n) or ("failed", retry fn) until a picture comes
        self._fade_since: float | None = None  # when the picture arrived over the shimmer, for its fade-in
        self._shimmered = False  # painted as loading since the last picture
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAccessibleName(empty_text or "Artwork")
        self.setAccessibleDescription("Press Enter to open the options")

    def describe(self, text: str):
        """What a screen reader says for this piece."""
        self.setAccessibleName(text)

    def set_loading(self, retry: int = 0):
        """A picture is on its way (`retry`: which retry this is, 0 for the first try)."""
        self.load_state = ("loading", retry)
        self.setToolTip("")
        self.update()

    def set_failed(self, retry_fn):
        """It couldn't load even after retrying; a click (or Enter) calls `retry_fn`."""
        self.load_state = ("failed", retry_fn)
        self.setToolTip("Couldn't load this picture. Click to try again.")
        self.update()

    def _arrived(self):
        if self.load_state is not None:
            self.load_state = None
            self.setToolTip("")
        if self._shimmered:  # it was seen loading: fade the picture in
            self._shimmered = False
            self._fade_since = time.monotonic()

    def set_pixmap(self, pm: QPixmap | None, backdrop: QPixmap | None = None):
        pm, backdrop = pm or QPixmap(), backdrop or QPixmap()
        if not pm.isNull():
            self._arrived()
        if pm.cacheKey() == self.pixmap.cacheKey() and backdrop.cacheKey() == self.backdrop.cacheKey():
            return  # the same pictures (a refresh): keep what's already scaled
        self.pixmap = pm
        self.backdrop = backdrop
        self._scaled = None
        self.update()

    def set_motion(self, key=None, factory=None):
        """Play an animation here: `factory()` makes the Motion, `key` says what it is, so asking
        again for the same thing carries on rather than restarting. No key stops it.

        The Motion is made only once the view is on show, so options that don't fit the strip
        never decode. The one it replaces is kept aside, paused, so a hover preview that ends
        goes straight back to it instead of decoding it again."""
        if key is not None and key == self.motion_spec[0]:
            return
        back = None
        if key is not None and self._parked is not None and self._parked[0] == key:
            back, self._parked = self._parked[1], None  # going back to the one kept aside
        if self._motion is not None:
            self._park(self.motion_spec[0], self._motion)
            self._motion = None
        self._frames = {}
        self._frames_bytes = 0
        self.motion_spec = (key, factory)
        self._make(back)

    def drop_motion(self):
        """Stop and let go of every animation here, for good (the view is going)."""
        self.motion_spec = (None, None)
        self._let_go()

    def _let_go(self):
        for m in (self._motion, self._parked[1] if self._parked else None):
            if m is not None:
                m.retire()
        self._motion = self._parked = None
        self._frames = {}
        self._frames_bytes = 0

    def _park(self, key, m):
        m.stop()
        _unhook(m.frame, self._frame)
        if self._parked is not None:
            self._parked[1].retire()
        self._parked = (key, m) if key is not None else None
        if key is None:
            m.retire()

    def _make(self, m=None):
        key, factory = self.motion_spec
        if key is None or factory is None or self._motion is not None:
            return
        if m is not None:
            pass
        elif self.isVisible():
            m = factory()
        else:
            return  # made when it's shown
        self._motion = m
        m.frame.connect(self._frame)
        if self.isVisible():
            m.start()

    def _frame(self, pm: QPixmap):
        first = self.pixmap.isNull()
        if first:
            self._arrived()
        self.pixmap = pm
        self.update()
        if first:
            self.frame_arrived()

    def frame_arrived(self):
        """The first picture of an animation has arrived (for subclasses)."""

    def showEvent(self, e):
        if self._drop_timer is not None:
            self._drop_timer.stop()
        if self._motion is not None:
            self._motion.start()
        else:
            self._make()
        super().showEvent(e)

    def hideEvent(self, e):
        if self._motion is not None:
            self._motion.stop()  # nothing plays where it can't be seen
        if not e.spontaneous() and (self._motion is not None or self._parked is not None):
            # Hidden for a while (a strip option that no longer fits, the page left for the
            # library): let go of its frames or video player; it's made again when shown.
            if self._drop_timer is None:
                self._drop_timer = QTimer(self, singleShot=True, interval=HIDDEN_DROP_MS)
                self._drop_timer.timeout.connect(self._drop_hidden)
            self._drop_timer.start()
        super().hideEvent(e)

    def _drop_hidden(self):
        if not self.isVisible():
            spec = self.motion_spec
            self._let_go()
            self.motion_spec = spec

    def resizeEvent(self, e):
        self._scaled = None
        self._frames = {}
        self._frames_bytes = 0
        super().resizeEvent(e)

    def _render(self) -> tuple[QPixmap | None, QPixmap | None]:
        """The backdrop and the art scaled for this size, made once rather than on every repaint."""
        dpr = self.devicePixelRatioF()
        key = (self.pixmap.cacheKey(), self.backdrop.cacheKey(), self.checker, dpr)
        if (self._scaled is None or self._scaled[0] != key) and key in self._frames:
            self._scaled = self._frames[key]
        if self._scaled is None or self._scaled[0] != key:
            bd = fill_rect(self.backdrop, self.size(), dpr) if not self.backdrop.isNull() else None
            art = None
            exact = False
            if not self.pixmap.isNull():
                if self.checker or bd is not None:
                    # Logos keep their shape, centred with a margin.
                    box = self.size() * (0.7 if bd is not None else 0.86) * dpr
                    art = self.pixmap.scaled(box, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                    art.setDevicePixelRatio(dpr)
                else:
                    exact = self.pixmap.size() == QSize(round(self.width() * dpr), round(self.height() * dpr))
                    art = fill_rect(self.pixmap, self.size(), dpr)
            self._scaled = (key, bd, art)
            # The same frames come round again: keep them scaled, unless made to measure already.
            if (isinstance(self._motion, motion.FrameMotion) and not (exact and bd is None)
                    and self._frames_bytes < FRAME_CACHE):
                self._frames[key] = self._scaled
                self._frames_bytes += sum(pm.width() * pm.height() * 4 for pm in (bd, art) if pm is not None)
        return self._scaled[1], self._scaled[2]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, self.round, self.round)
        focus = theme.kbd_focus(self)
        if self.pixmap.isNull() and self.load_state is not None:
            p.setClipPath(clip)
            if self.load_state[0] == "failed":
                draw_failed(p, r, hot=self.underMouse())
            else:
                self._shimmered = True
                draw_loading(p, r, pulse().phase(), QColor("#1f2a38") if self.checker else None)
                if self.load_state[1]:
                    draw_retrying(p, r, self.load_state[1])
                pulse().want(self)
            p.setClipping(False)
            if focus:
                p.setPen(QPen(q("text"), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), self.round, self.round)
            return
        if self.pixmap.isNull():
            if not self.backdrop.isNull():
                # An empty logo over its banner: the slot stays plainly empty, the banner faint behind it.
                bd, _art = self._render()
                p.setClipPath(clip)
                p.setOpacity(0.35)
                p.drawPixmap(self.rect(), bd)
                p.setOpacity(1.0)
                p.setClipping(False)
                draw_dashed_empty(p, r, self.empty_text, focus=focus, ground=QColor(31, 42, 56, 215))
            else:
                draw_dashed_empty(p, r, self.empty_text, focus=focus)
            return
        p.setClipPath(clip)
        bd, art = self._render()
        shown = fade(self._fade_since)
        if shown < 1.0:  # fading in over the shimmer it replaces
            draw_loading(p, r, pulse().phase(), QColor("#1f2a38") if self.checker else None)
            p.setOpacity(shown)
            pulse().want(self)
        else:
            self._fade_since = None
        if bd is not None:
            p.drawPixmap(self.rect(), bd)
        elif self.checker:
            draw_checker(p, r)
        if art is not None:
            if self.checker or bd is not None:
                dpr = art.devicePixelRatio()
                w, h = art.width() / dpr, art.height() / dpr
                p.drawPixmap(QPointF((self.width() - w) / 2, (self.height() - h) / 2), art)
            else:
                p.drawPixmap(self.rect(), art)
        p.setOpacity(1.0)
        p.setClipping(False)
        if self.badge:
            draw_badge(p, r)
        if self.animated:
            draw_animated_tag(p, r)
        # Picked: a blue outline and a ticked corner. Keyboard focus: a white ring inside it, so the
        # option you're on never looks like a second pick.
        if self.selected or self.underMouse():
            p.setPen(QPen(q("blue") if self.selected else QColor("#5a6a7e"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), self.round, self.round)
        if self.selected:
            d = 20
            tick = QRectF(r.right() - d - 5, r.top() + 5, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(q("blue"))
            p.drawEllipse(tick)
            icons.draw(p, "tick", tick.adjusted(4, 4, -4, -4).toRect(), QColor("white"))
        if focus:
            inset = 4 if self.selected else 1
            p.setPen(QPen(q("text"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(inset, inset, -inset, -inset), self.round, self.round)

    def enterEvent(self, e):
        self.hovered.emit(True)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered.emit(False)
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._activate()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
        else:
            super().keyPressEvent(e)


    def _activate(self):
        """A click or Enter: on a picture that couldn't load, try it again rather than picking it."""
        if self.pixmap.isNull() and self.load_state is not None and self.load_state[0] == "failed":
            self.load_state[1]()
            return
        self.clicked.emit()


class IconLabel(QWidget):
    """A drawn SVG icon at a fixed logical size, sharp at any device pixel ratio."""

    def __init__(self, name: str, size: int = 20, color=None, parent=None):
        super().__init__(parent)
        self.name, self.color, self.icon_size = name, color, size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set(self, name: str, color=None):
        self.name, self.color = name, color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, icons.pixmap(self.name, self.icon_size, self.color, self.devicePixelRatioF()))


class Notice(QFrame):
    """A full-width strip under the header."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notice")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 8, 16, 8)
        self.icon = IconLabel("info", 20)
        lay.addWidget(self.icon)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.text.setMinimumWidth(200)
        lay.addWidget(self.text, 1)
        self.buttons = QHBoxLayout()
        lay.addLayout(self.buttons)
        self.hide()

    def show_message(self, tone: str, text: str, actions: list[tuple[str, object]] = ()):
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.icon.set("warning" if tone == "warn" else "info", C["warn"] if tone == "warn" else C["blue_ink"])
        self.text.setText(text)
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w:
                w.deleteLater()
        for label, fn in actions:
            b = QPushButton(label)
            b.clicked.connect(fn)
            self.buttons.addWidget(b)
        self.show()


class Toast(QFrame):
    """Bottom-centre message with an optional Undo, floating over the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self.center_on: QWidget | None = None  # the pane to centre on; the whole window if None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 4, 6, 4)
        self.label = QLabel()
        lay.addWidget(self.label)
        self.action = QPushButton("Undo")
        self.action.setToolTip("Undo (Ctrl+Z)")
        self.action.clicked.connect(self._act)
        lay.addWidget(self.action)
        self._fn = None
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def show_message(self, text: str, undo=None, ms: int = 8000):
        self.label.setText(text)
        self._fn = undo
        self.action.setVisible(undo is not None)
        self.adjustSize()
        self.place()
        self.raise_()
        self.show()
        self.timer.start(ms)

    def place(self):
        """Bottom-centre of `center_on` (the main pane, as in Steam), else of the window, above the status bar."""
        par = self.parentWidget()
        area = par.rect()
        target = self.center_on
        if target is not None and target.isVisible():
            area = QRect(target.mapTo(par, QPoint(0, 0)), target.size())
        x = area.x() + (area.width() - self.width()) // 2
        self.move(max(8, min(x, par.width() - self.width() - 8)), par.height() - self.height() - 52)

    def _act(self):
        fn, self._fn = self._fn, None
        self.hide()
        if fn:
            fn()

    def undo_now(self):
        """Ctrl+Z: run the toast's Undo while it is still showing."""
        if self.isVisible() and self._fn:
            self._act()


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping onto new lines."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test):
        m = self.contentsMargins()  # Qt hands a custom layout the whole rect: margins are ours to keep
        outer, rect = rect, rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():  # hidden on purpose (not just not shown yet)
                continue
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x = rect.x()
                y += line + self._spacing
                line = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - outer.y() + m.bottom()


def label(text: str, name: str = "", wrap=False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    if wrap:
        lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lab


def quiet_button(text: str, fn=None) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("kind", "quiet")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if fn:
        b.clicked.connect(fn)
    return b
