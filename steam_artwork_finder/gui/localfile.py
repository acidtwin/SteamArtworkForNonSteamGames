"""Pictures from the user's own files or the clipboard, made ready to use as a piece of artwork.

Everything here but `source` is safe to run off the GUI thread: it works on bytes and QImages only.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from ..artwork import Slot, is_animated
from ..pending import LocalFile, Pick
from .widgets import _largest_frame

MAX_BYTES = 64 * 1024 * 1024
MAX_SIDE = 3840  # bigger stills are scaled down to this; Steam never shows them larger
PREVIEW_SIDE = 1920  # the preview on the game page, so a huge photo doesn't cost hundreds of MB
SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".ico", ".bmp", ".gif", ".tif", ".tiff")
FILTER = "Pictures (" + " ".join(f"*{s}" for s in SUFFIXES) + ");;All files (*)"
# Formats Steam reads as they are; anything else becomes a PNG.
KEEP = {"png", "jpeg", "jpg", "webp"}
KEEP_ICON = {"png", "ico"}
MIME = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp", "ico": "image/x-icon"}
BEST = {"grid": (600, 900), "wide": (920, 430), "hero": (1920, 620), "icon": (256, 256)}


class FileProblem(Exception):
    """A picture that can't be used; the message is shown as it is."""


@dataclass
class Loaded:
    pick: Pick
    preview: QImage  # no bigger than PREVIEW_SIDE
    note: str  # a gentle warning about the shape or size, or ""


def _reader(data: bytes) -> tuple[QImageReader, QBuffer]:
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    return reader, buf  # the buffer must outlive the reader


def _fit(size: QSize, side: int) -> QSize:
    if max(size.width(), size.height()) <= side:
        return size
    return size.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio)


def _encode(img: QImage, fmt: str, quality: int = -1) -> bytes:
    arr = QByteArray()
    buf = QBuffer(arr)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, fmt, quality):
        raise FileProblem("Couldn't convert the picture.")
    buf.close()
    return bytes(arr.data())


def advice(slot: Slot, w: int, h: int, see_through: bool) -> str:
    """A word of warning when a picture is far off for its piece; it can still be used."""
    name = slot.label.lower()
    if slot.key == "logo":
        if not see_through:
            return "This logo has no see-through background, so it will show as a box over the banner."
        return ""
    bw, bh = BEST[slot.key]
    ratio = (w / h) / slot.aspect
    if ratio > 1.25 or ratio < 0.8:
        shape = "wider" if ratio > 1 else "taller"
        return f"This picture is {shape} than a {name} ({bw} × {bh}), so Steam will crop or stretch it."
    if w < bw / 2:
        return f"This picture is small for a {name} ({bw} × {bh} is best), so it may look blurry."
    return ""


def load(slot: Slot, data: bytes, name: str, path: str = "") -> Loaded:
    """Worker: check a picture, convert it if Steam can't read it as it is, and make the pick."""
    if len(data) > MAX_BYTES:
        raise FileProblem(f"{name} is too big to use (over {MAX_BYTES // 2**20} MB).")
    reader, _buf = _reader(data)
    if not reader.canRead():
        raise FileProblem(f"{name} isn't a picture this app can read.")
    fmt = bytes(reader.format().data()).decode(errors="replace").lower()
    _largest_frame(reader)  # an .ico holds several sizes
    size = reader.size()
    moving = slot.animates and is_animated(data)
    keep = moving or (fmt in (KEEP_ICON if slot.key == "icon" else KEEP)
                      and size.isValid() and max(size.width(), size.height()) <= MAX_SIDE)
    if keep:
        if size.isValid():
            reader.setScaledSize(_fit(size, PREVIEW_SIDE))
        img = reader.read()
        if img.isNull():
            raise FileProblem(f"{name} couldn't be read: {reader.errorString()}")
        out, mime = data, MIME[fmt]
        w, h = (size.width(), size.height()) if size.isValid() else (img.width(), img.height())
    else:
        if size.isValid():
            reader.setScaledSize(_fit(size, MAX_SIDE))
        img = reader.read()
        if img.isNull():
            raise FileProblem(f"{name} couldn't be read: {reader.errorString()}")
        if fmt in ("jpeg", "jpg") and slot.key != "icon" and not img.hasAlphaChannel():
            out, mime = _encode(img, "JPG", 92), "image/jpeg"
        else:
            out, mime = _encode(img, "PNG"), "image/png"
        w, h = img.width(), img.height()
        if max(w, h) > PREVIEW_SIDE:
            img = img.scaled(_fit(img.size(), PREVIEW_SIDE), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    asset = LocalFile(-(zlib.crc32(out) or 1), path, path, w, h, mime, 0, "Your file", "",
                      path=path, moving=moving)
    return Loaded(Pick(asset, out), img, advice(slot, w, h, img.hasAlphaChannel()))


def load_path(slot: Slot, path: str) -> Loaded:
    """Worker: `load` for a file on disk."""
    p = Path(path)
    try:
        if p.stat().st_size > MAX_BYTES:
            raise FileProblem(f"{p.name} is too big to use (over {MAX_BYTES // 2**20} MB).")
        data = p.read_bytes()
    except OSError as e:
        raise FileProblem(f"Couldn't open {p.name}: {e.strerror or e}") from e
    return load(slot, data, p.name, str(p))


def load_image(slot: Slot, image: QImage) -> Loaded:
    """Worker: `load` for a picture pasted or dropped without a file."""
    if image.isNull():
        raise FileProblem("There's no picture on the clipboard to paste.")
    return load(slot, _encode(image, "PNG"), "The pasted picture")


def source(mime: QMimeData | None) -> dict | None:
    """What a drop or the clipboard holds that could be used: {"path": ...}, {"image": ...} or None."""
    if mime is None:
        return None
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(SUFFIXES):
                return {"path": url.toLocalFile()}
    if mime.hasImage():
        img = mime.imageData()
        if isinstance(img, QPixmap):
            img = img.toImage()
        if isinstance(img, QImage) and not img.isNull():
            return {"image": img}
    return None
