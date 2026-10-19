"""Run the app offscreen (no window ever reaches a screen) against a fake Steam install and a fake
SteamGridDB serving the made-up games in artgen.py, and capture stills and animations at 2x.

    python3 harness.py stills        # the README screenshots, into ./out
    python3 harness.py anim_get      # Get Artwork then Apply (also anim_pick, anim_wall)

Needs PySide6 and Pillow. Copy what you want from ./out into docs/.
"""
import importlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(os.environ.get("APP_SRC", Path(__file__).resolve().parents[2]))
W, HT, DPR = int(os.environ.get("SHOT_W", 1280)), int(os.environ.get("SHOT_H", 800)), float(os.environ.get("DPR", 2))
OUT = Path(os.environ.get("OUT", HERE / "out"))
OUT.mkdir(exist_ok=True)
HOME = HERE / f"fakehome-{sys.argv[1]}"
if HOME.exists():
    shutil.rmtree(HOME)
HOME.mkdir()
os.environ["HOME"] = str(HOME)
os.environ["XDG_CONFIG_HOME"] = str(HOME / ".config")
os.environ["XDG_CACHE_HOME"] = str(HOME / ".cache")
(HOME / "screen.json").write_text(json.dumps({"screens": [{"name": "s", "x": 0, "y": 0, "width": int(W * DPR),
                                                            "height": int(HT * DPR), "logicalDpi": 96,
                                                            "logicalBaseDpi": 96, "dpr": 1}]}))
os.environ["QT_SCALE_FACTOR"] = str(DPR)
os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={HOME / 'screen.json'}"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "tests"))
sys.path.insert(0, str(HERE))
from conftest import make_entry, make_shortcuts  # noqa: E402

from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from PIL import Image  # noqa: E402

import artgen  # noqa: E402
from steam_artwork_finder import APP_NAME  # noqa: E402

app.setApplicationName(APP_NAME)
app.setStyle("Fusion")
app.setFont(QFont(os.environ.get("FONT", "Inter"), 10))

# appid, name in Steam, folder, the SteamGridDB game it is
GAMES = [
    (-1734531011, "Lumen Drift", "Lumen Drift", "Lumen Drift"),
    (-2011916284, "Hollow Pines", "Hollow Pines", "Hollow Pines"),
    (-1000000000, "Neon Requiem", "Neon Requiem", "Neon Requiem"),
    (-1500000000, "Saltwind (GOG)", "Saltwind", "Saltwind"),
    (-1400000000, "Emberfall", "Emberfall", "Emberfall"),
    (-1300000000, "Starward Bound", "Starward Bound", "Starward Bound"),
    (-1200000000, "Launcher", "Glass Tide", "Glass Tide"),  # generic title: renamed
    (-1100000000, "Duneheart", "Duneheart", "Duneheart"),
    (-1050000000, "Kestrel Run", "Kestrel Run", "Kestrel Run"),
    (-1040000000, "Aurora Station", "Aurora Station", "Aurora Station"),
    (-1030000000, "Moth & Lantern", "Moth and Lantern", "Moth & Lantern"),
    (-1020000000, "iron_orchard.exe", "Iron Orchard", "Iron Orchard"),  # file name: renamed
    (-1010000000, "Petal Circuit", "Petal Circuit", "Petal Circuit"),
    (-1005000000, "Quiet Harbor", "Quiet Harbor", "Quiet Harbor"),
    (-1004000000, "Voidline", "Voidline", "Voidline"),
    (-1003000000, "Paper Kingdoms", "Paper Kingdoms", "Paper Kingdoms"),
]
EXTRA = [(-1002000000, "Homebrew Test Build", "homebrew", None)]  # not on SteamGridDB
ANIMATED_COVERS = {"Lumen Drift", "Neon Requiem", "Aurora Station", "Quiet Harbor", "Moth & Lantern"}

root = HOME / ".local/share/Steam"
cfg = root / "userdata/12345678/config"
grid = cfg / "grid"
SLOT_FILES = {"grid": "p", "wide": "", "hero": "_hero", "logo": "_logo", "icon": "_icon"}


def qimage_png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def to_pil(img: QImage) -> Image.Image:
    return Image.open(io.BytesIO(qimage_png(img))).convert("RGBA")


def anim_webp(frames: list[QImage], ms: int = 70, quality: int = 88) -> bytes:
    ims = [to_pil(f) for f in frames]
    buf = io.BytesIO()
    ims[0].save(buf, "WEBP", save_all=True, append_images=ims[1:], duration=ms, loop=0, quality=quality, method=4)
    return buf.getvalue()


_anim_cache: dict = {}


def animated_bytes(name, slot, variant=0, n=24, scale=1.0):
    key = (name, slot, variant, n, scale)
    if key not in _anim_cache:
        _anim_cache[key] = anim_webp(artgen.frames(name, slot, variant, n, scale))
    return _anim_cache[key]


_dl_cache: dict = {}


def setup_steam(have: str = "full"):
    """have: "full" (every game has its artwork), "none" (nothing yet), "some"."""
    cfg.mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/loginusers.vdf").write_text(
        '"users"\n{\n\t"76561197972611406"\n\t{\n\t\t"PersonaName"\t\t"player one"\n\t\t"MostRecent"\t\t"1"\n'
        '\t}\n}\n')
    entries = [make_entry(a, n, f'"/home/deck/Games/{f}/{f.replace(" ", "")}.x86_64"') for a, n, f, _ in GAMES + EXTRA]
    (cfg / "shortcuts.vdf").write_bytes(make_shortcuts(entries))
    grid.mkdir()
    from steam_artwork_finder.shortcuts import ShortcutsFile

    sf = ShortcutsFile(cfg / "shortcuts.vdf")
    for i, sc in enumerate(sf.shortcuts):
        g = (GAMES + EXTRA)[i][3]
        if g is None or have == "none":
            continue
        if have == "some" and i % 3 == 1:
            continue
        for slot, suffix in SLOT_FILES.items():
            if have == "some" and i % 3 == 2 and slot != "grid":
                continue
            path = grid / f"{sc.appid}{suffix}.png"
            if slot == "grid" and g in ANIMATED_COVERS:
                path.write_bytes(animated_bytes(g, "grid", 0, 24))
            else:
                artgen.make(g, slot).save(str(path))
            if slot == "icon":
                sc.set_icon(str(path))
    sf.save()
    matches = {}
    if have != "none":
        for i, sc in enumerate(sf.shortcuts):
            g = (GAMES + EXTRA)[i][3]
            if g:
                matches[str(sc.appid)] = {"id": 1000 + i, "name": g, "year": "2025"}
    conf_dir = HOME / ".config/steam-artwork-finder"
    conf_dir.mkdir(parents=True)
    (conf_dir / "config.json").write_text(json.dumps({
        "api_key": "fake", "matches": matches, "auto_restart_steam": True,
        "applied": {}}))
    return sf


# ---- fake SteamGridDB -----------------------------------------------------------------------------
import steam_artwork_finder.sgdb as sgdb  # noqa: E402
from steam_artwork_finder.sgdb import Game, clean_name  # noqa: E402

ART_NAMES = {n.casefold(): n for n in artgen.GAMES}
ART_NAMES.update({"moth and lantern": "Moth & Lantern"})
GAME_ID = {n: 1000 + i for i, n in enumerate(artgen.GAMES)}
ID_GAME = {v: k for k, v in GAME_ID.items()}
LATENCY = {"search": 0.12, "list": 0.15, "download": 0.08}
AUTHORS = ["Nyx", "Fiddlehead", "Marrow", "Pixelwitch", "Orion", "Tamsin", "Quill", "Hexen"]
STYLES = ["official", "alternate", "material", "blurred", "white_logo", "no_logo"]


ANIM_SCALE = {"grid": 0.6, "wide": 0.6, "hero": 0.5, "logo": 0.6, "icon": 1.0}
THUMB_SCALE = {"grid": 0.5, "wide": 0.5, "hero": 0.4, "logo": 0.6, "icon": 0.5}


class FakeSGDB(sgdb.SteamGridDB):
    def check_key(self):
        time.sleep(0.05)
        return True

    def search(self, term):
        time.sleep(LATENCY["search"])
        t = clean_name(term).casefold()
        name = ART_NAMES.get(t)
        if name is None:
            for k, v in ART_NAMES.items():
                if k in t or t in k:
                    name = v
                    break
        if name is None:
            return []
        return [Game(GAME_ID[name], name, "2025", True), Game(GAME_ID[name] + 500, name + ": Prologue", "2024")]

    def _get(self, path, params=None):
        time.sleep(LATENCY["list"])
        gid = int(path.rsplit("/", 1)[-1])
        name = ID_GAME.get(gid if gid < 1500 else gid - 500)
        if name is None:
            return []
        endpoint = path.split("/")[1]
        slot = {"grids": "grid", "heroes": "hero", "logos": "logo", "icons": "icon"}[endpoint]
        if slot == "grid" and (params or {}).get("dimensions", "").startswith("460"):
            slot = "wide"
        page = int((params or {}).get("page", 0))
        animated = "animated" in str((params or {}).get("types", "")) and slot != "icon"
        out = []
        n = 14 if page == 0 else 6
        for k in range(n):
            v = k + page * 14
            anim = animated and k in (1, 4) and slot in ("grid", "wide", "hero")
            out.append({"id": gid * 1000 + v + (5000000 if slot == "wide" else 0) + hash(slot) % 97 * 100000,
                        "url": f"fake://{slot}/{name}/{v}/{'a' if anim else 's'}",
                        "thumb": f"fake://{slot}/{name}/{v}/{'a' if anim else 's'}/t" + (".webm" if anim else ""),
                        "width": artgen.SIZES[slot][0], "height": artgen.SIZES[slot][1],
                        "mime": "image/webp" if anim else "image/png", "score": 20 - v,
                        "style": STYLES[v % len(STYLES)], "author": {"name": AUTHORS[(v + len(name)) % len(AUTHORS)]}})
        return out

    def download(self, url):
        time.sleep(LATENCY["download"])
        parts = url.replace("fake://", "").split("/")
        slot, name, v, kind = parts[0], parts[1], int(parts[2]), parts[3]
        if kind == "a":
            return animated_bytes(name, slot, v, 24, ANIM_SCALE[slot])
        thumb = parts[-1] == "t"
        key = (slot, name, v, thumb)
        if key not in _dl_cache:
            _dl_cache[key] = qimage_png(artgen.make(name, slot, v, 0.0, THUMB_SCALE[slot] if thumb else 1.0))
        return _dl_cache[key]


sgdb.SteamGridDB = FakeSGDB
import steam_artwork_finder.gui.app as appmod  # noqa: E402
from steam_artwork_finder.gui import motion, smooth, theme  # noqa: E402

motion.video_supported = lambda: False
appmod.SteamGridDB = FakeSGDB
STEAM = {"running": True}
appmod.steam_running = lambda root=None: STEAM["running"]


def _shutdown(root=None):
    time.sleep(0.6)
    STEAM["running"] = False
    return True


def _start(root=None):
    STEAM["running"] = True
    return True


appmod.shutdown_steam = _shutdown
appmod.start_steam = _start
appmod.wait_until_closed = lambda root=None, **k: (time.sleep(0.4), True)[1]

app.setPalette(theme.palette())
app.setStyleSheet(theme.stylesheet())
smooth.install(app)


class H:
    """What a shot script gets: the window, and ways to wait, look, point and record."""

    def __init__(self, have):
        self.sf = setup_steam(have)
        self.win = appmod.MainWindow()
        self.win.move(0, 0)
        self.win.resize(W, HT)
        self.win.show()
        self.cursor = None  # QPointF in window coordinates, drawn on captures
        self.pressed = False
        self.frames: list[tuple[QImage, float]] = []
        self.recording = False
        self._last = 0.0

    # waiting
    def pump(self, ms=400, until=None, every=None):
        t = time.time()
        while time.time() - t < ms / 1000:
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            if self.recording:
                if QApplication.focusWidget() is not None:
                    self.unfocus()
                self._maybe_frame()
            else:
                time.sleep(0.003)
            if until is not None and until():
                break

    def settle(self):
        self.pump(3000, until=lambda: not self.win._matching)
        self.pump(600)

    # looking
    def popups(self):
        return [w for w in QApplication.topLevelWidgets()
                if w is not self.win and w.isVisible() and w.windowType() in (Qt.WindowType.Popup,
                                                                             Qt.WindowType.ToolTip)
                and w.width() > 30]

    def capture(self) -> QImage:
        pm = self.win.grab()
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin = self.win.mapToGlobal(QPoint(0, 0))
        for w in self.popups():
            p.drawPixmap(w.mapToGlobal(QPoint(0, 0)) - origin, w.grab())
        if self.cursor is not None:
            draw_cursor(p, self.cursor, self.pressed)
        p.end()
        return pm.toImage()

    def unfocus(self):
        f = QApplication.focusWidget()
        if f is not None:
            f.clearFocus()

    def shot(self, name, rect=None):
        self.unfocus()
        self.pump(350)
        img = self.capture()
        if rect is not None:
            img = img.copy(int(rect[0] * DPR), int(rect[1] * DPR), int(rect[2] * DPR), int(rect[3] * DPR))
        img.save(str(OUT / f"{name}.png"))
        print("saved", name, img.width(), img.height(), flush=True)

    # pointing
    def at(self, widget, where=None) -> QPointF:
        r = widget.rect()
        pt = where if where is not None else r.center()
        return QPointF(widget.mapTo(self.win, pt))

    def move_to(self, target: QPointF, ms=450):
        start = self.cursor or QPointF(W * 0.6, HT * 0.7)
        t0 = time.time()
        while True:
            f = min(1.0, (time.time() - t0) / (ms / 1000))
            e = f * f * (3 - 2 * f)
            self.cursor = start + (target - start) * e
            self.pump(8)
            if f >= 1:
                break

    def click(self, widget, where=None, ms=450):
        from PySide6.QtTest import QTest

        self.move_to(self.at(widget, where), ms)
        self.pressed = True
        self.pump(90)
        pt = where if where is not None else widget.rect().center()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
        self.pressed = False
        self.unfocus()
        self.pump(60)

    # recording
    def button(self, prefix, parent=None):
        from PySide6.QtWidgets import QPushButton

        for b in (parent or self.win).findChildren(QPushButton):
            if b.isVisible() and b.text().startswith(prefix):
                return b
        raise LookupError(prefix)

    def speed(self, f):
        """Play what's recorded from now on f times faster (waiting on the fake network)."""
        self._speed = f

    def record(self, fps=25, scale=0.625):
        self._speed = 1.0
        self.rec_scale = scale
        self.frames = []
        self.recording = True
        self._gap = 1 / fps
        self._last = 0.0

    def _maybe_frame(self):
        now = time.time()
        if now - self._last >= self._gap:
            self._last = now
            img = self.capture()
            if self.rec_scale != 1.0:
                img = img.scaledToWidth(round(img.width() * self.rec_scale), Qt.TransformationMode.SmoothTransformation)
            self.frames.append((img.convertToFormat(QImage.Format.Format_RGB888), now, self._speed))

    def save_anim(self, name, crop=None, scale=1.0, quality=90, hold_last=1500):
        self.recording = False
        frames = self.frames
        ims, durs = [], []
        for i, (img, t, sp) in enumerate(frames):
            if crop is not None:
                k = DPR * self.rec_scale
                img = img.copy(int(crop[0] * k), int(crop[1] * k), int(crop[2] * k), int(crop[3] * k))
            pil = to_pil(img).convert("RGB")
            if scale != 1.0:
                pil = pil.resize((round(pil.width * scale), round(pil.height * scale)), Image.LANCZOS)
            nxt = frames[i + 1][1] if i + 1 < len(frames) else t + hold_last / 1000
            d = max(20, round((nxt - t) * 1000 / sp))
            if ims and pil.tobytes() == ims[-1].tobytes():
                durs[-1] += d
                continue
            ims.append(pil)
            durs.append(d)
        path = OUT / f"{name}.webp"
        ims[0].save(path, "WEBP", save_all=True, append_images=ims[1:], duration=durs, loop=0, quality=quality,
                    method=6, minimize_size=True)
        print("saved", name, len(ims), "frames", sum(durs), "ms", path.stat().st_size // 1024, "KB", flush=True)


def draw_cursor(p: QPainter, pt: QPointF, pressed=False):
    s = 1.15
    path = QPainterPath(QPointF(0, 0))
    for x, y in ((0, 17), (4.2, 13.2), (7, 19.5), (9.6, 18.4), (6.9, 12.2), (12.4, 12.2)):
        path.lineTo(x * s, y * s)
    path.closeSubpath()
    p.save()
    p.translate(pt)
    if pressed:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QPointF(0, 0), 14, 14)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 60))
    p.drawPath(path.translated(1, 1.5))
    p.setPen(QPen(QColor("white"), 1.4))
    p.setBrush(QColor("#111111"))
    p.drawPath(path)
    p.restore()


if __name__ == "__main__":
    mod = importlib.import_module(sys.argv[1])
    h = H(getattr(mod, "HAVE", "full"))
    mod.run(h)
    print("done", flush=True)
    os._exit(0)
