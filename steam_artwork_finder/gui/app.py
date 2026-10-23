"""The main window."""

from __future__ import annotations

import itertools
import os
import tempfile
import time
from collections import OrderedDict
import sys
from pathlib import Path

from shiboken6 import isValid
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from .. import artwork
from ..artwork import IMAGE_EXTS, SLOTS, Slot, current_icon, prune_replaced, remove_asset, save_asset, undo
from ..config import Config, cache_dir
from ..exeicon import exe_icon, exe_path
from ..pending import LocalFile, Pending, Pick
from ..sgdb import Asset, AuthError, Game, GoneError, SGDBError, SteamGridDB, clean_name, generic_name, name_variants, search_name
from ..shortcuts import Shortcut
from ..steamstore import SteamAsset, SteamStore, StoreGame
from ..steam import (
    SteamUser,
    avatar_path,
    find_steam_roots,
    list_users,
    shutdown_steam,
    start_steam,
    steam_running,
    wait_until_closed,
)
from . import icons, localfile, motion, theme, workers
from .chrome import ContentBar, Header, StatusBar, SteamPopover
from .steamfolder import SteamFolderPopover, tilde
from .scrim import Scrim, run_dialog
from .dialogs import (
    AboutDialog, ConfirmDialog, KeyPopover, MatchDialog, QuitDialog, RestoreDialog, tell,
)
from .gamepage import GamePage
from .library import ROLE_INDEX, SORTS, GameList, StartPage, art_text, sorted_indices
from .transition import ApplyFill, PageTransition
from .tray import ROLE_LOAD, ROLE_PIX, OptionTray
from .tray import _full_for as tray_full_pixmap
from .widgets import _cache as pixmap_cache
from .widgets import ArtView, Notice, Toast, _largest_frame, decode_scaled, label, load_pixmap, read_image, remember

ORDER = ("grid", "wide", "hero", "logo", "icon")
EXE_STYLE = "exe"  # the style of an icon taken from the game's .exe
_exe_ids = itertools.count(-1, -1)  # SteamGridDB ids are positive, so these never clash


ISSUES_URL = "https://github.com/acidtwin/steam-artwork-for-non-steam-games/issues"


def _join(words: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


class _Bridge(QObject):
    """Carries callbacks made on worker threads over to the GUI thread."""

    wait = Signal(float)  # SteamGridDB asked us to wait this many seconds


def is_exact(sc_name: str, game_name: str, exe: str = "") -> bool:
    """The SteamGridDB name is the shortcut's name, give or take launcher tags (or, for a
    generic name like "game.exe", the name of the folder the game is in)."""
    want = clean_name(game_name).casefold()
    term = search_name(sc_name, exe)
    return any(v.casefold() == want for v in name_variants(sc_name) + name_variants(term))


# The biggest a waiting pick is shown at (Steam's own sizes): its preview is decoded no larger.
PICK_BOUND = {"grid": (600, 900), "wide": (920, 430), "hero": (1920, 620), "logo": (1280, 720), "icon": (256, 256)}


def decode_pick(src, slot_key: str) -> QImage:
    """Worker: a waiting pick's picture, from its file path or bytes, scaled down to PICK_BOUND."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImageReader

    buf = None
    if isinstance(src, (bytes, bytearray)):
        buf = QBuffer()
        buf.setData(QByteArray(bytes(src)))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buf)
    else:
        reader = QImageReader(str(src))
    reader.setAutoTransform(True)
    if slot_key == "icon":  # an .ico's biggest size; never for the others, where a file's "images"
        _largest_frame(reader)  # are an animation's frames, and stepping through them decodes each one
    w, h = PICK_BOUND.get(slot_key, (1920, 1920))
    size = reader.size()
    if size.isValid() and (size.width() > w or size.height() > h):
        scale = min(w / size.width(), h / size.height())
        reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
    img = reader.read()
    del buf
    return img


def picks_dir() -> Path:
    return cache_dir() / "picks" / str(os.getpid())  # this run's own: another copy of the app has its own


def _spool_pick(data: bytes, appid: int, slot_key: str) -> tuple[str, QImage]:
    """Worker: keep a Get Artwork download on disk until Apply (not in memory, which ran to
    gigabytes on big libraries) and decode its preview here, at the size it's shown."""
    d = picks_dir()
    d.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=f"{appid}-{slot_key}-", dir=d)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path, decode_pick(data, slot_key)


def clear_picks():
    """Waiting picks don't outlive the app: drop the spooled downloads of past runs."""
    import shutil

    try:
        runs = list((cache_dir() / "picks").iterdir())
    except OSError:
        return
    for d in runs:
        try:
            pid = int(d.name)
            age = time.time() - d.stat().st_mtime
        except (ValueError, OSError):
            continue
        if pid == os.getpid():
            continue
        # A run older than a day is over whatever its PID now belongs to (PIDs get reused).
        if age < 86400 and _pid_alive(pid):
            continue
        shutil.rmtree(d, ignore_errors=True)


def _pid_alive(pid: int) -> bool:
    """Is that process still running? On Windows os.kill(pid, 0) would end it, so there the
    answer is "maybe" (True) and only age clears a past run's folder."""
    if sys.platform == "win32":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # someone else's process: alive
    return True


class PixmapLRU(OrderedDict):
    """pick_pix: newest last, oldest let go once over `budget` bytes."""

    def __init__(self, budget: int):
        super().__init__()
        self.budget, self.used = budget, 0

    @staticmethod
    def _size(pm) -> int:
        return pm.width() * pm.height() * 4 if pm is not None else 0

    def __setitem__(self, key, pm):
        if key in self:
            self.used -= self._size(super().pop(key))
        super().__setitem__(key, pm)
        self.used += self._size(pm)
        while self.used > self.budget and len(self) > 1:
            _, old = self.popitem(last=False)
            self.used -= self._size(old)

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default

    def pop(self, key, *default):
        if key in self:
            pm = super().pop(key)
            self.used -= self._size(pm)
            return pm
        return default[0] if default else None

    def clear(self):
        super().clear()
        self.used = 0


def _exe_icon_png(appid: int, exe_field: str) -> Path | None:
    """Worker: the icon in a shortcut's .exe as a cached PNG, or None."""
    exe = exe_path(exe_field)
    if exe is None:
        return None
    try:
        mtime = exe.stat().st_mtime_ns
    except OSError:
        return None
    dest = cache_dir() / "exe-icons" / f"{appid}-{mtime}.png"
    if dest.is_file():
        return dest
    data = exe_icon(exe)
    img = QImage.fromData(data) if data else QImage()
    if img.isNull():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if img.save(str(tmp), "PNG"):
        os.replace(tmp, dest)
        return dest
    return None


def _thumb_image(url: str, fetch) -> QImage:
    """Worker: a thumbnail downloaded (or from the cache) and decoded, so the GUI thread only shows it."""
    img = read_image(workers.cached_download(url, fetch))
    if img.isNull():
        raise GoneError("Not a picture")  # trying again won't change that
    return img


PROBE_FIRST = 15_000  # ms before a given-up picture is first tried again by itself
PROBE_MAX = 120_000  # and at most this long between tries after that


def _alive(target) -> bool:
    """Whether a thumbnail's target is still around (strips are rebuilt, See all is emptied)."""
    try:
        if isinstance(target, ArtView):
            return isValid(target)
        if isinstance(target, QListWidgetItem):
            return isValid(target) and target.listWidget() is not None
    except RuntimeError:
        return False
    return False


def _load_state(target, state: tuple):
    """Tell a thumbnail's target it's ("loading", retry n) or ("failed", retry fn)."""
    try:
        if isinstance(target, ArtView):
            if state[0] == "failed":
                target.set_failed(state[1])
            else:
                target.set_loading(state[1])
        elif isinstance(target, QListWidgetItem):
            if target.data(ROLE_PIX) is None:
                target.setData(ROLE_LOAD, state)
    except RuntimeError:  # gone meanwhile
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        theme.track_focus()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1280, 800)  # Steam Deck screen; main() maximises on screens that small
        self.home = Path.home()
        self.config = Config()
        self.library_sort = self.config.data.get("library_sort", "az")
        if self.library_sort not in {k for k, _, _ in SORTS}:
            self.library_sort = "az"
        self.sgdb = SteamGridDB(self.config.api_key)
        self.steam = SteamStore()
        # appid -> (the Steam store game or None, {slot key: its official piece}), once looked up
        self.steam_found: dict[int, tuple[StoreGame | None, dict[str, SteamAsset]]] = {}
        self.users: list[SteamUser] = []
        self.user: SteamUser | None = None
        self.sfile = None
        self.status: dict[int, str] = {}  # appid -> "notfound" | "check" | "getting"
        self.guesses: dict[int, Game] = {}  # best guesses not yet confirmed by an action
        self._matching = False  # the startup lookup of every game is running
        self._match_count = (0, 0)  # games looked up so far, of how many
        self._exe_icons: dict = {}  # (appid, exe field) -> the icon from its .exe, or None
        self._exe_icon_busy: set = set()
        self._grid_names: set[str] | None = None  # file names in the grid folder, read once per refresh
        self._gaps_checked: set[int] = set()  # games whose missing pieces were asked about this session
        self._note: tuple[str, str] | None = None  # a short-lived status bar message
        self._apply_fx: dict | None = None  # the Apply button filling while it writes
        self._note_timer = QTimer(self, singleShot=True, interval=5000, timeout=self._clear_note)
        self.candidates: dict[int, int] = {}  # appid -> how many similar names SteamGridDB returned
        self.asset_cache: dict[tuple, list[Asset]] = {}
        self.pending = Pending()
        self.pick_pix = PixmapLRU(256 * 1024 * 1024)  # (appid, slot key) -> waiting pick's picture
        self._pick_loading: set = set()
        self.motion_files: dict[int, tuple[str, str]] = {}  # animated asset id -> (kind, cached file)
        self.full_files: dict[int, str] = {}  # animated asset id -> its full file, for big places
        self._full_loading: set[int] = set()
        self._fetches = workers.Shared()  # thumbnails and preview files on their way, one download each
        # Pictures given up on: id(target) -> (target, retry). All are tried again once anything
        # downloads (or the connection is found again); until then one is tried now and then.
        self._failed: dict[int, tuple] = {}
        self._probe_wait = PROBE_FIRST
        self._probe = QTimer(self, singleShot=True)
        self._probe.timeout.connect(self._probe_failed)
        self._anim_files: dict[tuple, bool] = {}  # (artwork file, mtime) -> animated?
        # Each game's artwork files in ORDER (None where it has none), so painting and
        # counting never touch the disk. Forgotten whenever the app writes a game's files.
        self._cov: dict[int, list[Path | None]] = {}
        # Many small changes in a row (a bulk Get Artwork) redraw everything at most every 100 ms.
        self._refresh_timer = QTimer(self, singleShot=True, interval=100, timeout=self.refresh_all)
        self.last_errors: list[str] = []
        self.current = -1
        self.busy = ""  # "" | "fetch" | "apply" | "restart"
        self._stop_fill = False  # the user asked a Get Artwork run to stop
        self.progress_text = ""
        self.restart_needed = False
        self.conn = "disconnected"
        self._close_after_apply = False
        self._steam_poll = False  # a background Steam check is running
        # The rate-limit callback runs on whichever worker thread made the request.
        self._bridge = _Bridge(self)
        self._bridge.wait.connect(lambda secs: self._progress(f"SteamGridDB asked us to wait {int(secs)} s…"))
        self.sgdb.on_wait = self._bridge.wait.emit

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = Header()
        self.header.home_clicked.connect(self.go_home)
        self.header.accounts_clicked.connect(lambda: self.show_account_menu(self.header.account))
        self.header.more_clicked.connect(self.show_more_menu)
        outer.addWidget(self.header)
        self.notice = Notice()
        outer.addWidget(self.notice)

        self.split = QSplitter()
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        self.game_list = GameList(self)
        self.game_list.setMinimumWidth(260)
        self.game_list.picked.connect(self._picked_in_sidebar)
        # The start page shows the games the sidebar's search and filter leave.
        for changed in (self.game_list.search.textChanged, self.game_list.filter_changed):
            changed.connect(self._library_narrowed)
        self.split.addWidget(self.game_list)

        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.cbar = ContentBar()
        self.cbar.home_clicked.connect(self.go_home)
        self.cbar.rest_focus = self._focus_page
        cl.addWidget(self.cbar)
        self.stage = QStackedWidget()
        self.stage.setObjectName("stage")
        self.stage.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.nosteam = self._full_message(
            "Couldn't find Steam", "Is Steam installed, and have you signed in to it at least once?", None,
            "Choose Steam Folder…", self.choose_steam_folder, "go")
        self.nogames = self._full_message(
            "No non-Steam games yet", "Add one in Steam, then come back here.",
            "In Steam, open Games › Add a Non-Steam Game to My Library…, pick your game, "
            "then click Reload Game List below.", "Reload Game List", self.reload_shortcuts, "go")
        self.start = StartPage(self)
        self.start.picked.connect(self._picked_on_wall)
        bar = self.game_list.homebar
        bar.home.clicked.connect(self.go_home)
        bar.menu.chosen.connect(self.set_library_view)
        bar.sort_menu.chosen.connect(self.set_library_sort)
        bar.sort.set_view(self.library_sort)
        view = self.config.data.get("library_view", "grid")
        bar.view.set_view(view)
        self.start.set_view(view)
        self.page = GamePage(self)
        self.page.change_match.connect(self.change_match)
        self.page.confirm_match.connect(self.confirm_match)
        for w in (self.nosteam, self.nogames, self.start, self.page):
            self.stage.addWidget(w)
        cl.addWidget(self.stage, 1)
        self.transition = PageTransition(self.stage)
        self.statusbar = StatusBar()
        self.statusbar.steam_clicked.connect(self.show_folder_popover)
        self.statusbar.process_clicked.connect(self.show_steam_popover)
        self.statusbar.connection_clicked.connect(self.ask_api_key)
        self.statusbar.stop_clicked.connect(self.stop_fill)
        cl.addWidget(self.statusbar)
        self.split.addWidget(col)

        self.tray = OptionTray(self)
        self.tray.place_over(self.stage, self)  # slides in over the page rather than squeezing it
        self.tray.preview.connect(self._tray_preview)
        self.tray.slot_changed.connect(self._show_row)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([300, 980])
        outer.addWidget(self.split, 1)

        self.scrim = Scrim(self)  # dims the window behind the tray, pop-overs and menus
        self.popover = SteamPopover(self)
        self.popover.anchor_widget = self.statusbar.process
        self.keypop = KeyPopover(self)
        self.keypop.anchor_widget = self.statusbar.connection
        self.keypop.saved.connect(self._key_saved)
        self.folderpop = SteamFolderPopover(self)
        self.folderpop.anchor_widget = self.statusbar.steam
        self.folderpop.use_root.connect(self.use_steam_root)
        self.folderpop.accounts_wanted.connect(lambda b: self.show_account_menu(b, from_folder=True))
        self.popover.set_auto_restart(self.auto_restart)
        self.popover.auto_restart_changed.connect(self._set_auto_restart)
        self.popover.set_apply_on_quit(self.apply_on_quit)
        self.popover.apply_on_quit_changed.connect(self._set_apply_on_quit)
        self.popover.restart_clicked.connect(self.restart_steam_now)

        self.toast = Toast(central)
        self.toast.center_on = self.stage
        self.steam_timer = QTimer(self, interval=5000, timeout=self._poll_steam)
        self.steam_timer.start()
        for keys, fn in ((QKeySequence.StandardKey.Find, lambda: self.game_list.search.setFocus()),
                         (QKeySequence.StandardKey.Undo, self.toast.undo_now),
                         (QKeySequence.StandardKey.Refresh, self.reload_game_list),
                         (QKeySequence("Ctrl+R"), self.reload_game_list),
                         (QKeySequence("Ctrl+Q"), self.close),
                         (QKeySequence("Ctrl+Return"), self._apply_key),
                         (QKeySequence("Ctrl+Enter"), self._apply_key),
                         (QKeySequence.StandardKey.Back, self._back_key)):  # Alt+Left
            act = QAction(self)
            act.setShortcut(keys)
            # Nothing behind the dim reacts to the keyboard either; quitting still works.
            act.triggered.connect(
                lambda _=False, fn=fn: fn() if fn == self.close or not self.scrim.is_covering() else None)
            self.addAction(act)
        self.detect_steam()
        self.check_connection()

    # ---- layout pieces ----------------------------------------------------

    def show_more_menu(self):
        from .gamemenu import SHADOW, GameMenu, Item

        menu = GameMenu(None, "", None, [
            "Library",
            Item("Reload Game List", "refresh", self.reload_game_list, shortcut="Ctrl+R"),
            Item("Open Artwork Folder", "folder", self.open_grid_folder, bool(self.user)),
            Item("Restore Steam's Game List…", "history", self.restore_backup, bool(self.sfile)),
            "Settings",
            Item("Steam Folder…", "folder", self.show_folder_popover),
            Item("SteamGridDB API Key…", "key", self.ask_api_key),
            Item("Restart Steam When I Apply", "power", self._menu_auto_restart, toggle=self.auto_restart),
            Item("Apply When I Quit", "exit", self._menu_apply_on_quit, toggle=self.apply_on_quit),
            Item("Show Animated Artwork", "play", self._set_show_animated, toggle=self.show_animated),
            Item("Get All Prefers Animated Artwork", "download", self._set_get_all_animated, toggle=self.get_all_animated),
            Item("Use Steam's Official Artwork", "images", self._set_steam_art, toggle=self.steam_art),
            "Help",
            Item("Report a Problem", "bug", lambda: QDesktopServices.openUrl(QUrl(ISSUES_URL)), external=True),
            Item("About", "info", self.about),
            None,
            Item("Exit", "exit", self.close, shortcut="Ctrl+Q"),
        ], self, width=300)
        self._more_menu = menu  # kept alive while it shows
        menu.adjustSize()
        b = self.header.more
        menu.popup(b.mapToGlobal(QPoint(b.width() - menu.width() + SHADOW, b.height() + 4)))

    def show_account_menu(self, anchor: QWidget, from_folder: bool = False):
        """The account switcher, under the avatar in the top bar or under Switch in the Steam
        folder pop-over (which then leaves out its own Steam Folder… link)."""
        from .accounts import Account, AccountMenu

        if not self.users or self._wait_for_busy():
            return
        cur = self.users.index(self.user) if self.user in self.users else -1
        accounts = [Account(u.label, self._game_count(u), self._avatar_of(u)) for u in self.users]
        folder = "" if from_folder else (tilde(self.steam_root) if self.steam_root else "")
        menu = AccountMenu(accounts, cur, folder, self)
        menu.chosen.connect(self._account_changed)
        menu.folder.connect(self.show_folder_popover)
        chip = self.header.account
        if anchor is chip:
            chip.open = True
            chip.update()
            menu.closed.connect(lambda: (setattr(chip, "open", False), chip.update()))
        self._account_menu = menu  # kept alive while it shows
        menu.popup_under(anchor)

    def _game_count(self, user) -> int | None:
        """How many non-Steam games `user` has; others' lists are read once per change on disk."""
        if user is self.user and self.sfile is not None:
            return len(self.sfile.shortcuts)
        path = user.shortcuts_path
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return 0  # no list yet: Steam writes it with the first non-Steam game
        cache = self.__dict__.setdefault("_counts", {})
        if cache.get(path, (None,))[0] != stamp:
            from ..shortcuts import ShortcutsFile

            try:
                cache[path] = (stamp, len(ShortcutsFile(path).shortcuts))
            except Exception:
                cache[path] = (stamp, None)
        return cache[path][1]

    def _avatar_of(self, user) -> QPixmap | None:
        p = avatar_path(user.userdata.parent.parent, user.account_id)
        pm = load_pixmap(p) if p else None
        return pm if pm is not None and not pm.isNull() else None

    def show_get_menu(self, button):
        """The still/animated menu under the arrow beside Get All Artwork."""
        from .gamemenu import SHADOW, GameMenu, Item

        on = self.get_all_animated
        menu = GameMenu(None, "", None, [
            "Get All Takes",
            Item("Still Artwork", "images", lambda: self._set_get_all_animated(False), checked=not on),
            Item("Animated Artwork", "play", lambda: self._set_get_all_animated(True), checked=on),
        ], self, width=240)
        menu.setAccessibleName("Get All Artwork options")
        self._get_menu = menu  # kept alive while it shows
        menu.adjustSize()
        menu.popup(button.mapToGlobal(QPoint(button.width() - menu.width() + SHADOW, button.height() + 4)))
        theme.hold_open(button)

    def _menu_auto_restart(self, on: bool):
        self._set_auto_restart(on)
        self.popover.set_auto_restart(on)

    def _menu_apply_on_quit(self, on: bool):
        self._set_apply_on_quit(on)
        self.popover.set_apply_on_quit(on)

    def _full_message(self, title, text, hint, button, fn, kind) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        # A fixed-width column: word-wrapped labels only get the height they need when the layout
        # gives them their full width (an alignment flag squeezes them to their narrow hint).
        column = QWidget()
        column.setFixedWidth(460)
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        for t, name in ((title, "title"), (text, "muted"), (hint, "faint")):
            if t:
                lab = label(t, name, wrap=True)
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cl.addWidget(lab)
        cl.addSpacing(12)
        # The button sits inside the column, out of reach of the stage background rule, which
        # paints every widget two levels below the stack (and would flatten a button there).
        b = QPushButton(button)
        if kind:
            b.setProperty("kind", kind)
        b.clicked.connect(fn)
        cl.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()  # centred by stretches, not an alignment flag, so its height follows the wrapping
        row.addStretch(1)
        row.addWidget(column)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(2)
        return w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.place()

    # ---- state helpers used by the pages ----------------------------------

    def slot_path(self, sc: Shortcut, slot: Slot, fresh: bool = False) -> Path | None:
        """The file in use for a piece. `fresh` looks on disk again (before acting on the answer)."""
        if fresh:
            self.forget_files(sc.appid)
        paths = self._cov.get(sc.appid)
        if paths is None:
            grid = self.user.grid_dir
            if self._grid_names is None or not self._cov:
                # One directory read instead of a dozen file checks per game.
                try:
                    with os.scandir(grid) as it:
                        self._grid_names = {e.name for e in it}
                except OSError:
                    self._grid_names = set()
            names = self._grid_names

            def have(key):
                stem = f"{sc.appid}{SLOTS[key].suffix}"
                return next((grid / (stem + ext) for ext in IMAGE_EXTS if stem + ext in names), None)

            paths = self._cov[sc.appid] = [(current_icon(sc, grid) if sc.icon else have(k)) if k == "icon" else have(k)
                                           for k in ORDER]
        return paths[ORDER.index(slot.key)]

    def forget_files(self, appid: int | None = None):
        """Look on disk again for one game's artwork files (or every game's, with None)."""
        self._grid_names = None
        if appid is None:
            self._cov.clear()
            self._exe_icons.clear()  # looked up again (in a worker) in case a game was updated
        else:
            self._cov.pop(appid, None)

    def coverage(self, sc: Shortcut) -> list[bool]:
        return [self.slot_path(sc, SLOTS[k]) is not None for k in ORDER]

    def unavailable(self, sc: Shortcut) -> set[str]:
        """Missing pieces SteamGridDB has nothing for, so the game already has all there is."""
        return self.config.no_art(sc.appid)

    def art_states(self, sc: Shortcut) -> list:
        """Per piece: True (has it), "none" (SteamGridDB has none) or False (missing)."""
        none = self.unavailable(sc)
        return [have or ("none" if k in none else False) for k, have in zip(ORDER, self.coverage(sc))]

    def complete(self, sc: Shortcut) -> bool:
        """Every piece there is: on disk, or not on SteamGridDB."""
        return all(self.art_states(sc))

    def cover_path(self, sc: Shortcut) -> Path | None:
        return self.slot_path(sc, SLOTS["grid"])

    def list_icon(self, sc: Shortcut) -> tuple[Path | None, bool]:
        """(picture, contain) for the game's icon in the app's own lists: its icon, else the
        one in its .exe, else one cut from its cover, banner or wide cover (a logo fitted
        whole), else (None, False) for the letter. Steam's own icon isn't touched by this."""
        path = self.slot_path(sc, SLOTS["icon"]) or self.exe_icon_file(sc)
        if path is not None:
            return path, False
        for key in ("grid", "hero", "wide", "logo"):
            path = self.slot_path(sc, SLOTS[key])
            if path is not None:
                return path, key == "logo"
        return None, False

    def exe_icon_file(self, sc: Shortcut, wait: bool = False) -> Path | None:
        """The icon inside the game's .exe, saved once as a PNG in the cache; None if it has none
        (or, without `wait`, until a worker has looked: lists paint without touching the disk,
        which may be a sleeping drive or a network share)."""
        key = (sc.appid, sc.exe)
        if key in self._exe_icons:
            return self._exe_icons[key]
        if wait:
            self._exe_icons[key] = _exe_icon_png(*key)
            return self._exe_icons[key]
        if key not in self._exe_icon_busy:
            self._exe_icon_busy.add(key)

            def done(path, key=key):
                self._exe_icon_busy.discard(key)
                self._exe_icons[key] = path
                if path is not None:
                    self.game_list.list.viewport().update()
                    self.start.wall.viewport().update()

            workers.run(_exe_icon_png, *key, on_done=done,
                        on_error=lambda _m, key=key: (self._exe_icon_busy.discard(key),
                                                      self._exe_icons.__setitem__(key, None)))
        return None

    def stage_exe_icon(self, sc: Shortcut) -> bool:
        """When SteamGridDB has no icon, stage the one in the game's .exe for Steam (written on
        Apply like any pick). True if one was staged. Callers look the .exe up in a worker first
        (exe_icon_file's cache), so this rarely touches the disk on the window's thread."""
        if self.busy == "apply" or self.pending.get(sc.appid, "icon") is not None \
                or self.slot_path(sc, SLOTS["icon"]):
            return False
        path = self.exe_icon_file(sc, wait=True)
        if path is None:
            return False
        try:
            data = path.read_bytes()
        except OSError:
            return False
        img = QImage.fromData(data)
        # No new title along with it: this can happen unasked (see check_gaps), and a rename the
        # user didn't ask for shouldn't ride on it.
        self._stage(sc, "icon", Pick(Asset(next(_exe_ids), "", "", img.width(), img.height(), "image/png", 0,
                                           EXE_STYLE, ""), data), rename=False)
        return True

    def icon_pixmap(self, sc: Shortcut) -> QPixmap | None:
        p = self.slot_path(sc, SLOTS["icon"])
        return load_pixmap(p) if p else None

    def current_shortcut(self) -> Shortcut | None:
        if self.sfile and 0 <= self.current < len(self.sfile.shortcuts):
            return self.sfile.shortcuts[self.current]
        return None

    def match_info(self, sc: Shortcut) -> dict | None:
        """The saved match, or the unconfirmed best guess, as {id, name, year}."""
        m = self.config.get_match(sc.appid)
        if m:
            return m
        g = self.guesses.get(sc.appid)
        return {"id": g.id, "name": g.name, "year": g.year} if g else None

    def _fill_still_wanted(self, sc: Shortcut, asset: Asset, gid: int | None) -> bool:
        """Get Artwork's piece is still for the game the shortcut is matched to now."""
        if isinstance(asset, SteamAsset):
            saved = self.config.get_steam_match(sc.appid)
            return saved is None or saved.get("id") == asset.appid
        m = self.match_info(sc)
        return m is not None and m["id"] == gid

    def match_candidates(self, sc: Shortcut) -> int:
        return self.candidates.get(sc.appid, 0)

    def current_game(self) -> Game | None:
        sc = self.current_shortcut()
        m = self.match_info(sc) if sc else None
        return Game(m["id"], m["name"], m.get("year", "")) if m else None

    def commit_match(self, sc: Shortcut):
        """Make a best guess permanent (the user acted on it)."""
        g = self.guesses.pop(sc.appid, None)
        if g and not self.config.get_match(sc.appid):
            self.config.set_match(sc.appid, g.id, g.name, g.year)

    def load_thumb(self, asset: Asset, target):
        """Show `asset`'s thumbnail in `target` (an ArtView, a See all option, or a callback taking
        the pixmap). Until it comes the target shows it's loading, and counts any retries; if it
        still can't be had, the target says so and a click tries again."""
        self._failed.pop(id(target), None)  # trying again now
        if asset.animated:
            self._load_animated(asset, target)
            return
        key = ("thumb", asset.thumb)
        pm = pixmap_cache.get(key)
        if pm is not None:
            self._deliver_thumb(target, pm)
            return

        def done(img):
            self._connection_back()
            pm = pixmap_cache.get(key)  # made once for everyone who asked
            if pm is None:
                pm = QPixmap.fromImage(img)
                remember(key, pm)
            self._deliver_thumb(target, pm)

        n = self._fetches.get(key, _thumb_image, asset.thumb, self.sgdb.download, on_done=done,
                              on_error=lambda _m: self._gave_up(target, lambda: self.load_thumb(asset, target)),
                              on_gone=lambda _m: _load_state(target, ("failed", lambda: self.load_thumb(asset, target))),
                              on_retry=lambda n: _load_state(target, ("loading", n)))
        _load_state(target, ("loading", n))

    @staticmethod
    def _deliver_thumb(target, pm: QPixmap):
        try:
            if isinstance(target, ArtView):
                target.set_pixmap(pm)
            elif isinstance(target, QListWidgetItem):
                target.setData(ROLE_LOAD, None)
                target.setData(ROLE_PIX, pm)
            elif callable(target):
                target(pm)
        except RuntimeError:  # the widget was replaced meanwhile
            pass

    def _gave_up(self, target, retry):
        """`target` couldn't load even after retrying: it says so (a click tries again), and it's
        tried again by itself once the connection is back."""
        _load_state(target, ("failed", retry))
        self._failed[id(target)] = (target, retry)
        if not self._probe.isActive():
            self._probe.start(self._probe_wait)

    def _connection_back(self):
        """Something downloaded: try every picture that was given up on again."""
        self._probe_wait = PROBE_FIRST
        if not self._failed:
            return
        self._probe.stop()
        waiting, self._failed = list(self._failed.values()), {}
        for target, retry in waiting:
            if _alive(target):
                retry()

    def _probe_failed(self):
        """Now and then, while pictures are given up on, try one again to see if the connection's back
        (waiting longer each time it isn't); if it is, it brings the rest back with it."""
        for k, (target, _r) in list(self._failed.items()):
            if not _alive(target):
                del self._failed[k]
        if not self._failed:
            return
        self._probe_wait = min(self._probe_wait * 2, PROBE_MAX)
        _target, retry = self._failed.pop(next(iter(self._failed)))
        retry()

    # ---- animated artwork -----------------------------------------------------

    @property
    def show_animated(self) -> bool:
        return self.config.data.get("show_animated", True)

    def _set_show_animated(self, on: bool):
        """Animated options in the strips and the tray, and animated artwork playing on the game page
        and in the library, or none of it; reloads the open game's options."""
        self.config.data["show_animated"] = on
        self.config.save()
        self.start.wall.viewport().update()
        sc, game = self.current_shortcut(), self.current_game()
        if sc is not None and game is not None and self.stage.currentWidget() is self.page:
            self._load_rows(sc, game)
            if self.tray.is_open():
                self.tray.open_slot(self.tray.slot)

    @property
    def get_all_animated(self) -> bool:
        """Get All Artwork takes the top animated cover, wide cover, banner and logo where there is one."""
        return self.config.data.get("get_all_animated", False)

    def _set_get_all_animated(self, on: bool):
        self.config.data["get_all_animated"] = on
        self.config.save()
        self.page.card.set_get_animated(on)  # the Get All button's tag follows

    def _load_animated(self, asset: Asset, target):
        """An animated option plays its video thumbnail; where Qt can't play video, the file itself."""
        kind, url = ("video", asset.thumb) if motion.video_supported() else ("frames", asset.url)

        def deliver(pm):
            try:
                if isinstance(target, QListWidgetItem):
                    target.setData(ROLE_LOAD, None)
                    target.setData(ROLE_PIX, pm)
                elif callable(target):
                    target(pm)
            except RuntimeError:  # the item was replaced meanwhile
                pass

        self._failed.pop(id(target), None)

        def done(path):
            self._connection_back()
            self.motion_files[asset.id] = (kind, path)
            if isinstance(target, QListWidgetItem):
                _load_state(target, ("loading", 0))  # downloaded: decoding its first picture
            try:
                if isinstance(target, ArtView):
                    target.size()  # raises if the widget was replaced meanwhile
                    target.animated = True
                    target.set_motion(*self.motion_spec(asset, target.size(), crop=not target.checker))
                elif isinstance(target, QListWidgetItem):
                    # See all asks only for options in view, once it has stopped scrolling.
                    motion.first_frame(kind, path, deliver, wanted=lambda: self.tray.is_open())
                else:
                    motion.first_frame(kind, path, deliver)
            except RuntimeError:
                pass

        n = self._fetches.get(("file", url), workers.cached_file, url, self.sgdb.download, on_done=done,
                              on_error=lambda _m: self._gave_up(target, lambda: self._load_animated(asset, target)),
                              on_gone=lambda _m: _load_state(target, ("failed", lambda: self._load_animated(asset, target))),
                              on_retry=lambda n: _load_state(target, ("loading", n)))
        _load_state(target, ("loading", n))

    def motion_spec(self, asset: Asset | None, size, crop: bool = False) -> tuple:
        """(key, factory) for ArtView.set_motion to play an animated option, once it's downloaded.
        With `crop`, frames are made to measure (cut as fill_rect cuts), so they're drawn as they are."""
        if asset is None or not asset.animated:
            return None, None
        full = self.full_files.get(asset.id)  # the whole animation at full quality, when it's to hand
        src = ("frames", full) if full is not None else self.motion_files.get(asset.id)
        if src is None:
            return None, None
        kind, path = src
        bound = self._bound(size)
        how = "middle" if crop else False
        return ("asset", asset.id, kind), lambda: motion.make(kind, path, bound, crop=how)

    def _bound(self, size: QSize) -> QSize:
        """`size` in device pixels, rounded as fill_rect rounds, so frames made to it draw 1:1."""
        dpr = self.devicePixelRatioF()
        return QSize(round(size.width() * dpr), round(size.height() * dpr))

    def full_motion_spec(self, asset: Asset | None, size, budget: int | None = None, **how) -> tuple:
        """Like motion_spec, but playing the option's full file at `size` rather than its small
        preview video: for big places such as the page's banner. The preview plays until the
        file is downloaded."""
        if asset is None or not asset.animated:
            return None, None
        path = self.full_files.get(asset.id)
        if path is None:
            if asset.id not in self._full_loading:
                self._full_loading.add(asset.id)

                def done(p, aid=asset.id):
                    self._full_loading.discard(aid)
                    self.full_files[aid] = p
                    self.refresh_soon()

                self._fetches.get(("file", asset.url), workers.cached_file, asset.url, self.sgdb.download,
                                  on_done=done, on_error=lambda _m, aid=asset.id: self._full_loading.discard(aid))
            return self.motion_spec(asset, size, crop=bool(how.get("crop")))
        bound = self._bound(size)
        return ("full", asset.id), lambda: motion.make("frames", path, bound, budget, **how)

    def file_motion_spec(self, path: Path | None, size, budget: int | None = None, **how) -> tuple:
        """(key, factory) to play an animated artwork file from Steam's folder; nothing for a still."""
        if path is None or not self.show_animated:
            return None, None
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return None, None
        if key not in self._anim_files:
            self._anim_files[key] = artwork.file_is_animated(path)
        if not self._anim_files[key]:
            return None, None
        bound = self._bound(size)
        return ("file",) + key, lambda: motion.make("frames", str(path), bound, budget, **how)

    # ---- Steam's own store artwork ---------------------------------------------

    @property
    def steam_art(self) -> bool:
        return self.config.data.get("steam_art", True)

    def _set_steam_art(self, on: bool):
        """Offer Steam's official artwork (and prefer it in Get Artwork), or leave Steam's store alone."""
        self.config.data["steam_art"] = on
        self.config.save()
        if self.current_shortcut() is not None and self.stage.currentWidget() is self.page:
            self.load_steam()

    def steam_option(self, slot_key: str) -> SteamAsset | None:
        """The open game's official piece from Steam, once looked up."""
        sc = self.current_shortcut()
        if sc is None or not self.steam_art:
            return None
        found = self.steam_found.get(sc.appid)
        return found[1].get(slot_key) if found else None

    def _steam_lookup(self, name: str, saved: dict | None):
        """Worker: the Steam store game for a shortcut (the user's pick, else a same-name match)
        and its official pieces."""
        if saved is not None:
            game = StoreGame(saved["id"], saved.get("name", "")) if saved.get("id") else None
        elif generic_name(name):
            game = None  # "Launcher", "Play": an exact store match would be some other game
        else:
            game = self.steam.match(name)
        return game, (self.steam.assets(game.appid) if game else {})

    def load_steam(self):
        """Find the open game on the Steam store and put its official artwork first in each row."""
        sc = self.current_shortcut()
        if sc is None:
            return
        if not self.steam_art:
            self._show_steam(sc, (None, {}), "off")
            return
        found = self.steam_found.get(sc.appid)
        if found is not None:
            self._show_steam(sc, found)
            return
        self._show_steam(sc, (None, {}), "finding")
        appid = sc.appid

        def here() -> Shortcut | None:
            cur = self.current_shortcut()
            ok = cur is not None and cur.appid == appid and self.stage.currentWidget() is self.page
            return cur if ok else None

        saved = self.config.get_steam_match(appid)

        def done(result):
            if self.config.get_steam_match(appid) != saved:
                return  # the Steam game was changed meanwhile: this answer is for the old one
            self.steam_found[appid] = result
            if here() is not None:
                self._show_steam(here(), result)

        def failed(_msg):
            if here() is not None:
                self._show_steam(here(), (None, {}), "error")

        workers.run(self._steam_lookup, search_name(sc.name, sc.exe), saved, on_done=done, on_error=failed)

    def _show_steam(self, sc: Shortcut, found, state: str = ""):
        game, assets = found
        for key, row in self.page.rows.items():
            a = assets.get(key)
            if state == "finding":
                row.wait_for_steam()
            else:
                row.set_steam([a] if a else [])
        self.page.card.set_steam(state or ("found" if game else "none"), game.name if game else "")
        self.page._card_cover()
        if self.tray.is_open():
            self.tray.open_slot(self.tray.slot)

    def _steam_now(self, sc: Shortcut):
        """For the game picker: the Steam store game in use, MatchDialog.NOT_ON_STEAM, or None if unknown."""
        found = self.steam_found.get(sc.appid)
        if found is not None and found[0] is not None:
            return found[0]
        saved = self.config.get_steam_match(sc.appid)
        return MatchDialog.NOT_ON_STEAM if saved is not None and not saved.get("id") else None

    def _set_steam_game(self, sc: Shortcut, choice):
        """Use Steam store game `choice` (or MatchDialog.NOT_ON_STEAM) for a shortcut's official art."""
        if choice is MatchDialog.NOT_ON_STEAM:
            self.config.set_steam_match(sc.appid, 0)
        else:
            self.config.set_steam_match(sc.appid, choice.appid, choice.name)
        for key, pick in self.pending.for_game(sc.appid).items():
            if isinstance(pick.asset, SteamAsset):  # the other game's art no longer fits
                self.pending.drop(sc.appid, key)
        self.steam_found.pop(sc.appid, None)

    def fetch_assets(self, game_id: int, slot_key: str, page: int, callback):
        animated = self.show_animated
        key = (game_id, slot_key, page, animated)
        if key in self.asset_cache:
            callback(self.asset_cache[key])
            return

        def done(assets):
            self.asset_cache[key] = assets
            self.set_connection("connected")
            callback(assets)

        def failed(msg):
            self._network_problem(msg)
            callback(None)

        workers.run(self.sgdb.assets, slot_key, game_id, page, animated, on_done=done, on_error=failed)

    # ---- waiting changes ----------------------------------------------------

    def pick_pixmap(self, sc: Shortcut, slot_key: str) -> QPixmap | None:
        pick = self.pending.get(sc.appid, slot_key)
        if pick is None or pick.clears:
            return None
        key = (sc.appid, slot_key)
        pm = self.pick_pix.get(key)
        if pm is None:
            self._load_pick_pix(key, pick)  # let go to save memory: back shortly
        return pm

    def _stage(self, sc: Shortcut, slot_key: str, pick: Pick, pm: QPixmap | None = None, rename: bool = True):
        self.pending.set(sc.appid, slot_key, pick)
        if rename:
            self._auto_rename(sc)
        key = (sc.appid, slot_key)
        self.pick_pix.pop(key, None)
        if pick.clears:
            return
        if pm is not None:
            self.pick_pix[key] = pm
            return
        self._load_pick_pix(key, pick)

    def _load_pick_pix(self, key, pick: Pick):
        """Fill pick_pix[key] off the window's thread: decoded from the pick's file or bytes,
        else from its option's thumbnail. Also used when the cache let it go."""
        if key in self._pick_loading:
            return
        self._pick_loading.add(key)
        if pick.fetched:
            def decoded(img, key=key, pick=pick):
                self._pick_loading.discard(key)
                if self.pending.get(*key) is pick and not img.isNull():
                    self.pick_pix[key] = QPixmap.fromImage(img)
                    self.refresh_soon()

            workers.run(decode_pick, pick.source, key[1], kind="cpu", on_done=decoded,
                        on_error=lambda _m, key=key: self._pick_loading.discard(key))
            return

        def loaded(pm, key=key, asset=pick.asset, pick=pick):
            self._pick_loading.discard(key)
            cur = self.pending.get(*key)
            if cur is pick and pick.fetched:  # the full file came first: that's shown instead
                if key not in self.pick_pix:
                    self._load_pick_pix(key, pick)
                return
            full = tray_full_pixmap(asset.id)  # sharper than the thumbnail, if See all loaded it
            if full is not None:
                pm = full
            if cur is not None and cur.asset is asset:
                self.pick_pix[key] = pm
                self.refresh_soon()

        self.load_thumb(pick.asset, loaded)  # stands in until the full file is here
        self._fetch_pick(key, pick)

    def _fetch_pick(self, key, pick: Pick):
        """Download a clicked option's full file into the cache (at once if it's there already,
        say from See all), then show that: the thumbnail is a small, softer copy. Apply uses it."""
        url = pick.asset.url
        if not url.startswith(("http://", "https://")):
            return

        def got(path, key=key, pick=pick):
            if self.pending.get(*key) is pick and not pick.fetched:
                pick.cached = path
                self._pick_loading.discard(key)  # a thumbnail still on its way is no longer wanted
                self._load_pick_pix(key, pick)

        self._fetches.get(("file", url), workers.cached_file, url, self.sgdb.download, on_done=got,
                          on_error=lambda _m: None)  # Apply downloads it then, as before

    def stage_pick(self, slot: Slot, asset: Asset):
        sc = self.current_shortcut()
        if not sc or self.busy == "apply":
            return
        self._stage(sc, slot.key, Pick(asset))
        self.refresh_all()

    def stage_file(self, slot: Slot, path: str | None = None, image=None):
        """Pick a picture from the user's files (or a pasted one) for a piece of the open game.

        It's read and, if Steam couldn't use it as it is, converted off the GUI thread; then it
        waits for Apply like any other pick. A picture far off for its piece still goes in, with a word.
        """
        sc = self.current_shortcut()
        if not sc or self.busy == "apply":
            return
        if path:
            self.config.data["last_image_folder"] = str(Path(path).parent)
            self.config.save()

        def done(loaded):
            if self.busy == "apply" or self.sfile is None:
                return
            game = next((s for s in self.sfile.shortcuts if s.appid == sc.appid), None)
            if game is None:
                return  # the game is gone
            asset = loaded.pick.asset
            if asset.animated and self.show_animated:
                self.motion_files[asset.id] = ("frames", asset.path)
            self._stage(game, slot.key, loaded.pick, QPixmap.fromImage(loaded.preview))
            self.refresh_all()
            cur = self.current_shortcut()
            if cur is None or cur.appid != sc.appid:  # you moved on while it converted: it still went in
                self.toast.show_message(f"Your {slot.label.lower()} for {game.name or 'the game'} is ready to apply.")
            elif loaded.note:
                self.toast.show_message(loaded.note)

        fn, arg = (localfile.load_path, path) if path else (localfile.load_image, image)
        workers.run(fn, slot, arg, on_done=done, on_error=lambda msg: self.toast.show_message(msg))

    def stage_clear(self, slot: Slot):
        sc = self.current_shortcut()
        if not sc or self.busy == "apply":
            return
        if self.slot_path(sc, slot, fresh=True) is None:
            self.pending.drop(sc.appid, slot.key)  # nothing on disk: just drop the waiting pick
        else:
            self._stage(sc, slot.key, Pick(None))
        self.refresh_all()

    def unstage(self, slot: Slot):
        sc = self.current_shortcut()
        if sc and self.busy != "apply":
            self.pending.drop(sc.appid, slot.key)
            self.refresh_all()

    def shown_name(self, sc: Shortcut) -> str:
        """The game's title as it will be once applied: a waiting new title, else Steam's."""
        return self.pending.name(sc.appid) or sc.name or "(unnamed)"

    def rename_suggestion(self, sc: Shortcut | None) -> str | None:
        """For a game stuck with a title like "game.exe", the name of the SteamGridDB game it
        is matched to, once that match is settled; None when there's nothing to suggest."""
        if sc is None or not generic_name(sc.name, sc.exe) or self.status.get(sc.appid) == "check":
            return None
        if str(sc.appid) in self.pending_names():
            return None  # already renamed, waiting for Steam to close
        m = self.config.get_match(sc.appid)
        name = clean_name(m["name"]) if m else ""
        return name if name and name != sc.name else None

    def keeps_name(self, appid: int) -> bool:
        """The user turned the suggested new title down for this game: don't offer it by default."""
        return str(appid) in self.config.data.get("keep_names", [])

    def _set_keeps_name(self, appid: int, keep: bool):
        names = [a for a in self.config.data.get("keep_names", []) if a != str(appid)]
        self.config.data["keep_names"] = names + [str(appid)] if keep else names
        self.config.save()

    def rename_due(self, sc: Shortcut) -> str | None:
        """The new title Get Artwork and Apply take along by default, if it isn't waiting already."""
        if self.pending.name(sc.appid) is not None or self.keeps_name(sc.appid):
            return None
        return self.rename_suggestion(sc)

    def _auto_rename(self, sc: Shortcut) -> bool:
        """Stage the suggested title along with the game's artwork, unless it was turned down."""
        name = self.rename_due(sc)
        if name:
            self.pending.set_name(sc.appid, name)
        return bool(name)

    def stage_rename(self):
        sc = self.current_shortcut()
        name = self.rename_suggestion(sc)
        if name and self.busy != "apply":
            self.pending.set_name(sc.appid, name)
            self._set_keeps_name(sc.appid, False)
            self.page.update_match()
            self.refresh_all()

    def unstage_rename(self):
        sc = self.current_shortcut()
        if sc and self.busy != "apply":
            self.pending.set_name(sc.appid, None)
            self._set_keeps_name(sc.appid, True)  # so Get Artwork and Apply leave the title alone
            self.page.update_match()
            self.refresh_all()

    def _rename(self, jobs, defer: bool, sfile):
        """Retitle each (shortcut, new name) in `sfile`; with Steam open, queue it for when
        Steam closes instead (Steam writes its own list back on exit). Returns the undo
        records (appid, old name, new name, queued) and errors."""
        done, errors = [], []
        for sc, name in jobs:
            old = sc.name
            try:
                if defer:
                    self.config.set_pending_name(sfile.path, sc.appid, name)
                elif artwork.set_name(sfile, sc, name):
                    self._after_reload(sc.appid)
            except Exception as e:
                errors.append(f"Renaming {old}: {e}")
                continue
            self.pending.set_name(sc.appid, None)
            done.append((sc.appid, old, name, defer))
        if any(not queued for *_, queued in done):
            cur = self.current_shortcut()
            self._after_reload(cur.appid if cur else -1)  # new titles: the lists show and sort by them
        return done, errors

    def _unstaged_missing(self, sc: Shortcut, recheck: bool = False) -> list[Slot]:
        """The pieces a game has no artwork for, nor a waiting pick. Pieces SteamGridDB had
        nothing for lately are left out (the game has all there is), unless `recheck`."""
        none = set() if recheck else self.unavailable(sc)
        return [SLOTS[k] for k, have in zip(ORDER, self.coverage(sc))
                if not have and self.pending.get(sc.appid, k) is None and k not in none]

    # ---- Steam & shortcuts -------------------------------------------------

    @property
    def steam_root(self) -> Path | None:
        return self.user.userdata.parent.parent if self.user else None

    def detect_steam(self, roots: list[Path] | None = None):
        if roots is None:
            roots = find_steam_roots()
            saved = self.config.data.get("steam_folder")
            if saved:  # the folder chosen last time comes first
                roots = find_steam_roots([Path(saved)]) + [r for r in roots if r != Path(saved)]
        self.users = [u for r in roots for u in list_users(r)]
        self.forget_files()
        if not self.users:
            self.user = None
            self.sfile = None
            self.current = -1
            self.header.set_users([], -1, None)
            self.game_list.set_empty_reason("nosteam")
            self.stage.setCurrentWidget(self.nosteam)
            self.update_chrome()
            self._sync_folder_popover()
            return
        self._account_changed(0)

    def _wait_for_busy(self) -> bool:
        """True (after saying so) while Apply, Get Artwork or a Steam restart is running."""
        if not self.busy:
            return False
        self.toast.show_message({
            "apply": "Wait until the artwork has been applied.",
            "fetch": "Wait until Get Artwork has finished.",
            "restart": "Wait until Steam has closed.",
        }.get(self.busy, "Wait a moment, then try again."))
        return True

    def show_folder_popover(self):
        if self.folderpop.is_open():
            return self.folderpop.dismiss()
        if self._wait_for_busy():
            return
        self.popover.dismiss()
        self.keypop.dismiss()
        self._sync_folder_popover()
        self.folderpop.open_for(self.statusbar.steam_anchor())

    def _sync_folder_popover(self):
        idx = self.users.index(self.user) if self.user in self.users else -1
        self.folderpop.set_current(self.steam_root, [u.label for u in self.users], idx, self._avatar())

    def use_steam_root(self, root: Path):
        """Switch to another Steam folder (from the pop-over), remembering it for next time."""
        if root == self.steam_root or self._wait_for_busy():
            return
        if self.pending and not self._ask_discard():
            return
        self.pending.clear()
        self.config.data["steam_folder"] = str(root)
        self.config.save()
        self.detect_steam([root])

    def choose_steam_folder(self):
        if self._wait_for_busy():
            return
        d = QFileDialog.getExistingDirectory(self, "Choose your Steam folder", str(Path.home()))
        if d:
            roots = find_steam_roots([Path(d)])
            if not roots:
                tell(self, APP_NAME, "That folder doesn't have a Steam “userdata” folder inside it.")
                return
            self.use_steam_root(roots[0])

    def _account_changed(self, idx: int):
        if not 0 <= idx < len(self.users):
            return
        if self.user is not self.users[idx] and self._wait_for_busy():
            if self.user in self.users:
                self.header.set_users([u.label for u in self.users], self.users.index(self.user), self._avatar())
            self._sync_folder_popover()
            return
        if self.pending and self.user is not self.users[idx]:
            if not self._ask_discard():
                self.header.set_users([u.label for u in self.users], self.users.index(self.user), self._avatar())
                return
        self.user = self.users[idx]
        self.current = -1  # another account's games: start from its library
        self.header.set_users([u.label for u in self.users], idx, self._avatar())
        self._sync_folder_popover()
        self.reload_shortcuts()

    def _avatar(self) -> QPixmap | None:
        p = avatar_path(self.steam_root, self.user.account_id) if self.user else None
        pm = load_pixmap(p) if p else None
        return pm if pm is not None and not pm.isNull() else None

    def _ask_discard(self) -> bool:
        n = len(self.pending.games())
        return bool(run_dialog(ConfirmDialog(
            self, "Discard the artwork you haven't applied?",
            f"New artwork for {plural(n, 'game')} hasn't been applied yet.", "Discard", "trash", bad=True),
            delete=True))

    def reload_game_list(self):
        """Reload Game List (menu, Ctrl+R, F5): re-read Steam's list and say what changed."""
        if self.busy or not self.user:
            return self.reload_shortcuts()
        before = {sc.appid for sc in self.sfile.shortcuts} if self.sfile else set()
        self.reload_shortcuts()
        if not self.sfile:
            return
        after = {sc.appid for sc in self.sfile.shortcuts}
        parts = [plural(len(after), "game")]
        if after - before:
            parts.append(f"{len(after - before)} new")
        if before - after:
            parts.append(f"{len(before - after)} removed")
        self.show_note("done", "Game list reloaded · " + ", ".join(parts))

    def show_note(self, kind: str, text: str, hold: int | None = 5000):
        """A status bar message that fades out after `hold` ms (None: stays until the next
        piece of work starts), unless work is running."""
        self._note = (kind, text)
        if hold:
            self._note_timer.start(hold)
        else:
            self._note_timer.stop()
        self._show_status()

    def _clear_note(self):
        self._note = None
        if self.busy or self._matching:
            self._show_status()
        else:
            self.statusbar.fade_message()

    def _show_status(self):
        """The left of the status bar: running work, then the startup match, then a short note."""
        done, total = self._match_count
        if self.busy:
            self.statusbar.set_message("busy", self.progress_text)
        elif self._matching and total:
            self.statusbar.set_message("busy", f"Matching games on SteamGridDB · {done} of {total}")
        elif self._note:
            self.statusbar.set_message(*self._note)
        else:
            self.statusbar.set_message(None, "")

    def reload_shortcuts(self):
        if self._wait_for_busy():
            return
        if not self.user:
            return self.detect_steam()
        from ..shortcuts import ShortcutsFile

        keep = self.current_shortcut()
        keep = keep.appid if keep is not None else None  # stay on the same game, wherever it now sits
        self.forget_files()
        try:
            self.sfile = ShortcutsFile(self.user.shortcuts_path)
        except Exception as e:
            tell(
                self, APP_NAME,
                f"Steam's shortcut list couldn't be read:\n{e}\n\nYou can restore a backup from the ⋯ menu.",
                QMessageBox.Icon.Critical)
            self.sfile = None
        known = {sc.appid for sc in self.sfile.shortcuts} if self.sfile else set()
        for appid in list(self.pending.games()):
            if appid not in known:
                self.pending.drop(appid)
        if not self.sfile or not self.sfile.shortcuts:
            self.current = -1
            self.game_list.set_empty_reason("nogames")
            self.stage.setCurrentWidget(self.nogames)
            self.update_steam_state()
            self.update_chrome()
            return
        self.game_list.set_empty_reason(None)
        self.current = next((i for i, sc in enumerate(self.sfile.shortcuts) if sc.appid == keep), -1)
        self.game_list.rebuild()
        self.start.rebuild()
        self.show_index(self.current)
        workers.run(prune_replaced, self.user.grid_dir)
        if not getattr(self, "_cache_pruned", False):
            self._cache_pruned = True
            workers.run(workers.prune_cache, kind="long")
        self.match_all()
        self.update_steam_state()

    def _after_reload(self, appid: int):
        """The shortcut list was re-read from disk mid-change: rebuild what indexes into it."""
        self.current = next((i for i, s in enumerate(self.sfile.shortcuts) if s.appid == appid), -1) \
            if self.current >= 0 else -1
        self.forget_files()  # icon fields may have changed with it
        self.game_list.rebuild()
        self.start.rebuild()

    # ---- Steam itself ---------------------------------------------------------

    def steam_is_running(self) -> bool:
        return bool(self.user and steam_running(self.steam_root))

    def pending_icons(self) -> dict:
        """The current account's icons waiting for Steam to close."""
        return self.config.pending_icons(self.user.shortcuts_path) if self.user else {}

    def pending_names(self) -> dict:
        """The current account's new titles waiting for Steam to close."""
        return self.config.pending_names(self.user.shortcuts_path) if self.user else {}

    def _poll_steam(self):
        """The 5 s tick: check Steam in a worker (inside a Flatpak it asks the host,
        which can take seconds), then update the status bar with that one answer."""
        if self._steam_poll:
            return
        if not self.user:
            return self.update_steam_state(False)
        self._steam_poll = True
        root = self.steam_root

        def done(running):
            self._steam_poll = False
            if self.steam_root == root:
                self.update_steam_state(bool(running))

        def failed(_msg):
            self._steam_poll = False

        workers.run(steam_running, root, on_done=done, on_error=failed)

    @property
    def auto_restart(self) -> bool:
        return self.config.data.get("auto_restart_steam", True)

    @property
    def apply_on_quit(self) -> bool:
        return self.config.data.get("apply_on_quit", False)

    def _set_apply_on_quit(self, on: bool):
        self.config.data["apply_on_quit"] = on
        self.config.save()

    def _ask_on_quit(self) -> str | None:
        """"apply", "discard", or None (cancel), from the quit prompt."""
        games = []
        dpr = self.devicePixelRatioF()
        for appid in self.pending.games():
            sc = self.sfile.find(appid) if self.sfile else None
            if sc is None:
                continue
            picks = self.pending.for_game(appid)
            ordered = sorted(picks.items(), key=lambda kp: ORDER.index(kp[0]))
            names = [SLOTS[k].label.lower() for k, _ in ordered]
            clears = {p.clears for _, p in ordered}
            if not ordered:
                what = ""
            elif clears == {False}:
                what = "new " + _join(names)
            elif clears == {True}:
                what = _join(names) + " removed"
            else:
                what = _join([f"{n} removed" if p.clears else f"new {n}" for n, (_, p) in zip(names, ordered)])
            new_name = self.pending.name(appid)
            if new_name:
                what = (what + ", " if what else "") + f"renamed to “{new_name}”"
            cover = None
            if len(games) < QuitDialog.SHOWN:  # only the first few are shown: decode just those, small
                grid = picks.get("grid")
                if grid is not None and not grid.clears:
                    cover = self.pick_pixmap(sc, "grid")
                    if cover is None and grid.fetched:
                        cover = QPixmap.fromImage(decode_pick(grid.source, "grid"))
                elif grid is None:
                    path = self.cover_path(sc)
                    if path:
                        cover = QPixmap.fromImage(decode_scaled(str(path), round(30 * dpr), round(45 * dpr)))
                        cover.setDevicePixelRatio(dpr)
            games.append((sc.name or "(unnamed)", what[:1].upper() + what[1:], self.pending.count(appid), cover))
        dlg = QuitDialog(self, games)
        try:
            if not run_dialog(dlg):
                return None
            if dlg.choice == "apply" and dlg.remember.isChecked():
                self._set_apply_on_quit(True)
                self.popover.set_apply_on_quit(True)
            return dlg.choice
        finally:
            dlg.deleteLater()

    def _set_auto_restart(self, on: bool):
        self.config.data["auto_restart_steam"] = on
        self.config.save()

    def steam_state(self, running: bool | None = None) -> str:
        if running is None:
            running = self.steam_is_running()
        if running and (self.restart_needed or self.pending_icons() or self.pending_names()):
            return "restart"
        return "running" if running else "closed"

    def update_steam_state(self, running: bool | None = None):
        """`running` is an answer already in hand (from the tick's worker); None checks now."""
        if running is None:
            running = self.steam_is_running()
        if not running:
            self.restart_needed = False  # Steam loads the new artwork when it next starts
            if not self.busy:  # Apply and Restart link them themselves when they finish
                self.apply_pending_icons(running=False)
        self.statusbar.set_process(self.steam_state(running))

    def show_steam_popover(self):
        if self.popover.is_open():
            return self.popover.dismiss()
        self.keypop.dismiss()
        self.folderpop.dismiss()
        self.popover.set_auto_restart(self.auto_restart)
        self.popover.show_for(self.steam_state(), self.statusbar.process_anchor(),
                             len(self.pending_icons()) + len(self.pending_names()))

    def restart_steam_now(self):
        root = self.steam_root
        if not root or self.busy:
            return
        self.popover.dismiss()
        self._set_busy("restart", "Restarting Steam…")

        def work():
            if steam_running(root):
                shutdown_steam(root)
                if not wait_until_closed(root):
                    return False
            return True

        def done(closed):
            self._set_busy("")
            if not closed:
                self.toast.show_message("Steam didn't close. Close it yourself, then open it again.")
                return
            self.apply_pending_icons()
            self.restart_needed = False
            start_steam(root)
            self.update_steam_state()
            self.toast.show_message("Steam restarted with the new artwork.")

        workers.run(work, on_done=done, on_error=lambda m: (self._set_busy(""), self.toast.show_message(m)))

    def apply_pending_icons(self, running: bool | None = None):
        """Link icons that were saved while Steam was running, now that it has closed."""
        if not self.sfile or not self.user:
            return
        spath = self.user.shortcuts_path
        pending = self.config.pending_icons(spath)
        names = self.config.pending_names(spath)
        if not (pending or names) or self.sfile.path != spath:
            return
        if self.steam_is_running() if running is None else running:
            return
        cur = self.current_shortcut()
        linked, reloaded = 0, False
        for key, path in pending.items():
            sc = self.sfile.find(int(key))
            if sc is not None:
                self.forget_files(sc.appid)
                try:
                    if artwork.set_icon(self.sfile, sc, path):
                        reloaded = True  # Steam rewrote the list on exit: indexes may have moved
                    linked += 1
                except Exception as e:
                    reloaded = True
                    if self.sfile.find(int(key)) is not None:
                        self.toast.show_message(f"Couldn't link the icon for {sc.name}: {e}")
                        continue
            self.config.set_pending_icon(spath, int(key), None)  # linked, or the game is gone from this list
        for key, name in names.items():
            sc = self.sfile.find(int(key))
            if sc is not None:
                try:
                    if artwork.set_name(self.sfile, sc, name):
                        reloaded = True
                    linked += 1
                except Exception as e:
                    reloaded = True
                    if self.sfile.find(int(key)) is not None:
                        self.toast.show_message(f"Couldn't rename {sc.name}: {e}")
                        continue
            self.config.set_pending_name(spath, int(key), None)
        if names:
            reloaded = True  # titles changed: the lists show (and sort by) them
        if reloaded:
            self._after_reload(cur.appid if cur else -1)
        if linked or reloaded:
            self.refresh_all()

    # ---- SteamGridDB connection ---------------------------------------------

    def set_connection(self, state: str):
        if not self.config.api_key:
            state = "disconnected"
        self.conn = state
        self.statusbar.set_connection("nokey" if not self.config.api_key else state)
        if state == "connected":
            self._connection_back()
            QTimer.singleShot(0, self.match_all)

    def match_all(self):
        """Look every unmatched game up on SteamGridDB in the background, so best
        guesses show "Check Match" in the list without opening each game first."""
        if self._matching or self.conn != "connected" or not self.sfile:
            return
        todo = [(sc.appid, search_name(sc.name, sc.exe)) for sc in self.sfile.shortcuts
                if sc.name and not self.config.get_match(sc.appid)
                and sc.appid not in self.guesses and sc.appid not in self.status]
        if not todo:
            return self.check_gaps()
        self._matching = True
        self._match_count = (0, len(todo))
        self._show_status()
        sfile = self.sfile

        def work(progress):
            for appid, name in todo:
                try:
                    game, n = self.sgdb.match(name)
                except AuthError:
                    raise  # a revoked key: stop now rather than fail every game
                except Exception:
                    game, n = None, None  # tried again next start, or when the game is opened
                progress((appid, game, n))

        def got(result):
            appid, game, n = result
            done, total = self._match_count
            self._match_count = (done + 1, total)
            self._show_status()
            if n is None or self.sfile is not sfile or self.config.get_match(appid) or appid in self.guesses:
                return
            sc = next((g for g in sfile.shortcuts if g.appid == appid), None)
            if sc is None or appid in self.status:
                return
            self._record_match(sc, game, n)
            if sc is self.current_shortcut() and self.stage.currentWidget() is self.page:
                self.page.update_match()
            self.refresh_soon()

        def finished(*_):
            self._matching = False
            self._match_count = (0, 0)
            self._show_status()
            if self.sfile is not sfile:  # another account loaded meanwhile: match its games too
                QTimer.singleShot(0, self.match_all)
            else:
                self.check_gaps()

        def failed(msg):
            self._matching = False
            self._match_count = (0, 0)
            self._show_status()
            self._key_problem(msg)

        workers.run(work, kind="long", on_progress=got, on_done=finished, on_error=failed)

    def check_gaps(self):
        """Ask SteamGridDB, once a session, whether matched games' missing pieces exist at all,
        so a game that has everything there is stops counting as missing artwork."""
        if self._matching or self.conn != "connected" or not self.sfile:
            return
        todo = []
        for sc in self.sfile.shortcuts:
            m = self.config.get_match(sc.appid)
            if not m or sc.appid in self._gaps_checked:
                continue
            self._gaps_checked.add(sc.appid)
            todo += [(sc.appid, sc.exe, m["id"], s.key) for s in self._unstaged_missing(sc)]
        if not todo:
            return

        def work(progress):
            for appid, exe, game_id, key in todo:
                try:
                    none = not self.sgdb.assets(key, game_id)
                except AuthError:
                    raise
                except Exception:
                    continue  # asked again next start
                # No icon on SteamGridDB: read the game's own here, off the window's thread.
                exe_icon = _exe_icon_png(appid, exe) if none and key == "icon" else None
                progress((appid, exe, key, none, exe_icon))

        def got(result):
            appid, exe, key, none, exe_icon = result
            self.config.set_no_art(appid, key, none)
            sc = next((g for g in self.sfile.shortcuts if g.appid == appid), None) if self.sfile else None
            if none and key == "icon" and sc is not None and sc.exe == exe:
                self._exe_icons[(appid, exe)] = exe_icon
                self.stage_exe_icon(sc)  # the game's own icon, ready for Apply
            self.refresh_soon()

        workers.run(work, on_progress=got, on_done=lambda *_: None,
                    on_error=lambda msg: self._key_problem(msg))

    def check_connection(self):
        if not self.config.api_key:
            return self.set_connection("disconnected")
        self.set_connection("connecting")
        workers.run(self.sgdb.check_key,
                    on_done=lambda ok: self.set_connection("connected" if ok else "disconnected"),
                    on_error=lambda _m: self.set_connection("disconnected"))

    def _network_problem(self, msg: str):
        self.set_connection("disconnected")
        self.toast.show_message(msg)

    # ---- the bars around the pages ----------------------------------------------

    def _focus_page(self):
        """Focus the page under the content bar (the cover wall on the library)."""
        page = self.stage.currentWidget()
        (self.start.wall if page is self.start else page).setFocus(Qt.FocusReason.OtherFocusReason)

    def update_chrome(self):
        """The content bar's title and buttons, and the status bar's messages."""
        self.statusbar.set_steam(bool(self.users), str(self.steam_root).replace(str(self.home), "~")
                                 if self.steam_root else "")
        games = self.sfile.shortcuts if self.sfile else []
        sc = self.current_shortcut()
        on_page = sc is not None and self.stage.currentWidget() is self.page
        self.cbar.setVisible(not on_page)  # the game page's banner header has its own title and buttons
        if on_page:
            self.page.card.set_game(self.shown_name(sc))
            self.page.card.set_actions(bool(self._unstaged_missing(sc)), self.pending.count(sc.appid), bool(self.busy))
            self._show_fill(self.page.card.apply_btn, sc.appid)
        else:
            gl = self.game_list
            self.cbar.show_library(len(games) if self.users else None,
                                   sum(map(gl.matches, games)) if gl.narrowed() else None)
            # Get Artwork also takes along suggested titles, but the button counts and names what
            # each game gets, so a game that only needs a new name never reads as missing artwork.
            art = [g for g in games if self._unstaged_missing(g) and self.status.get(g.appid) != "notfound"]
            names = [g for g in games if g not in art and self.rename_due(g)]
            ready = len(self.pending.games())
            fx = self._apply_fx
            specs = []
            if games and (art or names or not ready):
                if art:
                    text = f"Get Artwork ({plural(len(art), 'Game')})"
                elif names:
                    text = f"Get Names ({plural(len(names), 'Game')})"
                else:
                    text = "Get Artwork"
                specs.append((text, "ghost" if ready or fx else "go", bool(art or names) and not self.busy,
                              self.fill_everything))
            if fx:
                specs.append((fx["text"], "go", True, self.apply_all))  # looks live while it fills
            elif ready:
                specs.append((f"Apply ({plural(ready, 'Game')})", "go", not self.busy, self.apply_all))
            buttons = self.cbar.set_buttons(specs)
            if specs and specs[0][3] == self.fill_everything:
                buttons[0].setToolTip(self._get_tip(art, names))
            for b in buttons:
                if getattr(b, "_cb", None) == self.apply_all:
                    self._show_fill(b, None)
        self._show_status()

    def _show_fill(self, button, appid: int | None):
        """Fill an Apply button as the writes go, while `_apply_fx` covers it (on a game page, only
        for a game being applied), and clear the fill otherwise."""
        fx = self._apply_fx
        if fx and (appid is None or appid in fx["games"]):
            button.setText(fx["text"])
            button.setEnabled(True)  # not dimmed mid-write; apply_games ignores clicks while busy
            ApplyFill.fill(button, fx["step"] / fx["total"])
        else:
            ApplyFill.fill(button, None)

    def _get_tip(self, art: list[Shortcut], names: list[Shortcut]) -> str:
        """The library Get button's tooltip: who gets artwork, and who only gets a new title."""
        def listing(games, line):
            shown = [line(g) for g in games[:20]]
            return "\n".join(shown) + (f"\n…and {len(games) - 20} more" if len(games) > 20 else "")
        parts = []
        if art:
            parts.append("Get artwork for:\n" + listing(art, lambda g: g.name or "(unnamed)"))
        if names:
            parts.append("Rename, from SteamGridDB:\n"
                         + listing(names, lambda g: f"{g.name or '(unnamed)'} → {self.rename_due(g)}"))
        return "\n\n".join(parts) or "Every game has all the artwork SteamGridDB has"

    def refresh_soon(self):
        """refresh_all within 100 ms, once however many changes arrive meanwhile."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def refresh_all(self):
        self._refresh_timer.stop()
        if not self.sfile or not self.sfile.shortcuts:
            self.update_chrome()
            return
        self.resort()
        self.game_list.update_counts()  # also refreshes what screen readers say
        self.game_list.refresh(spoken=False)
        self.start.refresh()
        if self.current >= 0:
            self.page.refresh_current()
            if self.tray.is_open():
                self.tray.mark_in_use()
        self.update_chrome()

    def _library_narrowed(self, *_):
        if self.sfile and self.sfile.shortcuts:
            self.start.refresh()
            self.update_chrome()

    # ---- navigation -------------------------------------------------------

    def set_library_view(self, view: str):
        """"list", "grid" or "details", remembered for next time; shows the library."""
        self.game_list.homebar.view.set_view(view)
        self.start.set_view(view)
        self.config.data["library_view"] = view
        self.config.save()
        self.go_home()

    def set_library_sort(self, mode: str):
        """The order the game list and cover wall show games in (a SORTS key), remembered."""
        self.game_list.homebar.sort.set_view(mode)
        self.library_sort = mode
        self.config.data["library_sort"] = mode
        self.config.save()
        if self.sfile and self.sfile.shortcuts:
            self.game_list.rebuild()
            self.start.rebuild()
            self.start.wall.scrollToTop()

    def resort(self):
        """Put the lists back in order if the artwork they sort by has changed."""
        if self.library_sort != "missing" or not self.sfile or not self.sfile.shortcuts:
            return
        want = sorted_indices(self, self.library_sort)
        if self.game_list.order() != want:
            self.game_list.rebuild()
        if self.start.order() != want:
            self.start.rebuild()

    def _apply_key(self):
        """Ctrl+Enter: Apply what's waiting, for the open game on its page, else for every game."""
        if self.busy:
            return
        sc = self.current_shortcut()
        if sc is not None and self.stage.currentWidget() is self.page:
            if self.pending.count(sc.appid):
                self.apply_games([sc.appid])
        elif self.pending.games():
            self.apply_all()

    def _back_key(self):
        """Alt+Left: from a game page back to the library."""
        if self.stage.currentWidget() is self.page:
            self.go_home()

    def go_home(self):
        if self.sfile and self.sfile.shortcuts:
            leaving = self.current if self.stage.currentWidget() is self.page else -1
            moving = self._moving()
            old = self.transition.snapshot() if moving else None
            src = self._page_cover_rect() if moving and leaving >= 0 else None
            cover = self.page.card.cover.grab() if src is not None else None
            self.game_list.select(-1)
            self.show_index(-1)
            if old is not None:
                # The cover shrinks back to its place on the wall, if it's on screen there.
                tile = self._tile_rect(leaving)
                self.transition.run(old, cover, src, tile, back=True,
                                    hide=lambda on: self._wall_away(leaving if on else -1))

    def open_game(self, idx: int):
        self.game_list.select(idx)
        self.show_index(idx)

    # ---- moving between pages -------------------------------------------

    def _moving(self) -> bool:
        """Whether a page change should be animated: only in a shown window, with nothing over it."""
        return self.isVisible() and not self.scrim.is_covering() and self.stage.currentWidget() in (self.start, self.page)

    def _picked_on_wall(self, idx: int):
        """A game opened from the library: from the cover wall its cover grows into the page."""
        if not self._moving():
            return self.open_game(idx)
        old = self.transition.snapshot()
        src = self._tile_rect(idx)
        cover = None
        if src is not None:
            dpr = old.devicePixelRatio()
            cover = old.copy(int(src.x() * dpr), int(src.y() * dpr), int(src.width() * dpr), int(src.height() * dpr))
            cover.setDevicePixelRatio(dpr)
        self.open_game(idx)
        self.transition.run(old, cover, src, self._page_cover_rect, hide=self._page_cover_away)

    def _wall_away(self, idx: int):
        """Leave a game's place on the wall empty while its cover flies back to it."""
        self.start.wall.delegate.away = idx
        self.start.wall.viewport().update()

    def _page_cover_away(self, away: bool):
        """Hide the game page's small cover while the wall's cover flies into its place."""
        c = self.page.card.cover
        if away:
            fx = QGraphicsOpacityEffect(c)
            fx.setOpacity(0.0)
            c.setGraphicsEffect(fx)
        else:
            c.setGraphicsEffect(None)

    def _picked_in_sidebar(self, idx: int):
        """The sidebar's list: a quick cross-fade to the game (or home), with no cover in flight."""
        if idx == self.current or not self._moving():
            return self.show_index(idx)
        old = self.transition.snapshot()
        game_to_game = self.stage.currentWidget() is self.page and idx >= 0
        self.show_index(idx)
        rise = None
        if game_to_game:
            self.stage.layout().activate()
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            card = self.page.card
            top = card.mapTo(self.stage, QPoint(0, card.height())).y()
            if 0 < top < self.stage.height():
                rise = (self.transition.snapshot(),
                        QRectF(0, top, self.stage.width(), self.stage.height() - top))
        self.transition.run(old, rise=rise)

    def _page_cover_rect(self) -> QRectF | None:
        """Where the game page's small cover is, in the stage's coordinates."""
        c = self.page.card.cover
        if self.stage.currentWidget() is not self.page or not c.isVisible():
            return None
        return QRectF(QPointF(c.mapTo(self.stage, QPoint(0, 0))), QSizeF(c.size()))

    def _tile_rect(self, idx: int) -> QRectF | None:
        """Where a game's cover sits on the cover wall, in the stage's coordinates, if it shows."""
        wall = self.start.wall
        if idx < 0 or wall.view != "grid" or self.stage.currentWidget() is not self.start:
            return None
        for row in range(wall.count()):
            item = wall.item(row)
            if item.data(ROLE_INDEX) == idx:
                break
        else:
            return None
        if item.isHidden():
            return None
        d = wall.delegate
        cell = wall.visualItemRect(item)
        r = QRect(d.tile_x(cell), cell.y() + d.TOP, d.COVER.width(), d.COVER.height())
        if not wall.viewport().rect().contains(r.center()):
            return None
        return QRectF(QPointF(wall.viewport().mapTo(self.stage, r.topLeft())), QSizeF(r.size()))

    def show_index(self, idx: int):
        self.current = idx
        self.game_list.homebar.home.set_on(idx < 0)
        if not self.sfile or not self.sfile.shortcuts:
            return
        if 0 <= idx < len(self.sfile.shortcuts):
            self.forget_files(self.sfile.shortcuts[idx].appid)  # the page shows what's on disk now
        if idx < 0:
            self.tray.close_now()
            self.stage.setCurrentWidget(self.start)
            self.start.refresh()
            self.update_chrome()
            return
        self.stage.setCurrentWidget(self.page)
        self.page.scroll.verticalScrollBar().setValue(0)
        self.page.show_game()
        self.load_steam()
        self.load_options()
        if self.tray.is_open():
            self.tray.open_slot(self.tray.slot)
        self.update_chrome()

    def load_options(self):
        sc = self.current_shortcut()
        if not sc:
            return
        if not self.config.api_key:
            self.page.show_key_prompt()
            return
        game = self.current_game()
        if game:
            self._load_rows(sc, game)
            return
        if self.status.get(sc.appid) == "notfound":
            self.page.set_all_messages("Not found on SteamGridDB.")
            return
        self.page.set_all_messages("Finding this game on SteamGridDB…")
        appid = sc.appid

        def matched(result):
            game, n = result
            self._record_match(sc, game, n)
            if self.current_shortcut() is not None and self.current_shortcut().appid == appid:
                self.page.update_match()
                self.load_options()
            self.refresh_all()

        workers.run(self.sgdb.match, search_name(sc.name, sc.exe), on_done=matched,
                    on_error=lambda m: (self.page.set_all_messages(m), self._key_problem(m)))

    def _record_match(self, sc: Shortcut, game: Game | None, candidates: int = 0, commit: bool = False):
        """Remember what SteamGridDB matched. Exact matches are saved; a best guess is
        kept in memory and tagged "Check match" until the user acts on it (or `commit`)."""
        if game is None:
            self.status[sc.appid] = "notfound"
            return
        exact = is_exact(sc.name, game.name, sc.exe)
        self.candidates[sc.appid] = candidates
        if self.config.get_match(sc.appid):
            pass
        elif exact or commit:
            self.config.set_match(sc.appid, game.id, game.name, game.year)
        else:
            self.guesses[sc.appid] = game
        if exact or self.config.get_match(sc.appid):
            self.status.pop(sc.appid, None)
        else:
            self.status[sc.appid] = "check"

    def _load_rows(self, sc: Shortcut, game: Game):
        appid = sc.appid
        for key, row in self.page.rows.items():
            row.set_loading()

            def got(assets, row=row, key=key, gen=row.gen, gid=game.id):
                cur, now = self.current_shortcut(), self.current_game()
                if cur is None or cur.appid != appid or row.gen != gen or getattr(now, "id", None) != gid:
                    return  # an earlier load, answered late (another game, or the match changed)
                if assets is None:
                    row.set_message("Couldn't reach SteamGridDB. Check your connection.", "warning")
                else:
                    self.config.set_no_art(appid, key, not assets)
                    more = len(assets) >= 50
                    row.set_assets(assets, more)
                    if key == "grid":
                        self.page.update_match()

            self.fetch_assets(game.id, key, 0, got)

    def open_tray(self, slot: Slot):
        if self.current_game() is None and self.steam_option(slot.key) is None:
            self.change_match() if self.config.api_key else self.ask_api_key()
            return
        self.tray.open_slot(slot)  # its slot_changed brings the page's row into view
        self.tray.list.setFocus()

    def _show_row(self, slot: Slot):
        """Bring the page's row for the tray's piece into view, so its preview is seen."""
        def show():
            row = self.page.rows.get(slot.key)
            if row is not None and self.stage.currentWidget() is self.page:
                self.page.scroll.ensureWidgetVisible(row, 0, 24)
        QTimer.singleShot(0, show)

    def _tray_preview(self, slot: Slot, pm, asset=None):
        row = self.page.rows.get(slot.key)
        if row is None:
            return
        if pm is not None and not pm.isNull():
            backdrop = row.art.backdrop if slot.key == "logo" else None
            row.art.set_motion(*self.motion_spec(asset, row.art.size(), crop=not row.art.checker))
            row.art.animated = bool(asset and asset.animated)
            row.art.new = False
            row.art.set_pixmap(pm, backdrop)
            row._set_caption("Previewing", "preview")
            row._previewing = True
        elif row._previewing:
            row._previewing = False
            row.show_current()

    # ---- the game menu ------------------------------------------------------------

    def show_game_menu(self, idx: int, pos: QPoint):
        if not self.sfile or not 0 <= idx < len(self.sfile.shortcuts):
            return
        from .gamemenu import GameMenu, Item

        sc = self.sfile.shortcuts[idx]
        n = self.pending.count(sc.appid)
        menu = GameMenu(sc.name or "(unnamed)", art_text(self, sc),
                        self.icon_pixmap(sc), [
            Item("Get All Artwork", "download", lambda: self._run_fill([sc], single=True),
                 bool(self._unstaged_missing(sc)) and not self.busy),
            Item("Reload Options", "refresh", lambda: self.refresh_options(sc), bool(self.match_info(sc))),
            Item("Apply", "check", lambda: self.apply_games([sc.appid]), bool(n) and not self.busy, "go", n),
            None,
            Item("Open Game Page", "game-page", lambda: self.open_game(idx)),
            None,
            Item("Remove All Artwork…", "trash", lambda: self.remove_all(sc),
                 any(self.coverage(sc)) and not self.busy, "bad"),
        ], self)
        self._game_menu = menu  # kept alive while it shows
        menu.popup(pos)

    def refresh_options(self, sc: Shortcut):
        m = self.match_info(sc)
        if not m:
            return
        for key in [k for k in self.asset_cache if k[0] == m["id"]]:
            del self.asset_cache[key]
        if self.current_shortcut() is sc:
            self.load_options()
            if self.tray.is_open():
                self.tray.open_slot(self.tray.slot)
        self.toast.show_message(f"Refreshed the SteamGridDB options for {sc.name}.")

    def remove_all(self, sc: Shortcut):
        if not run_dialog(ConfirmDialog(self, f"Remove all artwork from {sc.name}?", "You can undo it straight after.",
                                        "Remove Artwork", "trash", bad=True), delete=True):
            return
        for key in self.pending.for_game(sc.appid):
            self.pending.drop(sc.appid, key)  # a waiting new title stays
        self.forget_files(sc.appid)  # what's on disk now
        jobs = [(sc, k, Pick(None)) for k in ORDER if self.slot_path(sc, SLOTS[k]) is not None]
        done, errors = self._write(jobs, self.steam_is_running(), self.user.grid_dir, self.sfile)
        self._after_write(done, errors, f"Removed all artwork from {sc.name}.")

    # ---- writing changes ------------------------------------------------------------

    def _write(self, jobs, defer_icons: bool, grid_dir: Path, sfile):
        """Save or clear each (shortcut, slot key, Pick) into `grid_dir` and `sfile`, the
        account the jobs were made for; returns the undo records and errors."""
        with self.config.batch():  # one config save, not one per piece
            return self._write_each(jobs, defer_icons, grid_dir, sfile)

    def _write_each(self, jobs, defer_icons: bool, grid_dir: Path, sfile):
        done, errors = [], []
        for sc, key, pick in jobs:
            slot = SLOTS[key]
            before = self.config.get_applied(sc.appid, key)
            defer = key == "icon" and defer_icons
            old_icon = sc.icon
            try:
                if pick.clears:
                    change = remove_asset(grid_dir, sc, slot, None if defer else sfile)
                else:
                    change = save_asset(grid_dir, sc, slot, pick.content(), pick.asset.mime,
                                        None if defer else sfile)
            except Exception as e:
                errors.append(f"{sc.name} {slot.label.lower()}: {e}")
                continue
            finally:
                self.forget_files(sc.appid)
            if defer:
                change.old_icon = old_icon  # so Undo can put the field back once the icon is linked
                self.config.set_pending_icon(sfile.path, sc.appid, "" if pick.clears else str(change.new_file))
            if pick.clears:
                self.config.set_applied(sc.appid, key, None)
            else:
                self.commit_match(sc)
                if self.status.get(sc.appid) == "check":
                    self.status.pop(sc.appid, None)
                info = {"id": pick.asset.id, "author": pick.asset.author}
                if pick.asset.style == EXE_STYLE:
                    info["from_exe"] = True
                if isinstance(pick.asset, SteamAsset):
                    info["steam"] = pick.asset.appid
                if isinstance(pick.asset, LocalFile):
                    info["file"] = Path(pick.asset.path).name if pick.asset.path else "Pasted picture"
                self.config.set_applied(sc.appid, key, info)
            self.pending.drop(sc.appid, key)
            self.pick_pix.pop((sc.appid, key), None)
            if pick.file:
                Path(pick.file).unlink(missing_ok=True)
            if change.reloaded:
                self._after_reload(sc.appid)
            done.append((change, before, defer))
        return done, errors

    def _after_write(self, done, errors, message: str, renamed=()):
        self.refresh_all()
        if self.current >= 0:
            self.page.show_game()
        if errors:
            self.last_errors = errors
            self._summary_notice(f"{plural(len(errors), 'piece')} couldn't be saved.")
        if not done and not renamed:
            return

        spath = self.sfile.path if self.sfile else None

        def revert():
            if self._wait_for_busy():
                return
            sfile = self.sfile
            if sfile is None or sfile.path != spath:
                self.toast.show_message("Couldn't undo: another account or Steam folder is open now.")
                return
            # With Steam open, a write to shortcuts.vdf is undone when Steam exits, so
            # the icon field goes back through the queue that waits for Steam to close.
            running = self.steam_is_running()
            for change, before, defer in reversed(done):
                try:
                    undo(change, None if running else sfile)
                except Exception as e:
                    self.toast.show_message(f"Couldn't undo: {e}")
                    return
                finally:
                    self.forget_files(change.shortcut.appid)
                if change.old_icon is not None:
                    queued = None
                    if running:
                        sc = sfile.find(change.shortcut.appid)
                        if sc is not None and sc.icon != change.old_icon:
                            queued = change.old_icon  # already linked: point it back once Steam closes
                    self.config.set_pending_icon(sfile.path, change.shortcut.appid, queued)
                elif defer:
                    self.config.set_pending_icon(sfile.path, change.shortcut.appid, None)
                self.config.set_applied(change.shortcut.appid, change.slot.key, before)
                if change.reloaded:
                    self._after_reload(change.shortcut.appid)
            for appid, old, new, queued in reversed(renamed):
                sc = sfile.find(appid)
                try:
                    self._set_keeps_name(appid, True)  # turned down: Get Artwork won't offer it again
                    if sc is not None and sc.name == new:  # written (maybe once Steam closed)
                        if running:
                            self.config.set_pending_name(sfile.path, appid, old)
                        else:
                            artwork.set_name(sfile, sc, old)
                    elif queued:
                        self.config.set_pending_name(sfile.path, appid, None)
                except Exception as e:
                    self.toast.show_message(f"Couldn't undo: {e}")
                    return
            if renamed:
                cur = self.current_shortcut()
                self._after_reload(cur.appid if cur else -1)
            self.refresh_all()
            if self.current >= 0:
                self.page.show_game()
            self.update_steam_state()
            self.toast.show_message("Put back the way it was.")

        self.toast.show_message(message, undo=revert)

    def _end_fill(self, fx: dict):
        if self._apply_fx is fx:  # not a later Apply's
            self._apply_fx = None
            self.update_chrome()

    def apply_all(self) -> bool:
        return self.apply_games(self.pending.games())

    def apply_games(self, appids: list[int]) -> bool:
        """Start applying the waiting picks for `appids`; False if nothing was started."""
        if self.busy or not self.sfile:
            return False
        jobs = [(self.sfile.find(a), k, p) for a in appids for k, p in self.pending.for_game(a).items()]
        jobs = [j for j in jobs if j[0] is not None]
        for a in appids:
            sc = self.sfile.find(a)
            if sc is not None:
                self._auto_rename(sc)
        renames = [(self.sfile.find(a), self.pending.name(a)) for a in appids if self.pending.name(a)]
        renames = [r for r in renames if r[0] is not None]
        if not jobs and not renames:
            return False
        # The account the picks were made for, even if another is opened before the writes.
        root, grid_dir, sfile = self.steam_root, self.user.grid_dir, self.sfile
        restart = self.steam_is_running() and self.auto_restart
        # A pick's cached download can be gone (another copy of the app tidied its cache): fetch it again.
        todo = [p for _, _, p in jobs if not p.clears and (not p.fetched or p.cached and not Path(p.cached).is_file())]
        # The Apply button fills a step per download, one for closing Steam, one for the writes.
        self._apply_fx = {"games": set(appids), "total": len(todo) + restart + 1, "step": 0,
                          "text": "Applying…"}
        self._set_busy("apply", f"Downloading artwork  1 of {len(todo)}" if todo else
                       "Closing Steam…" if restart else
                       f"Applying changes to {plural(len({j[0].appid for j in jobs} | {r[0].appid for r in renames}), 'game')}…")

        def work(progress):
            # Download first, so Steam is only closed once the files are in hand,
            # and one bad download doesn't cancel the rest. Several at a time.
            from concurrent.futures import ThreadPoolExecutor, as_completed

            failures = {}

            def fetch(pick):
                pick.data = self.sgdb.download(pick.asset.url)

            with ThreadPoolExecutor(max_workers=6) as ex:
                futures = {ex.submit(fetch, pick): pick for pick in todo}
                for i, fut in enumerate(as_completed(futures)):
                    progress(("download", i))
                    try:
                        fut.result()
                    except AuthError:
                        for f in futures:
                            f.cancel()
                        raise
                    except SGDBError as e:
                        failures[id(futures[fut])] = str(e)
            closed = False
            if restart and (renames or len(failures) < len(jobs)):
                progress(("close", 0))
                shutdown_steam(root)
                closed = wait_until_closed(root)
            # Asked here, off the window's thread (inside a Flatpak it can take seconds).
            return closed, failures, bool(steam_running(root))

        def on_progress(ev):
            kind, i = ev
            self.progress_text = (f"Downloading artwork  {i + 1} of {len(todo)}" if kind == "download"
                                  else "Closing Steam…")
            fx = self._apply_fx
            if fx:
                fx["step"] = i + 1 if kind == "download" else len(todo) + 1
                fx["text"] = "Closing Steam…" if kind == "close" else "Applying…"
            self.update_chrome()

        def done(result):
            closed, failures, running = result
            errors = [f"{sc.name} {SLOTS[k].label.lower()}: {failures[id(p)]}"
                      for sc, k, p in jobs if id(p) in failures]
            ready = [j for j in jobs if id(j[2]) not in failures]  # failed picks stay waiting
            written, write_errors = self._write(ready, running, grid_dir, sfile)
            # Writing a game's artwork settles its match, so its proper name can come along now too.
            later = []
            for a in appids:
                sc = sfile.find(a)
                if sc is not None and (self._auto_rename(sc) or self.pending.name(a)):
                    later.append((sc, self.pending.name(a)))
            renamed, rename_errors = self._rename(later, running, sfile)
            errors += write_errors + rename_errors
            if self._apply_fx:  # full, then "Applied ✓" for a moment, then back to normal
                self._apply_fx.update(step=self._apply_fx["total"],
                                      text="Applied ✓" if not errors else "Applied, with problems")
                fx = self._apply_fx
                QTimer.singleShot(1200, lambda: self._end_fill(fx))
            self._set_busy("")
            if restart and closed:
                self.apply_pending_icons()
                start_steam(root)
                self.restart_needed = False
                note = " Steam is restarting to show it."
            elif running:
                self.restart_needed = True
                note = " Restart Steam to see it." if not restart else " Steam didn't close, so restart it yourself."
            else:
                note = ""
            self.update_steam_state()
            games = len({c.shortcut.appid for c, _, _ in written})
            parts = [f"Applied new artwork to {plural(games, 'game')}."] if written else []
            if len(renamed) == 1:
                parts.append(f"Renamed {renamed[0][1] or '(unnamed)'} to {renamed[0][2]}.")
            elif renamed:
                parts.append(f"Renamed {plural(len(renamed), 'game')}.")
            self._after_write(written, errors, " ".join(parts) + note, renamed)
            if self._close_after_apply:
                self._close_after_apply = False
                if not self.pending:  # some pieces failed: stay open so the user sees why
                    self._close_after_apply = True
                    self.close()

        def failed(msg):
            self._apply_fx = None
            self._set_busy("")
            self._close_after_apply = False
            if restart and not self.steam_is_running():
                start_steam(root)
            self._key_problem(msg)
            self.toast.show_message(f"Couldn't apply the artwork: {msg}")

        workers.run(work, kind="long", on_progress=on_progress, on_done=done, on_error=failed)
        return True

    # ---- SteamGridDB key and matching ---------------------------------------

    def ensure_key(self) -> bool:
        """True if there's a key; otherwise asks for one and returns False."""
        if self.config.api_key:
            return True
        self.ask_api_key()
        return False

    def ask_api_key(self):
        """Open the API key pop-over; saving it reconnects and reloads (see _key_saved)."""
        if self.keypop.is_open() and self.sender() is self.statusbar:
            return self.keypop.dismiss()  # a second click on the status item closes it
        self.popover.dismiss()
        self.folderpop.dismiss()
        self.keypop.open_for(self.config.api_key, self.statusbar.connection_anchor())

    def _key_saved(self, key: str):
        self.config.api_key = key
        self.sgdb.api_key = key
        self.asset_cache.clear()
        self.set_connection("connected")
        if self.current >= 0:
            self.load_options()
            self.page.update_match()
        self.update_chrome()
        self.toast.show_message("Connected to SteamGridDB.")

    def _key_problem(self, msg: str):
        self.set_connection("disconnected")
        if "API key" in msg:
            self.toast.show_message(msg)

    def confirm_match(self):
        sc = self.current_shortcut()
        if not sc:
            return
        self.commit_match(sc)
        self.status.pop(sc.appid, None)
        self.page.update_match()
        self.refresh_all()
        self.toast.show_message(f"Match confirmed for {sc.name}.")

    def change_match(self):
        sc = self.current_shortcut()
        if not sc or self.busy == "apply" or (not self.steam_art and not self.ensure_key()):
            return
        dlg = MatchDialog(self, self.sgdb, sc.name, sc.exe, self.steam if self.steam_art else None,
                          self._steam_now(sc))
        try:
            ok = run_dialog(dlg)
            g = dlg.selected() if ok and self.config.api_key else None
            steam = dlg.steam_choice() if ok else None
        finally:
            dlg.deleteLater()
        if steam is not None:
            self._set_steam_game(sc, steam)
            self.load_steam()
            self.refresh_all()
        elif ok and self.steam_art and sc.appid not in self.steam_found:
            self.load_steam()  # the store couldn't be reached before: try again
        if g is not None:
            old = self.match_info(sc)
            self.config.set_match(sc.appid, g.id, g.name, g.year)
            self.guesses.pop(sc.appid, None)
            self.status.pop(sc.appid, None)
            if not old or old.get("id") != g.id:
                self.pending.drop(sc.appid)  # picks from the other game no longer fit
            self.page.update_match()
            self.load_options()
            self.refresh_all()
            if self.tray.is_open():
                self.tray.open_slot(self.tray.slot)

    # ---- getting artwork in bulk ---------------------------------------------

    def _fetch_missing(self, jobs, progress, use_sgdb: bool = True, notfound: frozenset = frozenset(),
                       animated: bool = False):
        """Worker: for each (shortcut, slots, saved match, single, Steam lookup), match and download
        the top pick: Steam's official piece where there is one, else SteamGridDB's top option.
        With `animated`, SteamGridDB's top animated option comes first for the pieces Steam plays,
        then the still ones as before."""
        report = {"found": 0, "notfound": [], "gaps": 0, "errors": [], "unsure": 0, "stopped": False}
        for i, (sc, slots, saved, single, steam) in enumerate(jobs):
            if self._stop_fill:  # Stop in the status bar: finish the game in hand, then stop
                report["stopped"] = True
                break
            progress(("step", i, sc))
            official = {}
            if steam is not None:
                found, saved_steam = steam
                try:
                    if found is None:
                        found = self._steam_lookup(search_name(sc.name, sc.exe), saved_steam)
                        progress(("steam", sc, found))
                    official = found[1]
                except Exception:
                    pass  # the store couldn't be reached: SteamGridDB alone, as before
            game = None
            if use_sgdb and sc.appid not in notfound:
                try:
                    if saved:
                        game, n = Game(saved["id"], saved["name"], saved.get("year", "")), 0
                    else:
                        game, n = self.sgdb.match(search_name(sc.name, sc.exe))
                except AuthError:
                    raise
                except Exception as e:
                    report["errors"].append(f"{sc.name}: {e}")
                    if not official:
                        continue
                else:
                    progress(("match", sc, game, n, bool(saved)))
                    if game is None:
                        report["notfound"].append(sc.name)
                    elif not saved and not single and not is_exact(sc.name, game.name, sc.exe):
                        report["unsure"] += 1  # a best guess: let the user check it before fetching art
                        game = None
            got = False
            for slot in slots:
                assets = None  # SteamGridDB's options for the piece, once asked
                if animated and slot.animates and game is not None:
                    # An animated piece beats Steam's official one, which never moves.
                    try:
                        assets = self.sgdb.assets(slot.key, game.id, animated=True)
                        top = next((x for x in assets if x.animated), None)
                        if top is not None:
                            data = self.sgdb.download(top.url)
                            progress(("asset", sc, slot, *_spool_pick(data, sc.appid, slot.key), top, game.id))
                            got = True
                            continue
                        assets = [x for x in assets if not x.animated]  # none animated: the top still one
                    except AuthError:
                        raise
                    except Exception:
                        assets = None  # a still piece instead
                a = official.get(slot.key)
                if a is not None:
                    try:
                        progress(("asset", sc, slot, *_spool_pick(self.sgdb.download(a.url), sc.appid, slot.key), a, None))
                        got = True
                        continue
                    except Exception:
                        pass  # SteamGridDB's top option instead
                if game is None:
                    continue
                try:
                    if assets is None:
                        assets = self.sgdb.assets(slot.key, game.id)
                    if not assets:
                        report["gaps"] += 1
                        progress(("empty", sc, slot))
                        continue
                    data = self.sgdb.download(assets[0].url)
                    progress(("asset", sc, slot, *_spool_pick(data, sc.appid, slot.key), assets[0], game.id))
                    got = True
                except AuthError:
                    raise
                except Exception as e:
                    report["errors"].append(f"{sc.name} {slot.label.lower()}: {e}")
            report["found"] += got
            # The .exe icon may stand in for a missing one when this is done: read it here, not
            # on the window's thread (the drive may be asleep, or a network share).
            if (sc.appid, sc.exe) not in self._exe_icons and (
                    SLOTS["icon"] in slots or "icon" in self.config.no_art(sc.appid)):
                self._exe_icons[(sc.appid, sc.exe)] = _exe_icon_png(sc.appid, sc.exe)
        return report

    def _run_fill(self, shortcuts: list[Shortcut], single: bool = False, animated: bool | None = None):
        """Get Artwork for `shortcuts`; `animated` overrides the Get All Prefers Animated setting."""
        # Without a SteamGridDB key, Steam's official artwork can still fill the gaps.
        use_sgdb = bool(self.config.api_key)
        if self.busy or (not use_sgdb and not self.steam_art and not self.ensure_key()):
            return

        def steam(sc):
            if not self.steam_art:
                return None
            return self.steam_found.get(sc.appid), self.config.get_steam_match(sc.appid)

        jobs = [(sc, self._unstaged_missing(sc, recheck=single), self.match_info(sc), single, steam(sc))
                for sc in shortcuts]
        # Games SteamGridDB didn't have aren't searched again (Steam's store still is).
        notfound = {sc.appid for sc in shortcuts if self.status.get(sc.appid) == "notfound" and not single}
        skipped = sum(1 for j in jobs if j[1] and j[0].appid in notfound and j[4] is None)
        jobs = [j for j in jobs if j[1] and (j[0].appid not in notfound or j[4] is not None)
                or self.rename_due(j[0])]  # nothing missing, but a title like "game.exe" to fix
        if not jobs:
            self.toast.show_message("Nothing to fill in." if not skipped else
                                    f"Nothing to fill in: {plural(skipped, 'game')} not found on SteamGridDB.")
            return
        total = len(jobs)
        renamed = []  # games given their SteamGridDB name along the way
        self._stop_fill = False
        self._set_busy("fetch", f"Getting artwork  1 of {plural(total, 'game')}")

        def on_progress(ev):
            kind = ev[0]
            if kind == "step":
                _, i, sc = ev
                self._progress(f"Getting artwork  {i + 1} of {plural(total, 'game')}")
                self.status[sc.appid] = "getting"
                self.refresh_soon()
            elif kind == "match":
                _, sc, game, n, saved = ev
                self.status.pop(sc.appid, None)
                if not saved:
                    self._record_match(sc, game, n, commit=single)
                if self._auto_rename(sc):
                    renamed.append(sc)
                if self.current_shortcut() is sc:
                    self.page.update_match()
                self.refresh_soon()
            elif kind == "steam":
                _, sc, found = ev
                self.steam_found[sc.appid] = found
                if self.current_shortcut() is sc and self.stage.currentWidget() is self.page:
                    self._show_steam(sc, found)
            elif kind == "asset":
                _, sc, slot, file, img, asset, gid = ev
                self.config.set_no_art(sc.appid, slot.key, False)
                if self.pending.get(sc.appid, slot.key) is not None or not self._fill_still_wanted(sc, asset, gid):
                    Path(file).unlink(missing_ok=True)  # the user picked, cleared or rematched it meanwhile
                    return
                pm = QPixmap.fromImage(img) if not img.isNull() else None
                self._stage(sc, slot.key, Pick(asset, file=file), pm)
                self.refresh_soon()
            elif kind == "empty":
                _, sc, slot = ev
                self.config.set_no_art(sc.appid, slot.key, True)  # not counted as missing for a while

        def done(report):
            self._set_busy("")
            self.set_connection("connected")
            self.last_errors = list(report["errors"])
            for sc, *_ in jobs:
                if self.status.get(sc.appid) == "getting":
                    self.status.pop(sc.appid, None)
            self.refresh_all()
            # SteamGridDB has no icon for these: the game's .exe may have one for Steam.
            exe_icons = [sc for sc, *_ in jobs if "icon" in self.config.no_art(sc.appid) and self.stage_exe_icon(sc)]
            report["gaps"] -= sum(1 for sc, slots, *_ in jobs if sc in exe_icons and SLOTS["icon"] in slots)
            report["found"] += sum(1 for sc in exe_icons if len(self.pending.for_game(sc.appid)) == 1)
            if exe_icons:
                self.refresh_all()
            if self.current >= 0:
                self.load_options()
            if single:
                sc = jobs[0][0]
                n = len(self.pending.for_game(sc.appid))
                msg = (f"Found {plural(n, 'piece')} of artwork for {sc.name}, ready to apply." if n
                       else f"Nothing new for {sc.name} yet." if self.steam_art
                       else f"SteamGridDB has nothing new for {sc.name} yet.")
                if renamed:
                    msg += f" It will be renamed {self.pending.name(sc.appid)}."
                self.toast.show_message(msg)
                if not report["errors"]:
                    return
            parts = [f"Found artwork for {plural(report['found'], 'game')}, ready to apply."]
            if renamed:
                parts.append(f"{plural(len(renamed), 'game')} will get {'its' if len(renamed) == 1 else 'their'} "
                             "proper name.")
            if report.get("stopped"):
                parts.insert(0, "Stopped.")
            if report["unsure"]:
                parts.append(f"{plural(report['unsure'], 'game')} matched by best guess: check "
                             f"{'it' if report['unsure'] == 1 else 'them'} first.")
            if report["notfound"]:
                parts.append(f"{len(report['notfound'])} not found on SteamGridDB.")
            if report["gaps"]:
                parts.append(f"{plural(report['gaps'], 'piece')} had no options yet.")
            if report["errors"]:
                parts.append(f"{len(report['errors'])} couldn't be downloaded.")
            if skipped:
                parts.append(f"{skipped} skipped (not found before).")
            if report["errors"] or report["unsure"] or report["notfound"]:
                return self._summary_notice(" ".join(parts))  # stays up, with a way to act on it
            # Nothing to act on: a short line in the status bar, and the lit Apply button stays.
            self.notice.hide()
            found = report["found"]
            line = [f"Found artwork for {plural(found, 'game')}, ready to apply" if found
                    else "No new artwork found"]
            if renamed:
                line.append(f"{plural(len(renamed), 'rename')} ready")
                found = found or len(renamed)
            if report.get("stopped"):
                line.insert(0, "Stopped")
            if report["gaps"]:
                line.append(f"{plural(report['gaps'], 'piece')} had no options yet")
            if skipped:
                line.append(f"{skipped} skipped (not found before)")
            self.show_note("done" if found else "info", " · ".join(line), hold=8000 if found else None)

        def failed(msg):
            self._set_busy("")
            self._key_problem(msg)
            for sc, *_ in jobs:
                if self.status.get(sc.appid) == "getting":
                    self.status.pop(sc.appid, None)
            self.refresh_all()
            tell(self, APP_NAME, msg)

        workers.run(self._fetch_missing, jobs, kind="long", use_sgdb=use_sgdb, notfound=frozenset(notfound),
                    animated=self.get_all_animated if animated is None else animated,
                    on_progress=on_progress, on_done=done,
                    on_error=failed)

    def fill_current(self, animated: bool | None = None):
        sc = self.current_shortcut()
        if sc:
            if self.status.get(sc.appid) == "notfound":
                self.status.pop(sc.appid, None)
            self._run_fill([sc], single=True, animated=animated)

    def fill_everything(self, animated: bool | None = None):
        if self.sfile:
            self._run_fill(list(self.sfile.shortcuts), animated=animated)

    def _progress(self, text: str):
        self.progress_text = text
        if self.busy:
            self.statusbar.set_message("busy", text)

    def stop_fill(self):
        """Stop a Get Artwork run after the game it's on; what it found stays waiting."""
        if self.busy == "fetch":
            self._stop_fill = True
            self.statusbar.stop.setEnabled(False)
            self._progress("Stopping after this game…")

    def _set_busy(self, busy: str, text: str = ""):
        self.busy = busy
        if busy:
            self._note = None  # the last run's result is old news once new work starts
            self._note_timer.stop()
        self.progress_text = text
        self.statusbar.set_stoppable(busy == "fetch")
        # Only what would change the account or the game list mid-run; the app mark stays as it is.
        self.header.account.setEnabled(not busy)
        self.header.more.setEnabled(not busy)
        self.statusbar.steam.setEnabled(not busy)  # no switching account or Steam folder mid-change
        self.update_chrome()

    def _summary_notice(self, text: str):
        def dismiss():
            self.notice.hide()

        actions = [("Dismiss", dismiss)]
        if self.last_errors:
            actions.insert(0, ("Show details", lambda: tell(
                self, APP_NAME, "These couldn't be downloaded:\n\n" + "\n".join(self.last_errors[:40]),
                QMessageBox.Icon.Information)))
        if any(v in ("notfound", "check") for v in self.status.values()):
            actions.insert(0, ("Show games to check", lambda: self.game_list.set_filter("check")))
        self.notice.show_message("info", text, actions)

    # ---- menu actions -----------------------------------------------------

    def restore_backup(self):
        if not self.sfile or self.busy:
            return
        running = self.steam_is_running()
        dlg = RestoreDialog(self, self.sfile.backups(), self.sfile.shortcuts, running)
        try:
            path = dlg.selected() if run_dialog(dlg) else None
        finally:
            dlg.deleteLater()
        if not path:
            return
        if not self.steam_is_running():
            return self._restore(path)
        root = self.steam_root
        self._set_busy("restart", "Closing Steam…")

        def work():
            shutdown_steam(root)
            return wait_until_closed(root)

        def done(closed):
            self._set_busy("")
            self.update_steam_state()
            if not closed:
                self.toast.show_message("Steam didn't close, so nothing was restored. Close it, then try again.")
                return
            restored = self._restore(path)
            if self.auto_restart:
                start_steam(root)
                if restored:
                    self.toast.show_message("Game list restored, and Steam is starting again. "
                                            "The list you had before is saved as a copy too.")

        workers.run(work, on_done=done, on_error=lambda m: (self._set_busy(""), self.toast.show_message(m)))

    def _restore(self, path: Path) -> bool:
        try:
            self.sfile.restore(path)
        except Exception as e:
            tell(self, APP_NAME, f"Couldn't restore that copy: {e}")
            return False
        self.reload_shortcuts()
        self.toast.show_message("Game list restored. The list you had before is saved as a copy too.")
        return True

    def open_grid_folder(self):
        if self.user:
            self.user.grid_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.user.grid_dir)))

    def about(self):
        run_dialog(AboutDialog(self, APP_NAME, __version__), delete=True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.tray.is_open():
            self.tray.close_tray()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        if self.busy == "apply":
            self._close_after_apply = True
            e.ignore()
            return
        if self.busy in ("fetch", "restart"):
            # Quitting now would drop the artwork being fetched, or leave Steam closed.
            e.ignore()
            self._wait_for_busy()
            return
        if self.pending and not self._close_after_apply:
            choice = "apply" if self.apply_on_quit else self._ask_on_quit()
            if choice is None:
                e.ignore()
                return
            if choice == "apply":
                e.ignore()
                if self.apply_all():
                    self._close_after_apply = True
                return
        super().closeEvent(e)


def focus_start(win: MainWindow):
    """Start on the library with the cover wall focused. Focusing the game list instead
    would make Qt select its first game, which opens that game's page."""
    win.start.wall.setFocus()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("io.github.acidtwin.SteamArtworkForNonSteamGames")
    app.setStyle("Fusion")
    from . import theme

    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(icons.app_icon())
    from . import smooth

    smooth.install(app)
    app.aboutToQuit.connect(workers.shutdown)
    clear_picks()
    win = MainWindow()
    screen = app.primaryScreen().availableGeometry() if app.primaryScreen() else None
    if screen is not None and (screen.width() <= 1280 or screen.height() <= 800):
        win.showMaximized()  # Steam Deck: the whole work area, under the panel
    else:
        if screen is not None:
            win.resize(min(1280, screen.width() - 80), min(800, screen.height() - 80))
        win.show()
    focus_start(win)
    return app.exec()
