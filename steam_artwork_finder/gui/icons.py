"""Small SVG icons drawn in a colour, cached per size and device pixel ratio."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).with_name("icons")
_svg: dict[str, str] = {}
_pix: dict[tuple, QPixmap] = {}


def svg_text(name: str) -> str:
    if name not in _svg:
        _svg[name] = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    return _svg[name]


def pixmap(name: str, size: int, color: QColor | str | None = None, dpr: float = 1.0) -> QPixmap:
    """The icon rendered at `size` logical pixels, tinted with `color` (for currentColor SVGs)."""
    col = QColor(color).name() if color is not None else None
    key = (name, size, col, round(dpr, 2))
    pm = _pix.get(key)
    if pm is None:
        svg = svg_text(name)
        if col:
            svg = svg.replace("currentColor", col)
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, pm.width(), pm.height()))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _pix[key] = pm
    return pm


def icon(name: str, size: int, color: QColor | str | None = None) -> QIcon:
    """A QIcon with 1x and 2x renderings, for tool buttons and window icons."""
    ic = QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(pixmap(name, size, color, dpr))
    return ic


def draw(p: QPainter, name: str, rect, color: QColor | str | None = None):
    """Paint the icon into `rect` (QRect/QRectF) on an existing painter."""
    r = QRectF(rect)
    dpr = p.device().devicePixelRatioF() if p.device() else 1.0
    pm = pixmap(name, int(r.width()), color, dpr)
    p.drawPixmap(r.topLeft(), pm)


def app_icon() -> QIcon:
    return QIcon(str(ICON_DIR / "app.svg"))


def app_pixmap(size: int, dpr: float = 1.0) -> QPixmap:
    return pixmap("app", size, None, dpr)
