"""Animated artwork: decoding APNG and animated WebP into frames, and playing them (or
SteamGridDB's video thumbnails) into an ArtView."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRect, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap

from . import workers

PNG_SIG = b"\x89PNG\r\n\x1a\n"
MAX_FRAMES = 300
FRAME_BUDGET = 48 * 1024 * 1024  # bytes of decoded frames kept for one animation


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


class _Frames:
    """Collects an animation's frames as they're decoded, each scaled down to just cover
    `bound` straight away, and halves the frame rate whenever the frames kept pass
    `budget` bytes. Only one full-size frame is ever held, so a big animated banner
    can't eat gigabytes while it decodes."""

    def __init__(self, bound: QSize | None = None, budget: int | None = None, total: int = 0):
        self.bound, self.budget, self.total = bound, budget, total  # total: frames expected, if known
        self.out: list[list] = []  # [image, ms]
        self.step = 1  # keep every step-th frame, each shown for the time of the ones dropped
        self.count = 0
        self.size = 0

    def add(self, img: QImage, ms: int):
        if self.count % self.step:
            self.out[-1][1] += ms
        else:
            b = self.bound
            if b is not None and img.width() > b.width() and img.height() > b.height():
                img = img.scaled(b, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            if not self.out and self.budget is not None and self.total > 1:
                # Knowing how many are coming, pick the step now rather than halving later.
                keep = max(1, self.budget // (img.sizeInBytes() or 1))
                self.step = -(-self.total // keep)  # ceil
            self.out.append([img, ms])
            self.size += img.sizeInBytes()
            while self.budget is not None and self.size > self.budget and len(self.out) > 1:
                self.out = [[self.out[i][0], sum(ms for _, ms in self.out[i:i + 2])]
                            for i in range(0, len(self.out), 2)]
                self.step *= 2
                self.size = sum(img.sizeInBytes() for img, _ in self.out)
        self.count += 1

    def frames(self) -> list[tuple[QImage, int]]:
        return [(img, ms) for img, ms in self.out]


def apng_frames(data: bytes, max_frames: int = MAX_FRAMES, bound: QSize | None = None,
                budget: int | None = None) -> list[tuple[QImage, int]]:
    """The frames of an animated PNG, composited to full size, with each one's delay in ms.

    Qt reads only the first image of an APNG, so each frame is rebuilt as a plain PNG
    (the file's header and palette, the frame's data) and decoded by Qt, then laid
    over the canvas with the frame's blend and dispose rules. An ordinary PNG gives
    one frame; anything unreadable gives none. `bound` and `budget` work as in _Frames.
    """
    if not data.startswith(PNG_SIG):
        return []
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
        return []
    width, height = struct.unpack(">II", ihdr[:8])
    frames = [f for f in frames if f["data"]][:max_frames]
    out = _Frames(bound, budget, len(frames))
    if not frames:
        img = QImage.fromData(data)
        if not img.isNull():
            out.add(img, 0)
        return out.frames()

    canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    for i, f in enumerate(frames):
        png = (PNG_SIG + _chunk(b"IHDR", struct.pack(">II", f["w"], f["h"]) + ihdr[8:]) + b"".join(shared)
               + _chunk(b"IDAT", b"".join(f["data"])) + _chunk(b"IEND", b""))
        img = QImage.fromData(png, "PNG")
        if img.isNull():
            break
        area = QRect(f["x"], f["y"], f["w"], f["h"])
        dispose = 1 if f["dispose"] == 2 and i == 0 else f["dispose"]
        before = canvas.copy(area) if dispose == 2 else None
        p = QPainter(canvas)
        if f["blend"] == 0:  # APNG_BLEND_OP_SOURCE: replace the area, transparency and all
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(area.topLeft(), img)
        p.end()
        out.add(canvas.copy(), f["ms"])
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
    return out.frames()


def _reader_frames(data: bytes, max_frames: int, bound: QSize | None = None,
                   budget: int | None = None) -> list[tuple[QImage, int]]:
    """Frames of a format Qt animates itself (WebP, GIF)."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    reader.setDecideFormatFromContent(True)
    out = _Frames(bound, budget, min(max(reader.imageCount(), 0), max_frames))
    while out.count < max_frames:
        img = reader.read()
        if img.isNull():
            break
        out.add(img, max(reader.nextImageDelay(), 0) or 100)
        if not reader.supportsAnimation():
            break
    return out.frames()


def decode_frames(path: str, bound: QSize | None = None, max_frames: int = MAX_FRAMES) -> list[tuple[QImage, int]]:
    """Worker: every frame of an animated file (APNG, WebP or GIF, whatever its name), scaled
    to cover `bound`, within FRAME_BUDGET. A still image gives one frame."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    read = apng_frames if data.startswith(PNG_SIG) else _reader_frames
    return read(data, max_frames, bound, FRAME_BUDGET)


# ---- playing ---------------------------------------------------------------------


_pool = None


def _decode_pool():
    """Animations decode one at a time, so opening a page full of them can't decode them all at once."""
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(1)
    return _pool


class Motion(QObject):
    """Plays something animated; `frame` brings each picture as it's due."""

    frame = Signal(QPixmap)

    def start(self):
        pass

    def stop(self):
        pass


class FrameMotion(Motion):
    """An animated image file, decoded off the GUI thread, then looped."""

    def __init__(self, path: str, bound: QSize | None = None):
        super().__init__()
        self.frames: list[tuple[QPixmap, int]] = []
        self.i = 0
        self.playing = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._next)
        workers.run(decode_frames, path, bound, on_done=self._decoded, on_error=lambda _m: None,
                    pool=_decode_pool())

    def _decoded(self, frames):
        self.frames = [(QPixmap.fromImage(img), ms) for img, ms in frames]
        if self.frames:
            self.frame.emit(self.frames[0][0])
            if self.playing:
                self._schedule()

    def _schedule(self):
        if len(self.frames) > 1:
            self.timer.start(self.frames[self.i][1])

    def _next(self):
        if not self.frames:
            return
        self.i = (self.i + 1) % len(self.frames)
        self.frame.emit(self.frames[self.i][0])
        self._schedule()

    def start(self):
        if not self.playing:
            self.playing = True
            self._schedule()

    def stop(self):
        self.playing = False
        self.timer.stop()


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
        img = f.toImage()
        if not img.isNull():
            self.frame.emit(QPixmap.fromImage(img))

    def start(self):
        self.player.play()

    def stop(self):
        self.player.pause()


def make(kind: str, path: str, bound: QSize | None = None) -> Motion:
    return VideoMotion(path) if kind == "video" else FrameMotion(path, bound)


def first_frame(kind: str, path: str, deliver):
    """Call `deliver(QPixmap)` with the first picture of an animation, for places that show a still."""
    if kind != "video":
        img = QImageReader(path)
        img.setDecideFormatFromContent(True)
        deliver(QPixmap.fromImage(img.read()))
        return
    m = VideoMotion(path)
    _grabbing.add(m)

    def got(pm, m=m):
        if m not in _grabbing:
            return
        _grabbing.discard(m)
        m.stop()
        m.deleteLater()
        deliver(pm)

    def give_up(m=m):  # a video that never shows a frame mustn't keep a player running
        if m in _grabbing:
            _grabbing.discard(m)
            m.stop()
            m.deleteLater()

    m.frame.connect(got)
    m.player.errorOccurred.connect(lambda *_: give_up())
    QTimer.singleShot(10000, m, give_up)
    m.start()


_grabbing: set[Motion] = set()
