"""Animated artwork: decoding APNG and animated WebP into frames, and playing them (or
SteamGridDB's video thumbnails) into an ArtView."""

from __future__ import annotations

import os
import queue
import struct
import threading
import time
import warnings
import weakref
import zlib
from collections import deque
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice, QObject, QRect, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap

from . import workers

PNG_SIG = b"\x89PNG\r\n\x1a\n"
MAX_FRAMES = 300
FRAME_BUDGET = 48 * 1024 * 1024  # bytes of decoded frames kept for one animation
# Every playing animation's kept frames together, across the whole app: each FrameMotion
# reserves its share as its decode starts and gives back what it didn't use as it ends. A
# tight pool just means fewer frames kept (see _Frames), never more memory.
TOTAL_BUDGET = 320 * 1024 * 1024
MIN_SHARE = 4 * 1024 * 1024
# Animation decodes run up to DECODE_THREADS at once, but each one first reserves the most
# memory it can need (its full-size working pictures, the file, and its frames) against
# DECODE_LIMIT, so two big banners never decode together; one bigger than the limit runs alone.
DECODE_LIMIT = 256 * 1024 * 1024
DECODE_THREADS = 3


# ---- decoding (safe off the GUI thread) -----------------------------------------


def _chunks(data: bytes):
    pos = len(PNG_SIG)
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) < length:
            return
        yield kind, body
        pos += 12 + length
        if kind == b"IEND":
            return


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _delay(num: int, den: int) -> int:
    ms = round(1000 * num / (den or 100))
    return ms if ms > 10 else 100  # as browsers do, a zero or tiny delay plays at 10 fps


def _fit(img: QImage, bound: QSize | None, crop: bool) -> QImage:
    """Scaled down to just cover `bound`; with `crop`, scaled to cover it and cut to exactly
    `bound` (keeping the top, as Steam does with a banner), to be drawn pixel for pixel."""
    if bound is None:
        return img
    if crop or img.width() > bound.width() and img.height() > bound.height():
        img = img.scaled(bound, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
    if crop and img.size() != bound:
        # crop="middle" cuts evenly top and bottom, as fill_rect does for the strips and the panel.
        y = (img.height() - bound.height()) // 2 if crop == "middle" else 0
        img = img.copy((img.width() - bound.width()) // 2, y, bound.width(), bound.height())
    return img


class TooBig(Exception):
    """Every frame won't fit the budget (raised when asked not to drop any)."""


class _Frames:
    """Collects an animation's frames as they're decoded, each scaled down to just cover
    `bound` straight away, and halves the frame rate whenever the frames kept pass
    `budget` bytes. Only one full-size frame is ever held, so a big animated banner
    can't eat gigabytes while it decodes. With `keep_all`, it raises TooBig rather than
    drop frames, so the caller can play it another way (see FrameMotion)."""

    def __init__(self, bound: QSize | None = None, budget: int | None = None, total: int = 0,
                 crop: bool = False, keep_all: bool = False):
        self.bound, self.budget, self.total = bound, budget, total  # total: frames expected, if known
        self.crop, self.keep_all = crop, keep_all
        self.out: list[list] = []  # [image, ms]
        self.step = 1  # keep every step-th frame, each shown for the time of the ones dropped
        self.count = 0
        self.size = 0

    def add(self, img: QImage, ms: int):
        if self.count % self.step:
            self.out[-1][1] += ms
        else:
            img = _fit(img, self.bound, self.crop)
            if not self.out and self.budget is not None and self.total > 1:
                # Knowing how many are coming, pick the step now rather than halving later.
                keep = max(1, self.budget // (img.sizeInBytes() or 1))
                self.step = -(-self.total // keep)  # ceil
                if self.step > 1 and self.keep_all:
                    raise TooBig
            self.out.append([img, ms])
            self.size += img.sizeInBytes()
            while self.budget is not None and self.size > self.budget and len(self.out) > 1:
                if self.keep_all:
                    raise TooBig
                self.out = [[self.out[i][0], sum(ms for _, ms in self.out[i:i + 2])]
                            for i in range(0, len(self.out), 2)]
                self.step *= 2
                self.size = sum(img.sizeInBytes() for img, _ in self.out)
        self.count += 1

    def frames(self) -> list[tuple[QImage, int]]:
        return [(img, ms) for img, ms in self.out]


def _apng_iter(data: bytes, max_frames: int = MAX_FRAMES):
    """(count, frames) of an animated PNG: how many frames it has, and an iterator of each one
    composited to full size with its delay in ms.

    Qt reads only the first image of an APNG, so each frame is rebuilt as a plain PNG
    (the file's header and palette, the frame's data) and decoded by Qt, then laid
    over the canvas with the frame's blend and dispose rules. An ordinary PNG gives
    one frame; anything unreadable gives none.
    """
    if not data.startswith(PNG_SIG):
        return 0, iter(())
    ihdr = None
    shared: list[bytes] = []  # PLTE, tRNS, colour info: needed by every frame
    frames: list[dict] = []
    cur = None
    seen_idat = False
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            ihdr = body
        elif kind == b"fcTL" and len(body) >= 26:
            _seq, w, h, x, y, dn, dd, dispose, blend = struct.unpack(">IIIIIHHBB", body[:26])
            cur = {"w": w, "h": h, "x": x, "y": y, "ms": _delay(dn, dd), "dispose": dispose,
                   "blend": blend, "data": []}
            frames.append(cur)
        elif kind == b"IDAT":
            seen_idat = True
            if cur is not None:  # the default image is a frame only when an fcTL comes before it
                cur["data"].append(body)
        elif kind == b"fdAT":
            if cur is not None:
                cur["data"].append(body[4:])
        elif kind in (b"acTL", b"IEND"):
            pass
        elif not seen_idat:
            shared.append(_chunk(kind, body))
    if ihdr is None:
        return 0, iter(())
    frames = [f for f in frames if f["data"]][:max_frames]
    if not frames:
        img = QImage.fromData(data)
        return (0, iter(())) if img.isNull() else (1, iter([(img, 0)]))

    def gen():
        width, height = struct.unpack(">II", ihdr[:8])
        canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        for i, f in enumerate(frames):
            png = (PNG_SIG + _chunk(b"IHDR", struct.pack(">II", f["w"], f["h"]) + ihdr[8:]) + b"".join(shared)
                   + _chunk(b"IDAT", b"".join(f["data"])) + _chunk(b"IEND", b""))
            img = QImage.fromData(png, "PNG")
            if img.isNull():
                return
            area = QRect(f["x"], f["y"], f["w"], f["h"])
            dispose = 1 if f["dispose"] == 2 and i == 0 else f["dispose"]
            before = canvas.copy(area) if dispose == 2 else None
            p = QPainter(canvas)
            if f["blend"] == 0:  # APNG_BLEND_OP_SOURCE: replace the area, transparency and all
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.drawImage(area.topLeft(), img)
            p.end()
            yield canvas.copy(), f["ms"]
            if dispose == 1:  # clear the area for the next frame
                p = QPainter(canvas)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.fillRect(area, Qt.GlobalColor.transparent)
                p.end()
            elif dispose == 2 and before is not None:  # put back what was there
                p = QPainter(canvas)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.drawImage(area.topLeft(), before)
                p.end()

    return len(frames), gen()


def _reader_iter(data: bytes, max_frames: int = MAX_FRAMES):
    """(count, frames) of a format Qt animates itself (WebP, GIF)."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    reader.setDecideFormatFromContent(True)
    count = min(max(reader.imageCount(), 0), max_frames)

    def gen(buf=buf):  # keeps the buffer the reader reads from alive
        n = 0
        while n < max_frames:
            img = reader.read()
            if img.isNull():
                return
            yield img, max(reader.nextImageDelay(), 0) or 100
            n += 1
            if not reader.supportsAnimation():
                return

    return count, gen()


def frame_iter(data: bytes, max_frames: int = MAX_FRAMES):
    """(count, frames) of an animated file (APNG, WebP or GIF, whatever its name)."""
    return (_apng_iter if data.startswith(PNG_SIG) else _reader_iter)(data, max_frames)


def apng_frames(data: bytes, max_frames: int = MAX_FRAMES, bound: QSize | None = None,
                budget: int | None = None) -> list[tuple[QImage, int]]:
    """The frames of an animated PNG, composited to full size, with each one's delay in ms
    (see _apng_iter). `bound` and `budget` work as in _Frames."""
    count, frames = _apng_iter(data, max_frames)
    out = _Frames(bound, budget, count)
    for img, ms in frames:
        out.add(img, ms)
    return out.frames()


class _Gate:
    """A byte budget shared by the decode threads (see DECODE_LIMIT). Callers take turns in
    the order they asked, so a big decode waiting for room isn't starved by small ones."""

    def __init__(self, limit: int):
        self.limit = limit
        self.held = 0
        self.running = 0
        self.peak = 0  # the most ever held at once (for tests)
        self.cond = threading.Condition()
        self._line: deque = deque()

    def acquire(self, need: int, cancel: threading.Event | None = None) -> bool:
        """Wait for `need` bytes (or to be alone, when it's more than the whole limit). False if cancelled."""
        me = object()
        with self.cond:
            self._line.append(me)
            try:
                while not (self._line[0] is me and (self.running == 0 or self.held + need <= self.limit)):
                    if cancel is not None and cancel.is_set():
                        return False
                    self.cond.wait(0.1)
                self.held += need
                self.running += 1
                self.peak = max(self.peak, self.held)
                return True
            finally:
                self._line.remove(me)
                self.cond.notify_all()

    def release(self, need: int):
        with self.cond:
            self.held -= need
            self.running -= 1
            self.cond.notify_all()


_gate = _Gate(DECODE_LIMIT)


def decode_need(path: str, bound: QSize | None, budget: int, crop=False) -> int:
    """The most memory decoding this file can take: the file (read, and copied once more for
    the decoder), four full-size pictures (an APNG's canvas, its copy, the dispose backup and
    the frame decoded), a frame being shrunk, and the frames kept. Unknown size: the whole limit."""
    try:
        file = os.path.getsize(path)
    except OSError:
        return 0
    reader = QImageReader(path)
    reader.setDecideFormatFromContent(True)
    size = reader.size()
    if not size.isValid() or size.isEmpty():
        return max(_gate.limit, 2 * file + budget)
    full = size.width() * size.height() * 4
    fitted = full
    if bound is not None and not bound.isEmpty():
        s = size.scaled(bound, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        if crop or s.width() < size.width():
            fitted = s.width() * s.height() * 4
    return 2 * file + 4 * full + 2 * fitted + budget


def decode_frames(path: str, bound: QSize | None = None, max_frames: int = MAX_FRAMES,
                  budget: int | None = None, crop: bool | str = False, keep_all: bool = False,
                  cancel: threading.Event | None = None, share: list | None = None) -> list[tuple[QImage, int]] | None:
    """Worker: every frame of an animated file (APNG, WebP or GIF, whatever its name), scaled
    to cover `bound` (and cut to it, with `crop`), within `budget` bytes (FRAME_BUDGET if not
    given). A still image gives one frame. With `keep_all`, None when they won't all fit.
    It waits for room under DECODE_LIMIT first; once `cancel` is set it stops and gives [].
    With `share` (a one-item list), the budget is taken from TOTAL_BUDGET as it starts, held
    in share[0], and cut to what the frames kept take as it ends."""
    budget = budget or FRAME_BUDGET
    need = decode_need(path, bound, budget, crop)
    if not _gate.acquire(need, cancel):
        return []
    out = []
    try:
        if share is not None:
            budget = _reserve_into(share, budget, cancel)
            if not budget:
                return []  # cancelled
        # Its working pictures and the file go when _decode returns, before the room is given back.
        out = _decode(path, bound, max_frames, budget, crop, keep_all, cancel)
        return out
    finally:
        if share is not None:
            _cut_share(share, sum(img.sizeInBytes() for img, _ in out) if out else 0)
        _gate.release(need)


def _decode(path, bound, max_frames, budget, crop, keep_all, cancel):
    if cancel is not None and cancel.is_set():
        return []
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    count, frames = frame_iter(data, max_frames)
    out = _Frames(bound, budget, count, crop, keep_all)
    try:
        for img, ms in frames:
            if cancel is not None and cancel.is_set():
                return []
            out.add(img, ms)
    except TooBig:
        return None
    return out.frames()


class _Stream:
    """Decodes an animation over and over on a thread of its own, with only a few frames
    ready ahead: for one too big to hold every frame of (a big banner), which then plays
    every frame at full size in a few megabytes, as Steam does."""

    AHEAD = 4

    def __init__(self, path: str, bound: QSize | None, crop: bool):
        self.q: queue.Queue = queue.Queue(maxsize=self.AHEAD)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(path, bound, crop), daemon=True)
        self.thread.start()
        _streams.add(self)
        app = QCoreApplication.instance()
        if app is not None and not getattr(app, "_streams_closed_on_quit", False):
            app._streams_closed_on_quit = True
            app.aboutToQuit.connect(_close_streams)  # none left decoding while Python shuts down

    def _run(self, path, bound, crop):
        try:
            data = Path(path).read_bytes()
        except OSError:
            return
        while not self.done.is_set():
            n = 0
            for img, ms in frame_iter(data)[1]:
                item = (_fit(img, bound, crop), ms)
                while not self.done.is_set():
                    try:
                        self.q.put(item, timeout=0.2)
                        break
                    except queue.Full:
                        pass
                if self.done.is_set():
                    return
                n += 1
            if n < 2:
                return  # a still, or broken: nothing to loop

    def get(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        self.done.set()


_streams: weakref.WeakSet = weakref.WeakSet()


def _close_streams():
    for st in list(_streams):
        st.close()
    for st in list(_streams):
        st.thread.join(0.5)


# ---- playing ---------------------------------------------------------------------


_pool = None


def decode_pool():
    """Up to DECODE_THREADS animations decode at once (fewer on a small machine), each within
    the shared DECODE_LIMIT, so a page full of them starts sooner without a memory spike."""
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(max(1, min(DECODE_THREADS, QThreadPool.globalInstance().maxThreadCount() // 2)))
        workers._pools["decode"] = _pool  # so quitting waits for it too (workers.shutdown)
    return _pool


# ---- every animation's frames, within TOTAL_BUDGET -------------------------------

_kept = 0
_kept_lock = threading.Lock()  # a share can also be given back by the garbage collector


def _release_share(share: list):
    global _kept
    with _kept_lock:
        _kept = max(0, _kept - share[0])
        share[0] = 0


def _reserve_into(share: list, want: int, cancel: threading.Event | None) -> int:
    """Reserve into `share`, unless the animation was retired (its share is given back then)."""
    global _kept
    with _kept_lock:
        if cancel is not None and cancel.is_set():
            return 0
        give = max(MIN_SHARE, min(want, TOTAL_BUDGET - _kept))
        _kept += give
        share[0] += give
        return give


def _cut_share(share: list, used: int):
    """Keep only `used` bytes of a share (all of it if it was given back meanwhile)."""
    global _kept
    with _kept_lock:
        if share[0] > used:
            _kept = max(0, _kept - (share[0] - used))
            share[0] = used


def frames_kept() -> int:
    """Bytes of TOTAL_BUDGET reserved by animations now."""
    return _kept


class Motion(QObject):
    """Plays something animated; `frame` brings each picture as it's due."""

    frame = Signal(QPixmap)
    dead = False

    def start(self):
        pass

    def stop(self):
        pass

    def retire(self):
        """Stop for good: nothing more is emitted, whatever is still on its way, and it's deleted."""
        if self.dead:
            return
        self.dead = True
        self.stop()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                self.frame.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.deleteLater()


class FrameMotion(Motion):
    """An animated image file, decoded off the GUI thread, then looped.

    Every frame is kept when they fit `budget` (as much of it as TOTAL_BUDGET has left). When
    they don't and `stream` is set (a big banner), it plays from a _Stream instead, every frame
    at full size; otherwise frames are dropped to fit (each shown for the time of the ones
    dropped). Frames come when they're due by the clock, so drawing them doesn't slow the
    animation down. Retired, its decode is dropped if it hasn't started and stopped if it has."""

    def __init__(self, path: str, bound: QSize | None = None, budget: int | None = None,
                 crop: bool | str = False, stream: bool = False):
        super().__init__()
        self.path, self.bound, self.crop = path, bound, crop
        self.frames: list[tuple[QPixmap, int]] = []
        self.stream: _Stream | None = None
        self.i = 0
        self.due = 0.0
        self.playing = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._next)
        self._share = [0]  # bytes of TOTAL_BUDGET it holds, reserved as its decode starts
        weakref.finalize(self, _release_share, self._share)  # given back even if never retired
        self._cancel = threading.Event()
        self._task = workers.run(decode_frames, path, bound, budget=budget, crop=crop, keep_all=stream,
                                 cancel=self._cancel, share=self._share, on_done=self._decoded,
                                 on_error=lambda _m: None, pool=decode_pool())

    def _decoded(self, frames):
        self._task = None
        if self.dead:
            return
        if frames is None:  # too big to keep every frame of: stream it (only a few frames held)
            self.stream = _Stream(self.path, self.bound, self.crop)
            self.due = time.monotonic()
            if self.playing:
                self.timer.start(0)
            return
        # One at a time, so each image goes as its pixmap comes rather than holding both lists.
        frames.reverse()
        while frames:
            img, ms = frames.pop()
            self.frames.append((QPixmap.fromImage(img), ms))
            del img
        if self.frames:
            self.frame.emit(self.frames[0][0])
            self.due = time.monotonic() + self.frames[0][1] / 1000
            if self.playing:
                self._schedule()

    def _schedule(self):
        if self.stream is not None or len(self.frames) > 1:
            self.timer.start(max(0, round((self.due - time.monotonic()) * 1000)))

    def _next(self):
        if self.dead:
            return
        now = time.monotonic()
        if self.stream is not None:
            f = self.stream.get()
            if f is None:  # the decoder is behind: try again shortly
                self.timer.start(4)
                return
            img, ms = f
            self.frame.emit(QPixmap.fromImage(img))
        elif self.frames:
            self.i = (self.i + 1) % len(self.frames)
            self.frame.emit(self.frames[self.i][0])
            ms = self.frames[self.i][1]
        else:
            return
        # The next one is due this frame's time after this one was due, not after it was
        # drawn; a long stall (the window hidden, say) starts the clock again.
        self.due = max(self.due, now - 0.25) + ms / 1000
        self._schedule()

    def start(self):
        if not self.playing and not self.dead:
            self.playing = True
            self.due = max(self.due, time.monotonic())
            self._schedule()

    def stop(self):
        self.playing = False
        self.timer.stop()

    def retire(self):
        if self.dead:
            return
        self._cancel.set()
        task, self._task = self._task, None
        if task is not None and decode_pool().tryTake(task):
            workers._live.discard(task)  # still waiting its turn: it never runs
        if self.stream is not None:
            self.stream.close()
        self.frames = []
        _release_share(self._share)
        super().retire()

    def deleteLater(self):
        if self.stream is not None:
            self.stream.close()
        super().deleteLater()


_video: bool | None = None


def video_supported() -> bool:
    """Whether Qt can play SteamGridDB's video thumbnails here (Qt Multimedia with FFmpeg)."""
    global _video
    if _video is None:
        try:
            from PySide6.QtMultimedia import QMediaFormat

            fmts = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
            _video = QMediaFormat.FileFormat.Matroska in fmts or QMediaFormat.FileFormat.WebM in fmts
        except Exception:
            _video = False
    return _video


class VideoMotion(Motion):
    """A silent, looping video (SteamGridDB's thumbnail for an animated option)."""

    def __init__(self, path: str):
        super().__init__()
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

        self.player = QMediaPlayer(self)  # no audio output: silent
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.setSource(QUrl.fromLocalFile(path))

    def _frame(self, f):
        if self.dead:
            return
        img = f.toImage()
        if not img.isNull():
            self.frame.emit(QPixmap.fromImage(img))

    def start(self):
        if not self.dead:
            self.player.play()

    def stop(self):
        self.player.pause()

    def retire(self):
        if not self.dead:
            self.player.stop()  # lets go of the decoder, not just pauses it
        super().retire()


def make(kind: str, path: str, bound: QSize | None = None, budget: int | None = None,
         crop: bool | str = False, stream: bool = False) -> Motion:
    """Play a video thumbnail, or an animated image file decoded to cover `bound` within `budget` bytes
    (cut to it, with `crop`; streamed a few frames ahead, with `stream`, when every frame won't fit)."""
    return VideoMotion(path) if kind == "video" else FrameMotion(path, bound, budget, crop, stream)


# ---- first frames, for places that show a still ---------------------------------

GRAB_MAX = 2  # video players started at once just to grab a first frame
_grab_q: deque = deque()  # (path, deliver, wanted) waiting their turn
_grabbing: set[Motion] = set()


def _first_image(path: str, bound: QSize | None) -> QImage:
    """Worker: the first picture of an animated file, shrunk as it decodes to cover `bound`."""
    r = QImageReader(path)
    r.setDecideFormatFromContent(True)
    s = r.size()
    if bound is not None and s.isValid() and s.width() > bound.width() and s.height() > bound.height():
        r.setScaledSize(s.scaled(bound, Qt.AspectRatioMode.KeepAspectRatioByExpanding))
    return r.read()


def first_frame(kind: str, path: str, deliver, bound: QSize | None = None, wanted=None):
    """Call `deliver(QPixmap)` with the first picture of an animation, for places that show a still.
    An image file decodes off the GUI thread; a video plays just until its first frame, at most
    GRAB_MAX at a time. `wanted()`, if given, is asked before starting, so ones no longer needed are skipped."""
    if kind != "video":
        def done(img):
            if not img.isNull() and (wanted is None or wanted()):
                deliver(QPixmap.fromImage(img))

        workers.run(_first_image, path, bound, kind="cpu", on_done=done, on_error=lambda _m: None)
        return
    _grab_q.append((path, deliver, wanted))
    _pump_grabs()


def _pump_grabs():
    while len(_grabbing) < GRAB_MAX and _grab_q:
        path, deliver, wanted = _grab_q.popleft()
        if wanted is not None and not wanted():
            continue
        _grab(path, deliver)


def _grab(path: str, deliver):
    m = VideoMotion(path)
    _grabbing.add(m)

    def finish(pm=None, m=m):
        if m not in _grabbing:
            return
        _grabbing.discard(m)
        m.retire()
        QTimer.singleShot(0, _pump_grabs)
        if pm is not None:
            deliver(pm)

    m.frame.connect(finish)
    m.player.errorOccurred.connect(lambda *_: finish())  # a video that never shows a frame
    QTimer.singleShot(10000, m, finish)  # mustn't keep a player running
    m.start()
