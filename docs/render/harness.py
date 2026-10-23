"""Run the app offscreen (no window ever reaches a screen) and capture stills and animations at 2x.

Made-up games: a fake Steam install and a fake SteamGridDB serving the games in artgen.py.

    python3 harness.py stills        # into ./out
    python3 harness.py anim_get      # Get Artwork then Apply (also anim_pick, anim_wall)

Your own library (REAL=1): a copy of your Steam game list, artwork and the app's settings in a
throwaway home folder, with the real SteamGridDB and Steam store. Steam itself is never touched:
Apply writes into the copy, and restarting Steam is faked. The Steam login name is replaced in the
copy and blurred on every picture.

    REAL=1 python3 harness.py real_stills    # also real_pick, real_get, real_page
    LIBRARY='{"drop": ["Maelstrom"], "add": ["Alan Wake 2"]}'  # tidy the copied game list

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
REAL = os.environ.get("REAL") == "1"
REAL_HOME = Path(os.environ.get("REAL_HOME", Path.home()))  # where the real Steam and settings are
HOME = Path(os.environ.get("WORK", HERE)) / f"fakehome-{sys.argv[1]}"
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

from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from PIL import Image  # noqa: E402

import artgen  # noqa: E402
from steam_artwork_finder import APP_NAME  # noqa: E402

app.setApplicationName(APP_NAME)
app.setStyle("Fusion")
if os.environ.get("FONT") or not REAL:  # your own library: the desktop's font, as the app shows it
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
    if REAL:
        return setup_real(have)
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


def real_steam_root() -> Path:
    if os.environ.get("REAL_STEAM"):
        return Path(os.environ["REAL_STEAM"])
    for rel in (".local/share/Steam", ".steam/steam", ".steam/root", ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                "snap/steam/common/.local/share/Steam"):
        if (REAL_HOME / rel / "userdata").is_dir():
            return (REAL_HOME / rel).resolve()
    raise SystemExit("No Steam folder found; set REAL_STEAM")


PERSONA = "Player"


def edit_library(path: Path, edit: dict):
    """Change the copied game list only, for tidier pictures: LIBRARY='{"drop": ["Maelstrom"],
    "add": ["Alan Wake 2"]}' drops every game whose name holds a "drop" text and adds made-up
    shortcuts with the "add" names. Your real shortcuts.vdf is never opened for writing."""
    import copy

    from steam_artwork_finder import binvdf
    from steam_artwork_finder.shortcuts import legacy_appid

    root = binvdf.loads(path.read_bytes())
    key = next(k for k, v in root.items() if k.lower() == "shortcuts")
    old = [e for e in root[key].values() if isinstance(e, dict)]
    name_key = lambda e: next(k for k in e if k.lower() == "appname")  # noqa: E731
    drop = [d.casefold() for d in edit.get("drop", [])]
    keep = [e for e in old if not any(d in str(e.get_value(name_key(e), "")).casefold() for d in drop)]
    have = {str(e.get_value(name_key(e), "")).casefold() for e in keep}
    for name in (n for n in edit.get("add", []) if n.casefold() not in have):
        e = copy.deepcopy(old[0])
        exe = f'"/home/player/Games/{name}/game.exe"'
        e.set_string(name_key(e), name)
        e.set_string(next(k for k in e if k.lower() == "exe"), exe)
        e.set_string(next((k for k in e if k.lower() == "icon"), "icon"), "")
        e.set_int32(next((k for k in e if k.lower() == "appid"), "appid"), binvdf._to_int32(legacy_appid(exe, name)))
        keep.append(e)
    fresh = binvdf.VDFMap()
    for i, e in enumerate(keep):
        fresh[str(i)] = e
    root[key] = fresh
    path.write_bytes(binvdf.dumps(root))
    print("game list:", ", ".join(str(e.get_value(name_key(e), "")) for e in keep), flush=True)


def keep_real_art(real_grid: Path):
    """With a changed game list, Get All Artwork fills every game and the other scenes start from
    what it filled. So that they show the artwork you chose rather than the top option, each piece
    it gets for one of your own games is swapped for the picture already in your grid folder; only
    the added games keep the top option."""
    from steam_artwork_finder import artwork

    spool = appmod._spool_pick

    def yours(data: bytes, appid: int, slot_key: str):
        p = artwork.existing(real_grid, appid, artwork.SLOTS[slot_key])
        if p is not None:
            try:
                data = p.read_bytes()
            except OSError:
                pass
        return spool(data, appid, slot_key)

    appmod._spool_pick = yours


def setup_real(have: str = "full"):
    """Copy the real Steam user's game list and artwork, and the app's settings, into the throwaway
    home. have="none" leaves the artwork behind, for Get All Artwork to fill."""
    import re

    from steam_artwork_finder.shortcuts import ShortcutsFile
    from steam_artwork_finder.steam import list_users

    src = real_steam_root()
    user = list_users(src)[0]
    print("copying Steam user", user.account_id, "from", src, flush=True)
    (root / "config").mkdir(parents=True)
    login = (src / "config/loginusers.vdf").read_text(encoding="utf-8", errors="replace")
    login = re.sub(r'("PersonaName"\s+)"[^"]*"', rf'\1"{PERSONA}"', login, flags=re.I)  # never the real name
    login = re.sub(r'("AccountName"\s+)"[^"]*"', r'\1"player"', login, flags=re.I)
    (root / "config/loginusers.vdf").write_text(login, encoding="utf-8")
    ucfg = src / "userdata" / user.account_id / "config"
    cfg_dst = root / "userdata" / user.account_id / "config"
    cfg_dst.mkdir(parents=True)
    shutil.copy2(ucfg / "shortcuts.vdf", cfg_dst / "shortcuts.vdf")
    if os.environ.get("LIBRARY"):
        edit_library(cfg_dst / "shortcuts.vdf", json.loads(os.environ["LIBRARY"]))
        if have == "none":
            keep_real_art(ucfg / "grid")
    if have != "none" and (ucfg / "grid").is_dir():
        shutil.copytree(ucfg / "grid", cfg_dst / "grid", ignore=shutil.ignore_patterns(".replaced"),
                        ignore_dangling_symlinks=True)  # Steam leaves broken links behind
    else:
        (cfg_dst / "grid").mkdir()
    sf = ShortcutsFile(cfg_dst / "shortcuts.vdf")
    for sc in sf.shortcuts:  # icons linked to the real grid folder point at the copy instead
        icon = getattr(sc, "icon", "") or ""
        if "/grid/" in icon:
            moved = cfg_dst / "grid" / Path(icon).name
            sc.set_icon(str(moved) if moved.exists() else "")
    sf.save()
    conf = json.loads((REAL_HOME / ".config/steam-artwork-finder/config.json").read_text())
    for k in ("steam_dir", "steam_root", "last_image_folder", "pending_icons"):
        conf.pop(k, None)
    if have == "none":
        conf["applied"], conf["no_art"] = {}, {}
    conf.update(json.loads(os.environ.get("CONF", "{}")))  # e.g. CONF='{"get_all_animated": true}'
    conf_dir = HOME / ".config/steam-artwork-finder"
    conf_dir.mkdir(parents=True)
    (conf_dir / "config.json").write_text(json.dumps(conf))
    cache = REAL_HOME / ".cache/steam-artwork-finder"
    if cache.is_dir() and os.environ.get("COPY_CACHE", "1") == "1":  # thumbnails already fetched: faster
        shutil.copytree(cache, HOME / ".cache/steam-artwork-finder", ignore=shutil.ignore_patterns("picks"))
    return ShortcutsFile(cfg_dst / "shortcuts.vdf")


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


import steam_artwork_finder.gui.app as appmod  # noqa: E402
from steam_artwork_finder.gui import motion, smooth, theme  # noqa: E402

if not REAL or os.environ.get("FAKE_NET") == "1":
    sgdb.SteamGridDB = FakeSGDB
    appmod.SteamGridDB = FakeSGDB
    motion.video_supported = lambda: False
    appmod.SteamStore.match = lambda self, name: None  # no Steam store either
elif os.environ.get("VIDEO") != "1":
    # Offscreen, video frames may not reach a grab: animated options play from their own files instead.
    motion.video_supported = lambda: False
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
        acc = self.win.header.account  # the Steam login name and avatar: blurred
        if acc.isVisible():
            blur(p, pm, QRect(acc.mapTo(self.win, QPoint(0, 0)), acc.size()).adjusted(-2, -2, 2, 2))
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
        if now - self._last >= self._gap * self._speed:  # sped up: fewer frames, same frame rate played back
            self._last = now
            img = self.capture()
            if self.rec_scale != 1.0:
                img = img.scaledToWidth(round(img.width() * self.rec_scale), Qt.TransformationMode.SmoothTransformation)
            # kept as PNG: a long recording of raw frames can run to gigabytes
            self.frames.append((qimage_png(img.convertToFormat(QImage.Format.Format_RGB888)), now, self._speed))

    def save_anim(self, name, crop=None, scale=1.0, quality=90, hold_last=1500):
        self.recording = False
        frames = self.frames
        ims, durs = [], []
        for i, (img, t, sp) in enumerate(frames):
            pil = Image.open(io.BytesIO(img)).convert("RGB")
            if crop is not None:
                k = DPR * self.rec_scale
                pil = pil.crop((int(crop[0] * k), int(crop[1] * k), int((crop[0] + crop[2]) * k), int((crop[1] + crop[3]) * k)))
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


def blur(p: QPainter, pm, r: QRect, k: int = 9):
    """Paint `r` of `pm` back over itself heavily blurred (scaled right down, then up again twice)."""
    dpr = pm.devicePixelRatio()
    img = pm.copy(QRect(round(r.x() * dpr), round(r.y() * dpr), round(r.width() * dpr), round(r.height() * dpr))).toImage()
    w, h = img.width(), img.height()
    for _ in range(2):
        img = img.scaled(max(1, w // (k * 2)), max(1, h // k), Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img.setDevicePixelRatio(dpr)
    p.drawImage(r.topLeft(), img)


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
