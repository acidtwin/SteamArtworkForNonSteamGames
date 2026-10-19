"""Procedural artwork for made-up games, for the README screenshots.

Every game is a small painted scene. The same scene is drawn at every artwork shape (cover, wide
cover, banner, icon), with the title laid over it where Steam's own artwork usually has one, and a
transparent logo on its own. `t` in [0, 1) animates a scene in a seamless loop, and `variant`
re-colours it so SteamGridDB's "other options" look like other artists' takes on the same game.
"""
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)

TAU = math.tau

# name: (scene, palette [sky top, sky bottom, accent, far, mid, near], title font, title style)
GAMES = {
    "Lumen Drift": ("synth", ["#170b3b", "#ff4f8b", "#ffd36b", "#5b1f7a", "#2a0d4d", "#120626"],
                    ("Inter Display", 900, 0.04, True), "#fff3fb"),
    "Hollow Pines": ("forest", ["#081a2e", "#2f6f7e", "#f4f0d0", "#1e4a5a", "#11303f", "#071720"],
                     ("DejaVu Serif", 700, 0.02, False), "#f4f0d0"),
    "Neon Requiem": ("city", ["#12062b", "#a3196e", "#3ff0ff", "#4a1660", "#2a0c40", "#0c0418"],
                     ("Inter Display", 800, 0.18, True), "#3ff0ff"),
    "Saltwind": ("ocean", ["#ffb38a", "#ffe3b8", "#fff4d6", "#e07a6a", "#3a7fa0", "#1f4d6e"],
                 ("Bitstream Charter", 700, 0.06, True), "#173a52"),
    "Emberfall": ("lava", ["#0d0505", "#5a1408", "#ffb03a", "#2a0d08", "#1a0806", "#0a0303"],
                  ("Caladea", 700, 0.12, True), "#ffd28a"),
    "Starward Bound": ("planet", ["#04061a", "#14205a", "#ff9a4a", "#3b6cff", "#ffd2a0", "#0a0f2e"],
                       ("Inter Display", 800, 0.10, True), "#ffffff"),
    "Glass Tide": ("ocean", ["#0d3b4f", "#5fd1c9", "#e9fffb", "#1c6b7a", "#0f5566", "#073140"],
                   ("Inter Display", 300, 0.22, True), "#e9fffb"),
    "Duneheart": ("dunes", ["#ffcf7a", "#ff8a4c", "#fff1c4", "#e86a3e", "#c44a2c", "#7a2418"],
                  ("Caladea", 700, 0.08, True), "#4a140c"),
    "Kestrel Run": ("mesa", ["#2a1650", "#ff6a3d", "#ffd38a", "#8a3a5a", "#5a2140", "#2a0f22"],
                    ("Inter Display", 900, 0.0, False), "#ffe7c2"),
    "Aurora Station": ("aurora", ["#020814", "#0b2a3a", "#5cffb0", "#b06cff", "#1a3a4a", "#0a1820"],
                       ("Inter Display", 600, 0.16, True), "#dfffee"),
    "Moth & Lantern": ("lantern", ["#0a0a14", "#2a2238", "#ffcc66", "#3a2e48", "#1e1828", "#0c0a12"],
                       ("DejaVu Serif", 700, 0.0, False), "#ffe2a0"),
    "Iron Orchard": ("lowpoly", ["#3a1c12", "#c4632e", "#9fd36a", "#6a2e18", "#2e3a1a", "#1a120c"],
                     ("Inter Display", 800, 0.06, True), "#f6ead2"),
    "Petal Circuit": ("bloom", ["#ffe6ef", "#e6e0ff", "#ff6fa3", "#7a6cff", "#ffb36b", "#3a2a5a"],
                      ("Inter Display", 700, 0.02, False), "#3a2a5a"),
    "Quiet Harbor": ("harbor", ["#0b1430", "#3a4a7a", "#ffe9a8", "#26335c", "#1a2448", "#0a1024"],
                     ("Bitstream Charter", 700, 0.04, False), "#ffe9a8"),
    "Voidline": ("void", ["#050505", "#1a0606", "#ff3b3b", "#ff3b3b", "#2a0a0a", "#000000"],
                 ("DejaVu Sans Mono", 700, 0.3, True), "#ff5a5a"),
    "Paper Kingdoms": ("paper", ["#bfe3f0", "#fff3d6", "#ff7a59", "#8fc7a8", "#5aa384", "#2f6e5c"],
                       ("Bitstream Charter", 700, 0.03, False), "#2f3b45"),
}


def col(c, a=None):
    q = QColor(c)
    if a is not None:
        q.setAlphaF(a)
    return q


def shift(c, deg):
    q = QColor(c)
    h, s, v, a = q.getHsvF()
    if h < 0:
        return q
    q.setHsvF(((h * 360 + deg) % 360) / 360, s, v, a)
    return q


def mix(a, b, f):
    a, b = QColor(a), QColor(b)
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * f, a.greenF() + (b.greenF() - a.greenF()) * f,
                           a.blueF() + (b.blueF() - a.blueF()) * f)


def vgrad(p, r, stops):
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    for at, c in stops:
        g.setColorAt(at, col(c) if not isinstance(c, QColor) else c)
    p.fillRect(r, g)


def glow(p, cx, cy, r, c, a=0.6):
    g = QRadialGradient(cx, cy, r)
    g.setColorAt(0, col(c, a))
    g.setColorAt(0.4, col(c, a * 0.35))
    g.setColorAt(1, col(c, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QPointF(cx, cy), r, r)


def ridge_y(x, w, base, amp, rng_phases, rough=1.0):
    y = base
    for k, (f, ph, a) in enumerate(rng_phases):
        y += math.sin(x / w * f * TAU * rough + ph) * amp * a
    return y


def phases(seed, n=5):
    rng = random.Random(seed)
    return [(rng.uniform(0.6, 1.4) * (k + 1), rng.uniform(0, TAU), 1 / (k + 1) ** 1.1) for k in range(n)]


def ridge(p, w, h, base, amp, seed, c, rough=1.0, peaks=False):
    ph = phases(seed)
    path = QPainterPath(QPointF(0, h))
    steps = 120
    for i in range(steps + 1):
        x = w * i / steps
        y = ridge_y(x, w, base, amp, ph, rough)
        if peaks:
            y -= abs(math.sin(x / w * 3.3 * TAU * rough + seed)) ** 3 * amp * 0.9
        path.lineTo(x, y)
    path.lineTo(w, h)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c if isinstance(c, (QBrush, QLinearGradient)) else col(c))
    p.drawPath(path)
    return ph


def pines(p, w, h, base, amp, seed, c, size, density=1.0):
    ph = ridge(p, w, h, base + size * 0.3, amp, seed, c)
    rng = random.Random(seed * 7 + 1)
    p.setBrush(col(c))
    x = -size
    while x < w + size:
        y = ridge_y(x, w, base + size * 0.3, amp, ph)
        th = size * rng.uniform(0.7, 1.3)
        tw = th * rng.uniform(0.26, 0.34)
        tree = QPainterPath(QPointF(x, y - th))
        for tier in range(4):
            ty = y - th + th * (tier + 1) / 4
            ww = tw * (0.45 + 0.55 * (tier + 1) / 4)
            tree.lineTo(x + ww, ty)
            tree.lineTo(x + ww * 0.45, ty)
        for tier in reversed(range(4)):
            ty = y - th + th * (tier + 1) / 4
            ww = tw * (0.45 + 0.55 * (tier + 1) / 4)
            tree.lineTo(x - ww * 0.45, ty)
            tree.lineTo(x - ww, ty)
        tree.lineTo(x, y - th)
        p.drawPath(tree)
        p.drawRect(QRectF(x - tw * 0.08, y - th * 0.05, tw * 0.16, th * 0.25))
        x += tw * rng.uniform(0.9, 1.6) / density


def stars(p, w, h, n, seed, t, ymax=1.0, c="#ffffff", big=1.0):
    rng = random.Random(seed)
    p.setPen(Qt.PenStyle.NoPen)
    s = min(w, h) / 600
    for i in range(n):
        x, y = rng.uniform(0, w), rng.uniform(0, h * ymax) ** 1.0
        y = h * ymax * (y / (h * ymax)) ** 1.6
        r = rng.choice([0.6, 0.8, 1.0, 1.0, 1.4, 2.0]) * s * big
        tw = 0.55 + 0.45 * math.sin(TAU * (t * rng.choice([1, 2]) + rng.random()))
        p.setBrush(col(c, max(0.0, min(1.0, tw * rng.uniform(0.4, 1.0)))))
        p.drawEllipse(QPointF(x, y), r, r)
        if r > 1.6 * s:
            glow(p, x, y, r * 6, c, 0.25 * tw)


def fog(p, w, y0, y1, c, a=0.5):
    g = QLinearGradient(0, y0, 0, y1)
    g.setColorAt(0, col(c, 0))
    g.setColorAt(0.6, col(c, a))
    g.setColorAt(1, col(c, 0))
    p.fillRect(QRectF(0, y0, w, y1 - y0), g)


# ---- scenes -------------------------------------------------------------------------------------

def sc_synth(p, w, h, P, t):
    hz = h * 0.62
    vgrad(p, QRectF(0, 0, w, hz), [(0, P[0]), (0.55, P[3]), (1, P[1])])
    stars(p, w, hz, int(w * h / 4000), 3, t, 0.6)
    m = min(w, h)
    cx, cy, r = w * 0.5, hz - m * 0.2, m * 0.32
    glow(p, cx, cy, r * 2.2, P[1], 0.5)
    sun = QPainterPath()
    sun.addEllipse(QPointF(cx, cy), r, r)
    cut = QPainterPath()
    for k in range(7):
        frac = (k + (t % 1)) / 7
        y = cy + r * (0.05 + 0.95 * frac)
        bh = r * 0.02 + r * 0.075 * frac
        cut.addRect(QRectF(cx - r, y, 2 * r, bh))
    sun = sun.subtracted(cut)
    g = QLinearGradient(0, cy - r, 0, cy + r)
    g.setColorAt(0, col(P[2]))
    g.setColorAt(1, col(P[1]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawPath(sun)
    ridge(p, w, h, hz - m * 0.04, m * 0.07, 11, P[3], 1.4, peaks=True)
    ridge(p, w, h, hz, m * 0.03, 12, P[4], 2.2)
    p.fillRect(QRectF(0, hz, w, h - hz), col(P[5]))
    pen = QPen(col(P[1], 0.8), max(1.0, m / 300))
    p.setPen(pen)
    for k in range(14):
        f = ((k + t) / 14) ** 2.2
        y = hz + (h - hz) * f
        pen.setColor(col(P[1], 0.15 + 0.75 * f))
        p.setPen(pen)
        p.drawLine(QPointF(0, y), QPointF(w, y))
    pen.setColor(col(P[1], 0.6))
    p.setPen(pen)
    for k in range(-20, 21):
        p.drawLine(QPointF(w / 2 + k * m * 0.02, hz), QPointF(w / 2 + k * m * 0.25, h))
    fog(p, w, hz - m * 0.05, hz + m * 0.06, P[1], 0.55)


def sc_forest(p, w, h, P, t, lantern=False):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (0.75, P[1]), (1, P[1])])
    stars(p, w, h, int(w * h / 2500), 5, t, 0.55)
    if not lantern:
        mx, my, mr = w * 0.72, h * 0.36, m * 0.09
        glow(p, mx, my, mr * 5, P[2], 0.35)
        p.setBrush(col(P[2]))
        p.drawEllipse(QPointF(mx, my), mr, mr)
        p.setBrush(col(P[0], 0.12))
        p.drawEllipse(QPointF(mx - mr * 0.3, my - mr * 0.2), mr * 0.25, mr * 0.25)
        p.drawEllipse(QPointF(mx + mr * 0.35, my + mr * 0.3), mr * 0.15, mr * 0.15)
    ridge(p, w, h, h * 0.58, m * 0.09, 21, P[3], 1.2, peaks=True)
    fog(p, w, h * 0.52, h * 0.72, P[1], 0.6)
    pines(p, w, h, h * 0.70, m * 0.04, 22, P[4], m * 0.16)
    fog(p, w, h * 0.66, h * 0.86, P[1], 0.45)
    if lantern:
        lx, ly = w * 0.5, h * 0.66
        pulse = 0.85 + 0.15 * math.sin(TAU * t)
        glow(p, lx, ly, m * 0.55 * pulse, P[2], 0.55)
        glow(p, lx, ly, m * 0.12, "#fff6dd", 0.9)
        rng = random.Random(9)
        for i in range(40):
            a0 = rng.uniform(0, TAU)
            rad = m * rng.uniform(0.08, 0.42)
            a = a0 + TAU * t * rng.choice([1, -1])
            x, y = lx + math.cos(a) * rad, ly + math.sin(a) * rad * 0.6 - m * 0.05
            glow(p, x, y, m * 0.012, P[2], 0.9 * (0.5 + 0.5 * math.sin(TAU * (t * 2) + a0)))
        # a moth silhouette
        mxp, myp = lx + m * 0.12 * math.cos(TAU * t), ly - m * 0.2 + m * 0.03 * math.sin(2 * TAU * t)
        flap = 0.6 + 0.4 * abs(math.sin(TAU * t * 4))
        p.setBrush(col(P[5]))
        for sgn in (-1, 1):
            wing = QPainterPath(QPointF(mxp, myp))
            wing.cubicTo(mxp + sgn * m * 0.07 * flap, myp - m * 0.06, mxp + sgn * m * 0.09 * flap, myp + m * 0.02,
                         mxp, myp + m * 0.02)
            p.drawPath(wing)
    pines(p, w, h, h * 0.86, m * 0.03, 23, P[5], m * 0.24, 1.2)
    if not lantern:
        rng = random.Random(4)
        for i in range(18):
            x0, y0 = rng.uniform(0, w), rng.uniform(h * 0.7, h * 0.95)
            x = x0 + math.sin(TAU * (t + i / 18)) * m * 0.02
            glow(p, x, y0, m * 0.012, "#d8ff8a", 0.8 * (0.5 + 0.5 * math.sin(TAU * (t + i * 0.37))))


def sc_city(p, w, h, P, t):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (0.7, P[1]), (1, P[1])])
    stars(p, w, h, int(w * h / 6000), 7, t, 0.4)
    cx, cy, r = w * 0.5, h * 0.42, m * 0.3
    glow(p, cx, cy, r * 1.8, P[2], 0.25)
    p.setPen(QPen(col(P[2]), m * 0.012))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setPen(Qt.PenStyle.NoPen)
    rng = random.Random(31)
    for layer, (c, base, hmin, hmax, lit) in enumerate(((P[3], 0.72, 0.15, 0.4, 0.15), (P[4], 0.8, 0.12, 0.42, 0.25),
                                                        (P[5], 0.9, 0.1, 0.5, 0.35))):
        x = -rng.uniform(0, m * 0.1)
        while x < w:
            bw = m * rng.uniform(0.06, 0.16)
            bh = h * rng.uniform(hmin, hmax) * (0.6 + 0.4 * math.sin(x / w * math.pi + 0.3))
            top = h * base - bh
            p.setBrush(col(c))
            p.drawRect(QRectF(x, top, bw - m * 0.006, h - top))
            if rng.random() < 0.3:
                p.drawRect(QRectF(x + bw * 0.4, top - m * 0.05, m * 0.006, m * 0.05))
            ws = m * 0.012
            yy = top + ws * 2
            while yy < h * base:
                xx = x + ws
                while xx < x + bw - ws * 2:
                    if rng.random() < lit:
                        on = rng.random()
                        a = 0.9 if (on + t * (1 if on > 0.9 else 0)) % 1 > 0.08 else 0.1
                        p.setBrush(col(rng.choice([P[2], "#ff4fd8", "#ffd36b"]), a * (0.5 + layer * 0.25)))
                        p.drawRect(QRectF(xx, yy, ws, ws * 0.9))
                    xx += ws * 2
                yy += ws * 2.2
            x += bw
        fog(p, w, h * base - h * 0.08, h * base + h * 0.04, P[1], 0.35)
    # rain
    pen = QPen(col("#bfefff", 0.35), max(1.0, m / 500))
    p.setPen(pen)
    rng = random.Random(77)
    for i in range(int(w * h / 1800)):
        x0, y0 = rng.uniform(0, w + h * 0.2), rng.uniform(0, h)
        L = m * rng.uniform(0.03, 0.06)
        y = (y0 + t * h) % (h + L) - L
        x = x0 - (y - y0) * 0.2 - t * h * 0.2
        x = (x % (w + h * 0.2))
        p.drawLine(QPointF(x, y), QPointF(x - L * 0.2, y + L))
    p.setPen(Qt.PenStyle.NoPen)


def sc_ocean(p, w, h, P, t, night=False):
    m = min(w, h)
    hz = h * 0.56
    vgrad(p, QRectF(0, 0, w, hz), [(0, P[0]), (1, P[1])])
    if night:
        stars(p, w, hz, int(w * h / 3000), 13, t, 0.9)
    sx, sy, sr = w * 0.5, hz - m * 0.08, m * 0.14
    glow(p, sx, sy, sr * 4, P[2], 0.55)
    p.setBrush(col(P[2]))
    p.drawEllipse(QPointF(sx, sy), sr, sr)
    # clouds
    rng = random.Random(15)
    for i in range(6):
        cxp = (rng.uniform(0, w) + t * w * 0.0) % w
        cyp = rng.uniform(h * 0.12, hz * 0.8)
        cw = m * rng.uniform(0.25, 0.5)
        p.setBrush(col(P[2] if not night else P[1], 0.22))
        p.drawRoundedRect(QRectF(cxp - cw / 2, cyp, cw, m * 0.03), m * 0.015, m * 0.015)
    if night is False:
        ridge(p, w, h, hz - m * 0.01, m * 0.025, 14, P[3], 1.5)
    vgrad(p, QRectF(0, hz, w, h - hz), [(0, P[4]), (1, P[5])])
    # reflection
    pen = QPen(col(P[2], 0.7), max(1.0, m / 260))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    for k in range(26):
        f = k / 26
        y = hz + (h - hz) * f ** 1.4 + m * 0.004
        width = sr * (1.2 + f * 1.5) * (0.6 + 0.4 * math.sin(TAU * (t + k * 0.21)))
        pen.setColor(col(P[2], 0.75 * (1 - f)))
        p.setPen(pen)
        p.drawLine(QPointF(sx - width, y), QPointF(sx + width, y))
    # waves
    for layer in range(5):
        f = (layer + 1) / 5
        y0 = hz + (h - hz) * (0.15 + 0.85 * f ** 1.3)
        amp = m * 0.012 * (1 + layer)
        path = QPainterPath(QPointF(0, h))
        for i in range(121):
            x = w * i / 120
            path.lineTo(x, y0 + math.sin(x / (m * (0.18 + 0.08 * layer)) * TAU + TAU * t * (1 if layer % 2 else -1)
                                         + layer) * amp)
        path.lineTo(w, h)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col(mix(P[4], P[5], f), 0.55 + 0.45 * f))
        p.drawPath(path)


def sc_harbor(p, w, h, P, t):
    sc_ocean(p, w, h, P, t, night=True)
    m = min(w, h)
    hz = h * 0.56
    # headland with a lighthouse
    path = QPainterPath(QPointF(w * 0.55, hz + m * 0.02))
    path.cubicTo(w * 0.7, hz - m * 0.12, w * 0.85, hz - m * 0.16, w * 1.05, hz - m * 0.14)
    path.lineTo(w * 1.05, hz + m * 0.05)
    path.closeSubpath()
    p.setBrush(col(P[5]))
    p.drawPath(path)
    lx, ly = w * 0.82, hz - m * 0.15
    tower = QPainterPath(QPointF(lx - m * 0.025, ly))
    tower.lineTo(lx - m * 0.015, ly - m * 0.17)
    tower.lineTo(lx + m * 0.015, ly - m * 0.17)
    tower.lineTo(lx + m * 0.025, ly)
    tower.closeSubpath()
    p.drawPath(tower)
    top = ly - m * 0.19
    ang = TAU * t
    for sgn in (1, -1):
        dx = math.cos(ang) * sgn
        L = w * 1.2
        beam = QPainterPath(QPointF(lx, top))
        beam.lineTo(lx + dx * L, top - m * 0.08 * abs(dx) - m * 0.02)
        beam.lineTo(lx + dx * L, top + m * 0.1 * abs(dx) + m * 0.02)
        beam.closeSubpath()
        g = QLinearGradient(lx, top, lx + dx * L, top)
        g.setColorAt(0, col(P[2], 0.55 * abs(dx)))
        g.setColorAt(1, col(P[2], 0))
        p.setBrush(g)
        p.drawPath(beam)
    glow(p, lx, top, m * 0.07, P[2], 0.9)


def sc_lava(p, w, h, P, t):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (0.6, P[1]), (1, P[1])])
    cx, top = w * 0.5, h * 0.42
    glow(p, cx, top, m * 0.7, P[2], 0.35 + 0.08 * math.sin(TAU * t))
    ridge(p, w, h, h * 0.62, m * 0.05, 41, P[3], 1.1, peaks=True)
    v = QPainterPath(QPointF(cx - m * 0.9, h))
    v.cubicTo(cx - m * 0.4, h * 0.7, cx - m * 0.14, top + m * 0.05, cx - m * 0.08, top)
    v.lineTo(cx + m * 0.08, top)
    v.cubicTo(cx + m * 0.14, top + m * 0.05, cx + m * 0.4, h * 0.7, cx + m * 0.9, h)
    v.closeSubpath()
    g = QLinearGradient(0, top, 0, h)
    g.setColorAt(0, col(P[4]))
    g.setColorAt(1, col(P[5]))
    p.setBrush(g)
    p.drawPath(v)
    # lava streams
    pen = QPen(col(P[2], 0.9), m * 0.008)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for sgn, off in ((-1, 0.0), (1, 0.4), (-1, 0.7)):
        path = QPainterPath(QPointF(cx + sgn * m * 0.03, top + m * 0.01))
        path.cubicTo(cx + sgn * m * (0.1 + off * 0.1), top + m * 0.2, cx + sgn * m * (0.05 + off * 0.2), top + m * 0.4,
                     cx + sgn * m * (0.2 + off * 0.3), top + m * 0.7)
        p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    glow(p, cx, top, m * 0.12, "#fff0b0", 0.9)
    rng = random.Random(42)
    for i in range(90):
        x0 = cx + rng.gauss(0, m * 0.18)
        sp = rng.uniform(0.6, 1.4)
        ph = (rng.random() + t * sp) % 1
        y = top - ph * h * 0.5 + m * 0.05
        x = x0 + math.sin(TAU * (ph * 2 + rng.random())) * m * 0.03 + (x0 - cx) * ph
        r = m * rng.uniform(0.003, 0.008)
        a = (1 - ph) * 0.9
        p.setBrush(col(P[2], a))
        p.drawEllipse(QPointF(x, y), r, r)
        glow(p, x, y, r * 5, P[2], a * 0.4)
    ridge(p, w, h, h * 0.92, m * 0.03, 43, P[5], 2.0, peaks=True)


def sc_planet(p, w, h, P, t, void=False):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (1, P[1])])
    rng = random.Random(51)
    if not void:
        for i in range(5):
            glow(p, rng.uniform(0, w), rng.uniform(0, h), m * rng.uniform(0.3, 0.6),
                 rng.choice([P[3], P[2], "#8a3aff"]), 0.18)
    stars(p, w, h, int(w * h / 1500), 52, t, 1.0)
    cx, cy, r = w * 0.5, h * (0.62 if not void else 0.5), m * (0.36 if not void else 0.3)
    ring_t = 0.22
    rr = QRectF(cx - r * 1.9, cy - r * ring_t * 1.9, r * 3.8, r * ring_t * 3.8)

    def ring(front):
        p.save()
        clip = QPainterPath()
        if front:
            clip.addRect(QRectF(cx - r * 3, cy, r * 6, r * 3))
        else:
            clip.addRect(QRectF(cx - r * 3, cy - r * 3, r * 6, r * 3))
        p.setClipPath(clip)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, (wdt, a) in enumerate(((0.05, 0.7), (0.025, 0.45), (0.012, 0.8))):
            sc = 1 + k * 0.09
            p.setPen(QPen(col(P[4] if not void else P[2], a), r * wdt))
            p.drawEllipse(QRectF(cx - rr.width() * sc / 2, cy - rr.height() * sc / 2, rr.width() * sc,
                                 rr.height() * sc))
        p.restore()

    p.save()
    p.translate(cx, cy)
    p.rotate(-14)
    p.translate(-cx, -cy)
    ring(False)
    glow(p, cx, cy, r * 1.6, P[3] if not void else P[2], 0.3)
    g = QRadialGradient(cx - r * 0.4, cy - r * 0.45, r * 1.6)
    if void:
        g.setColorAt(0, col("#2a0a0a"))
        g.setColorAt(0.6, col("#000000"))
    else:
        g.setColorAt(0, col(P[4]))
        g.setColorAt(0.35, col(P[2]))
        g.setColorAt(1, col(P[5]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.save()
    clip = QPainterPath()
    clip.addEllipse(QPointF(cx, cy), r, r)
    p.setClipPath(clip)
    for k in range(7):
        y = cy - r + r * 2 * (k + 0.5) / 7
        band = QPainterPath()
        band.addRect(QRectF(cx - r, y, r * 2, r * 0.06 + r * 0.04 * math.sin(k * 3)))
        p.setBrush(col("#ffffff" if not void else P[2], 0.06 if not void else 0.0))
        p.drawPath(band)
    if void:
        p.setPen(QPen(col(P[2], 0.9), r * 0.02))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r * 0.99, r * 0.99)
    p.restore()
    ring(True)
    p.restore()
    a = TAU * t
    mxp, myp = cx + math.cos(a) * r * 1.7, cy + math.sin(a) * r * 0.45 - r * 0.15
    mr = r * 0.09
    if math.sin(a) > -0.05 or True:
        g2 = QRadialGradient(mxp - mr * 0.4, myp - mr * 0.4, mr * 1.6)
        g2.setColorAt(0, col("#ffffff"))
        g2.setColorAt(1, col(P[3] if not void else P[2]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g2)
        p.drawEllipse(QPointF(mxp, myp), mr, mr)
    if void:
        pen = QPen(col(P[2], 0.5), max(1.0, m / 400))
        p.setPen(pen)
        for k in range(24):
            y = h * k / 24 + (t * h / 24)
            p.drawLine(QPointF(0, y), QPointF(w, y))
        p.setPen(Qt.PenStyle.NoPen)
        vgrad(p, QRectF(0, 0, w, h), [(0, col("#000000", 0.0)), (0.5, col("#000000", 0.0)), (1, col("#000000", 0.6))])


def sc_dunes(p, w, h, P, t, mesa=False):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (0.7, P[1]), (1, P[1])])
    if mesa:
        stars(p, w, h, int(w * h / 9000), 61, t, 0.3)
    sx, sy, sr = w * (0.3 if not mesa else 0.5), h * (0.32 if not mesa else 0.5), m * (0.16 if not mesa else 0.22)
    glow(p, sx, sy, sr * 3.5, P[2], 0.6)
    p.setBrush(col(P[2]))
    p.drawEllipse(QPointF(sx, sy), sr, sr)
    if mesa:
        rng = random.Random(62)
        for layer, (c, base, hgt) in enumerate(((P[3], 0.62, 0.16), (P[4], 0.74, 0.2), (P[5], 0.9, 0.24))):
            x = -m * 0.1
            path = QPainterPath(QPointF(x, h))
            y = h * base
            while x < w + m * 0.2:
                if rng.random() < 0.45:
                    top = h * base - m * hgt * rng.uniform(0.6, 1.1)
                    wd = m * rng.uniform(0.15, 0.35)
                    path.lineTo(x, y)
                    path.lineTo(x + m * 0.03, top)
                    path.lineTo(x + wd, top)
                    path.lineTo(x + wd + m * 0.03, y)
                    x += wd + m * 0.03
                else:
                    x += m * rng.uniform(0.05, 0.15)
                    path.lineTo(x, y + rng.uniform(-1, 1) * m * 0.01)
            path.lineTo(w + m * 0.2, h)
            path.closeSubpath()
            p.setBrush(col(c))
            p.drawPath(path)
            fog(p, w, h * base - m * 0.08, h * base + m * 0.04, P[1], 0.25)
        # a kestrel gliding
        bx = w * ((0.15 + t) % 1.0) * 1.2 - w * 0.1
        by = h * 0.3 + math.sin(TAU * t) * m * 0.02
        s = m * 0.05
        flap = math.sin(TAU * t * 3) * 0.3
        bird = QPainterPath(QPointF(bx - s, by - s * (0.2 + flap)))
        bird.quadTo(bx - s * 0.4, by - s * 0.35, bx, by)
        bird.quadTo(bx + s * 0.4, by - s * 0.35, bx + s, by - s * (0.2 + flap))
        bird.quadTo(bx + s * 0.4, by - s * 0.1, bx, by + s * 0.12)
        bird.quadTo(bx - s * 0.4, by - s * 0.1, bx - s, by - s * (0.2 + flap))
        p.setBrush(col(P[5]))
        p.drawPath(bird)
        return
    for layer in range(4):
        f = layer / 3
        base = h * (0.55 + 0.13 * layer)
        path = QPainterPath(QPointF(0, h))
        pts = []
        ph = phases(70 + layer, 3)
        for i in range(81):
            x = w * i / 80
            y = ridge_y(x, w, base, m * 0.06, ph, 0.7)
            pts.append((x, y))
            path.lineTo(x, y)
        path.lineTo(w, h)
        path.closeSubpath()
        g = QLinearGradient(0, base - m * 0.1, w, base + m * 0.2)
        g.setColorAt(0, mix(P[2], P[3 + min(layer, 2)], 0.3 + 0.2 * f))
        g.setColorAt(0.5, col(P[3 + min(layer, 2)]))
        g.setColorAt(1, mix(P[3 + min(layer, 2)], "#2a0a08", 0.4))
        p.setBrush(g)
        p.drawPath(path)
        # wind-blown sand off the crest
        rng = random.Random(80 + layer)
        for i in range(30):
            x0 = rng.uniform(0, w)
            ph2 = (rng.random() + t) % 1
            x = (x0 + ph2 * m * 0.3) % w
            y = ridge_y(x0, w, base, m * 0.06, ph, 0.7) - ph2 * m * 0.03
            p.setBrush(col(P[2], 0.5 * (1 - ph2)))
            p.drawEllipse(QPointF(x, y), m * 0.003, m * 0.003)


def sc_aurora(p, w, h, P, t):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (1, P[1])])
    stars(p, w, h, int(w * h / 2000), 91, t, 0.8)
    for band, (c, base, amp, thick, sp) in enumerate(((P[2], 0.3, 0.06, 0.22, 1), (P[3], 0.22, 0.05, 0.16, -1),
                                                      (P[2], 0.38, 0.04, 0.12, 2))):
        steps = 90
        for i in range(steps):
            x = w * i / steps
            xn = x / w
            y = h * base + math.sin(xn * TAU * 1.3 + TAU * t * sp + band) * m * amp \
                + math.sin(xn * TAU * 3.1 + band * 2 - TAU * t) * m * amp * 0.4
            th = m * thick * (0.6 + 0.4 * math.sin(xn * TAU * 2 + TAU * t + band))
            g = QLinearGradient(0, y - th, 0, y + th * 0.2)
            g.setColorAt(0, col(c, 0))
            g.setColorAt(0.8, col(c, 0.35))
            g.setColorAt(1, col(c, 0))
            p.fillRect(QRectF(x, y - th, w / steps + 1, th * 1.2), g)
    mg = QLinearGradient(0, h * 0.5, 0, h * 0.8)
    mg.setColorAt(0, col("#dfeaf3"))
    mg.setColorAt(0.35, col("#8fa9bd"))
    mg.setColorAt(1, col(P[4]))
    ridge(p, w, h, h * 0.68, m * 0.06, 92, QBrush(mg), 0.8)
    fog(p, w, h * 0.66, h * 0.82, P[1], 0.4)
    ridge(p, w, h, h * 0.84, m * 0.03, 93, P[5], 1.5)
    # the station
    sx, sy = w * 0.62, ridge_y(w * 0.62, w, h * 0.84, m * 0.03, phases(93), 1.5)
    p.setBrush(col(P[5]))
    p.drawRect(QRectF(sx - m * 0.06, sy - m * 0.04, m * 0.12, m * 0.05))
    p.drawEllipse(QRectF(sx - m * 0.04, sy - m * 0.075, m * 0.08, m * 0.07))
    p.drawRect(QRectF(sx + m * 0.04, sy - m * 0.14, m * 0.005, m * 0.1))
    blink = 1.0 if (t * 4) % 1 < 0.5 else 0.25
    glow(p, sx + m * 0.0425, sy - m * 0.14, m * 0.02, "#ff4a4a", blink)
    for k in range(4):
        glow(p, sx - m * 0.045 + k * m * 0.03, sy - m * 0.02, m * 0.012, "#ffd36b", 0.9)


def sc_lowpoly(p, w, h, P, t):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (1, P[1])])
    rng = random.Random(101)
    cell = m * 0.12
    cols, rows = int(w / cell) + 2, int(h / cell) + 2
    pts = [[QPointF(i * cell + rng.uniform(-0.35, 0.35) * cell, j * cell + rng.uniform(-0.35, 0.35) * cell)
            for i in range(cols)] for j in range(rows)]
    p.setPen(Qt.PenStyle.NoPen)
    for j in range(rows - 1):
        for i in range(cols - 1):
            a, b, c, d = pts[j][i], pts[j][i + 1], pts[j + 1][i], pts[j + 1][i + 1]
            for tri in ((a, b, c), (b, d, c)):
                cy = sum(q.y() for q in tri) / 3 / h
                cx = sum(q.x() for q in tri) / 3 / w
                base = mix(P[0], P[1], max(0, min(1, cy)))
                light = 0.12 * math.sin(cx * 7 + cy * 5) + rng.uniform(-0.06, 0.06)
                cc = mix(base, "#ffffff" if light > 0 else "#000000", abs(light))
                path = QPainterPath(tri[0])
                path.lineTo(tri[1])
                path.lineTo(tri[2])
                path.closeSubpath()
                p.setBrush(cc)
                p.drawPath(path)
    # orchard of geometric iron trees
    ground = h * 0.78
    ridge(p, w, h, ground, m * 0.02, 102, P[4], 1.5)
    n = max(3, int(w / (m * 0.22)))
    for k in range(n):
        x = w * (k + 0.5) / n
        s = m * (0.16 if k % 2 else 0.2)
        y = ground + m * 0.01
        p.setBrush(col(P[5]))
        p.drawRect(QRectF(x - s * 0.05, y - s * 0.8, s * 0.1, s * 0.8))
        canopy = QPainterPath(QPointF(x, y - s * 1.8))
        for q in range(6):
            ang = -math.pi / 2 + TAU * (q + 1) / 6
            canopy.lineTo(x + math.cos(ang) * s * 0.55, y - s * 1.25 + math.sin(ang) * s * 0.55)
        canopy.closeSubpath()
        sway = math.sin(TAU * t + k) * 0.5
        p.save()
        p.translate(x, y - s * 0.8)
        p.rotate(sway * 3)
        p.translate(-x, -(y - s * 0.8))
        p.setBrush(col(P[2] if k % 2 else mix(P[2], P[4], 0.4)))
        p.drawPath(canopy)
        p.setBrush(col("#000000", 0.2))
        half = QPainterPath(QPointF(x, y - s * 1.8))
        half.lineTo(x + s * 0.48, y - s * 1.52)
        half.lineTo(x + s * 0.48, y - s * 0.98)
        half.lineTo(x, y - s * 0.7)
        half.closeSubpath()
        p.drawPath(half)
        rng2 = random.Random(k)
        for f in range(3):
            fx = x + rng2.uniform(-0.3, 0.3) * s
            fy = y - s * 1.25 + rng2.uniform(-0.3, 0.3) * s
            p.setBrush(col("#ff5a3a"))
            p.drawEllipse(QPointF(fx, fy), s * 0.05, s * 0.05)
        p.restore()
    ridge(p, w, h, h * 0.93, m * 0.015, 103, P[5], 2.0)


def sc_bloom(p, w, h, P, t):
    m = min(w, h)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, col(P[0]))
    g.setColorAt(1, col(P[1]))
    p.fillRect(QRectF(0, 0, w, h), g)
    cx, cy = w * 0.5, h * 0.48
    p.setPen(Qt.PenStyle.NoPen)
    for ring_i, (n, rad, size, c, sp) in enumerate(((12, 0.34, 0.2, P[3], 1), (10, 0.22, 0.17, P[2], -1),
                                                    (8, 0.11, 0.12, P[4], 1))):
        for k in range(n):
            ang = TAU * k / n + TAU * t / n * sp
            px, py = cx + math.cos(ang) * m * rad, cy + math.sin(ang) * m * rad
            p.save()
            p.translate(px, py)
            p.rotate(math.degrees(ang) + 90)
            petal = QPainterPath(QPointF(0, -m * size * 0.6))
            petal.cubicTo(m * size * 0.35, -m * size * 0.3, m * size * 0.35, m * size * 0.3, 0, m * size * 0.6)
            petal.cubicTo(-m * size * 0.35, m * size * 0.3, -m * size * 0.35, -m * size * 0.3, 0, -m * size * 0.6)
            p.setBrush(col(c, 0.55))
            p.drawPath(petal)
            p.restore()
    p.setBrush(col(P[5]))
    p.drawEllipse(QPointF(cx, cy), m * 0.06, m * 0.06)
    # circuit traces
    pen = QPen(col(P[5], 0.5), max(1.0, m / 260))
    p.setPen(pen)
    rng = random.Random(111)
    for i in range(16):
        ang = TAU * i / 16
        x, y = cx + math.cos(ang) * m * 0.46, cy + math.sin(ang) * m * 0.46
        path = QPainterPath(QPointF(x, y))
        for s in range(3):
            if rng.random() < 0.5:
                x += math.copysign(m * rng.uniform(0.05, 0.2), math.cos(ang))
            else:
                y += math.copysign(m * rng.uniform(0.05, 0.2), math.sin(ang))
            path.lineTo(x, y)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setBrush(col(P[5], 0.7))
        p.drawEllipse(QPointF(x, y), m * 0.008, m * 0.008)
        pulse = (t + i / 16) % 1
        pp = path.pointAtPercent(pulse)
        p.setPen(Qt.PenStyle.NoPen)
        glow(p, pp.x(), pp.y(), m * 0.02, P[2], 0.9)
        p.setPen(pen)
    p.setPen(Qt.PenStyle.NoPen)


def sc_paper(p, w, h, P, t):
    m = min(w, h)
    vgrad(p, QRectF(0, 0, w, h), [(0, P[0]), (1, P[1])])
    glow(p, w * 0.8, h * 0.36, m * 0.3, "#ffffff", 0.7)
    p.setBrush(col(P[2]))
    p.drawEllipse(QPointF(w * 0.8, h * 0.36), m * 0.07, m * 0.07)
    # clouds drifting
    for k in range(3):
        x = ((k * 0.37 + t) % 1.2 - 0.1) * w
        y = h * (0.26 + 0.06 * k)
        p.setBrush(col("#ffffff", 0.9))
        for d in range(3):
            p.drawEllipse(QPointF(x + d * m * 0.05, y - (m * 0.02 if d == 1 else 0)), m * 0.05, m * 0.035)
    layers = [(0.5, "#b6d8c6"), (0.6, P[3]), (0.72, P[4]), (0.86, P[5])]
    for i, (base, c) in enumerate(layers):
        ph = phases(120 + i, 3)
        path = QPainterPath(QPointF(0, h))
        for s in range(101):
            x = w * s / 100
            path.lineTo(x, ridge_y(x, w, h * base, m * 0.05, ph, 0.8))
        path.lineTo(w, h)
        path.closeSubpath()
        p.save()
        p.translate(0, -m * 0.008)
        p.setBrush(col("#000000", 0.18))
        p.drawPath(path)
        p.restore()
        p.setBrush(col(c))
        p.drawPath(path)
        if i == 1:
            # a little castle on the second hill
            cxp = w * 0.3
            cyp = ridge_y(cxp, w, h * base, m * 0.05, ph, 0.8)
            s = m * 0.08
            p.setBrush(col("#f4eadb"))
            p.drawRect(QRectF(cxp - s, cyp - s * 1.2, s * 2, s * 1.3))
            for tx in (-s * 1.1, s * 0.6):
                p.drawRect(QRectF(cxp + tx, cyp - s * 2, s * 0.5, s * 2.1))
                roof = QPainterPath(QPointF(cxp + tx - s * 0.1, cyp - s * 2))
                roof.lineTo(cxp + tx + s * 0.25, cyp - s * 2.6)
                roof.lineTo(cxp + tx + s * 0.6, cyp - s * 2)
                roof.closeSubpath()
                p.setBrush(col(P[2]))
                p.drawPath(roof)
                p.setBrush(col("#f4eadb"))
            flag_y = cyp - s * 2.6
            p.setBrush(col(P[2]))
            wave = math.sin(TAU * t * 2) * s * 0.06
            flag = QPainterPath(QPointF(cxp - s * 0.85, flag_y - s * 0.5))
            flag.lineTo(cxp - s * 0.45, flag_y - s * 0.42 + wave)
            flag.lineTo(cxp - s * 0.85, flag_y - s * 0.32)
            p.drawPath(flag)
            p.setBrush(col("#3a2a2a"))
            p.drawRect(QRectF(cxp - s * 0.15, cyp - s * 0.5, s * 0.3, s * 0.6))


SCENES = {"synth": sc_synth, "forest": sc_forest, "city": sc_city, "ocean": sc_ocean, "lava": sc_lava,
          "planet": sc_planet, "dunes": sc_dunes, "aurora": sc_aurora, "lowpoly": sc_lowpoly, "bloom": sc_bloom,
          "paper": sc_paper, "harbor": sc_harbor,
          "mesa": lambda p, w, h, P, t: sc_dunes(p, w, h, P, t, mesa=True),
          "lantern": lambda p, w, h, P, t: sc_forest(p, w, h, P, t, lantern=True),
          "void": lambda p, w, h, P, t: sc_planet(p, w, h, P, t, void=True)}


def palette(name, variant):
    P = GAMES[name][1]
    if variant == 0:
        return P
    deg = [0, 38, -42, 160, 75, -100, 200, 120, -150, 260, 20, -20][variant % 12]
    return [shift(c, deg).name() for c in P]


def painter(img):
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(Qt.PenStyle.NoPen)
    return p


def scene(name, w, h, t=0.0, variant=0):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.black)
    p = painter(img)
    SCENES[GAMES[name][0]](p, w, h, palette(name, variant), t)
    p.end()
    return img


def title_font(name, px, variant=0):
    fam, weight, spacing, upper = GAMES[name][2]
    if variant % 4 == 3:
        fam, weight = "Inter Display", 900
    f = QFont(fam)
    f.setPixelSize(int(px))
    f.setWeight(QFont.Weight(weight))
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing * 100)
    return f, (name.upper() if upper else name)


def draw_title(p, name, rect, px, variant=0, align=Qt.AlignmentFlag.AlignCenter, color=None, shadow=True):
    f, text = title_font(name, px, variant)
    # shrink to fit the rect, on up to two lines
    while True:
        p.setFont(f)
        br = p.boundingRect(rect, int(align | Qt.TextFlag.TextWordWrap), text)
        if (br.width() <= rect.width() and br.height() <= rect.height()) or f.pixelSize() < 10:
            break
        f.setPixelSize(int(f.pixelSize() * 0.92))
    c = col(color or GAMES[name][3])
    if variant and not color:
        c = shift(GAMES[name][3], [0, 38, -42, 160, 75, -100, 200, 120, -150, 260, 20, -20][variant % 12])
    if shadow:
        lum = c.lightnessF()
        sc = col("#000000", 0.55) if lum > 0.45 else col("#ffffff", 0.35)
        for dx, dy, a in ((0, 2, 1.0), (0, 4, 0.5), (0, 7, 0.25)):
            s = QColor(sc)
            s.setAlphaF(sc.alphaF() * a)
            p.setPen(s)
            p.drawText(rect.translated(dx * px / 40, dy * px / 40), int(align | Qt.TextFlag.TextWordWrap), text)
    p.setPen(c)
    p.drawText(rect, int(align | Qt.TextFlag.TextWordWrap), text)


def cover(name, w=600, h=900, t=0.0, variant=0):
    img = scene(name, w, h, t, variant)
    p = painter(img)
    top = variant % 2 == 0
    r = QRectF(w * 0.08, h * (0.06 if top else 0.72), w * 0.84, h * 0.2)
    light = col(GAMES[name][3]).lightnessF() > 0.45
    sc = "#000000" if light else "#ffffff"
    g = QLinearGradient(0, 0 if top else h, 0, h * 0.34 if top else h * 0.62)
    g.setColorAt(0, col(sc, 0.5 if light else 0.35))
    g.setColorAt(1, col(sc, 0))
    p.fillRect(QRectF(0, 0, w, h), g)
    draw_title(p, name, r, w * 0.13, variant, Qt.AlignmentFlag.AlignHCenter |
               (Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag.AlignBottom))
    p.end()
    return img


def wide(name, w=920, h=430, t=0.0, variant=0):
    img = scene(name, w, h, t, variant)
    p = painter(img)
    r = QRectF(w * 0.06, h * 0.1, w * 0.5, h * 0.8)
    light = col(GAMES[name][3]).lightnessF() > 0.45
    sc = "#000000" if light else "#ffffff"
    g = QLinearGradient(0, 0, w * 0.62, 0)
    g.setColorAt(0, col(sc, 0.6 if light else 0.45))
    g.setColorAt(1, col(sc, 0))
    p.fillRect(QRectF(0, 0, w, h), g)
    draw_title(p, name, r, h * 0.18, variant, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    p.end()
    return img


def hero(name, w=1920, h=620, t=0.0, variant=0):
    return scene(name, w, h, t, variant)


def logo(name, w=640, h=360, variant=0):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = painter(img)
    draw_title(p, name, QRectF(w * 0.04, h * 0.08, w * 0.92, h * 0.84), h * 0.3, variant)
    p.end()
    return img


def icon(name, s=256, variant=0):
    big = scene(name, s * 2, s * 2, 0.0, variant)
    img = QImage(s, s, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = painter(img)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, s, s), s * 0.2, s * 0.2)
    p.setClipPath(clip)
    p.drawImage(QRectF(0, 0, s, s), big, QRectF(s * 0.25, s * 0.5, s * 1.5, s * 1.5))
    f, text = title_font(name, s * 0.6, variant)
    p.setFont(f)
    p.setPen(col("#000000", 0.45))
    p.drawText(QRectF(0, s * 0.03, s, s), int(Qt.AlignmentFlag.AlignCenter), name[0])
    p.setPen(col(GAMES[name][3]))
    p.drawText(QRectF(0, 0, s, s), int(Qt.AlignmentFlag.AlignCenter), name[0])
    p.end()
    return img


MAKERS = {"grid": cover, "wide": wide, "hero": hero, "logo": logo, "icon": icon}
SIZES = {"grid": (600, 900), "wide": (920, 430), "hero": (1920, 620), "logo": (640, 360), "icon": (256, 256)}


def make(name, slot, variant=0, t=0.0, scale=1.0):
    w, h = SIZES[slot]
    w, h = round(w * scale), round(h * scale)
    if slot in ("logo",):
        return logo(name, w, h, variant)
    if slot == "icon":
        return icon(name, w, variant)
    return MAKERS[slot](name, w, h, t, variant)


def frames(name, slot, variant=0, n=24, scale=1.0):
    return [make(name, slot, variant, k / n, scale) for k in range(n)]
