"""A stand-in icon for games SteamGridDB has no icon for, made from their other artwork."""

from __future__ import annotations

from itertools import count

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QImage, QPainter

from ..sgdb import Asset

SIZE = 256  # Steam shows icons up to this size
SOURCES = (("grid", "cover"), ("hero", "banner"), ("wide", "wide cover"), ("logo", "logo"))
GENERATED = "generated"  # the style of a made-up icon's asset
_ids = count(-1, -1)  # SteamGridDB ids are positive, so these never clash with a real option


def square_icon(data: bytes, contain: bool = False) -> bytes | None:
    """A SIZE × SIZE PNG of the picture: cropped to fill (cover), or whole with clear edges (contain)."""
    img = QImage.fromData(data)
    if img.isNull():
        return None
    mode = Qt.AspectRatioMode.KeepAspectRatio if contain else Qt.AspectRatioMode.KeepAspectRatioByExpanding
    img = img.scaled(SIZE, SIZE, mode, Qt.TransformationMode.SmoothTransformation)
    out = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    src = QRect((img.width() - SIZE) // 2, (img.height() - SIZE) // 2, SIZE, SIZE) if not contain else img.rect()
    dst = QRect((SIZE - img.width()) // 2, (SIZE - img.height()) // 2, img.width(), img.height()) \
        if contain else out.rect()
    p.drawImage(dst, img, src)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    out.save(buf, "PNG")
    return bytes(buf.data())


def icon_asset(source: str) -> Asset:
    """The asset a made-up icon is staged as; `source` names the picture it came from."""
    return Asset(next(_ids), "", "", SIZE, SIZE, "image/png", 0, GENERATED, source)
