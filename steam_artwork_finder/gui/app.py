"""The main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from .. import artwork
from ..artwork import SLOTS, Slot, current_icon, existing, prune_replaced, remove_asset, save_asset, undo
from ..config import Config
from ..pending import Pending, Pick
from ..sgdb import Asset, AuthError, Game, SteamGridDB, clean_name, name_variants
from ..shortcuts import Shortcut
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
from . import icons, workers
from .chrome import ContentBar, Header, StatusBar, SteamPopover
from .steamfolder import SteamFolderPopover
from .dialogs import KeyPopover, MatchDialog, QuitDialog, RestoreDialog
from .gamepage import GamePage
from .library import GameList, StartPage
from .tray import ROLE_PIX, OptionTray
from .widgets import ArtView, Notice, Toast, label, load_pixmap

ORDER = ("grid", "wide", "hero", "logo", "icon")


ISSUES_URL = "https://github.com/acidtwin/steam-artwork-for-non-steam-games/issues"


def _join(words: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def is_exact(sc_name: str, game_name: str) -> bool:
    """The SteamGridDB name is the shortcut's name, give or take launcher tags."""
    want = clean_name(game_name).casefold()
    return any(v.casefold() == want for v in name_variants(sc_name))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1280, 800)  # Steam Deck screen; main() maximises on screens that small
        self.home = Path.home()
        self.config = Config()
        self.sgdb = SteamGridDB(self.config.api_key)
        self.users: list[SteamUser] = []
        self.user: SteamUser | None = None
        self.sfile = None
        self.status: dict[int, str] = {}  # appid -> "notfound" | "check" | "getting"
        self.guesses: dict[int, Game] = {}  # best guesses not yet confirmed by an action
        self._matching = False  # the startup lookup of every game is running
        self.candidates: dict[int, int] = {}  # appid -> how many similar names SteamGridDB returned
        self.asset_cache: dict[tuple, list[Asset]] = {}
        self.pending = Pending()
        self.pick_pix: dict[tuple[int, str], QPixmap] = {}
        self.last_errors: list[str] = []
        self.current = -1
        self.busy = ""  # "" | "fetch" | "apply" | "restart"
        self.progress_text = ""
        self.restart_needed = False
        self.conn = "disconnected"
        self._close_after_apply = False

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = Header()
        self.header.home_clicked.connect(self.go_home)
        self.header.user_chosen.connect(self._account_changed)
        self.header.more_clicked.connect(self.show_more_menu)
        outer.addWidget(self.header)
        self.notice = Notice()
        outer.addWidget(self.notice)

        self.split = QSplitter()
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        self.game_list = GameList(self)
        self.game_list.setMinimumWidth(260)
        self.game_list.picked.connect(self.show_index)
        self.split.addWidget(self.game_list)

        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.cbar = ContentBar()
        self.cbar.home_clicked.connect(self.go_home)
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
            "then click Check Again below.", "Check Again", self.reload_shortcuts, "")
        self.start = StartPage(self)
        self.start.picked.connect(self.open_game)
        self.page = GamePage(self)
        self.page.fill.connect(self.fill_current)
        self.page.change_match.connect(self.change_match)
        self.page.confirm_match.connect(self.confirm_match)
        for w in (self.nosteam, self.nogames, self.start, self.page):
            self.stage.addWidget(w)
        cl.addWidget(self.stage, 1)
        self.statusbar = StatusBar()
        self.statusbar.steam_clicked.connect(self.show_folder_popover)
        self.statusbar.process_clicked.connect(self.show_steam_popover)
        self.statusbar.connection_clicked.connect(self.ask_api_key)
        cl.addWidget(self.statusbar)
        self.split.addWidget(col)

        self.tray = OptionTray(self)
        self.tray.hide()
        self.tray.preview.connect(self._tray_preview)
        self.split.addWidget(self.tray)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([300, 980, 0])
        outer.addWidget(self.split, 1)

        self.popover = SteamPopover(self)
        self.popover.anchor_widget = self.statusbar.process
        self.keypop = KeyPopover(self)
        self.keypop.anchor_widget = self.statusbar.connection
        self.keypop.saved.connect(self._key_saved)
        self.folderpop = SteamFolderPopover(self)
        self.folderpop.anchor_widget = self.statusbar.steam
        self.folderpop.use_root.connect(self.use_steam_root)
        self.folderpop.account_chosen.connect(self._account_changed)
        self.popover.set_auto_restart(self.auto_restart)
        self.popover.auto_restart_changed.connect(self._set_auto_restart)
        self.popover.set_apply_on_quit(self.apply_on_quit)
        self.popover.apply_on_quit_changed.connect(self._set_apply_on_quit)
        self.popover.restart_clicked.connect(self.restart_steam_now)

        self.toast = Toast(central)
        self.steam_timer = QTimer(self, interval=5000, timeout=self.update_steam_state)
        self.steam_timer.start()
        for keys, fn in ((QKeySequence.StandardKey.Find, lambda: self.game_list.search.setFocus()),
                         (QKeySequence.StandardKey.Undo, self.toast.undo_now),
                         (QKeySequence.StandardKey.Refresh, self.reload_shortcuts),
                         (QKeySequence("Ctrl+R"), self.reload_shortcuts),
                         (QKeySequence("Ctrl+Q"), self.close)):
            act = QAction(self)
            act.setShortcut(keys)
            act.triggered.connect(fn)
            self.addAction(act)
        self.detect_steam()
        self.check_connection()

    # ---- layout pieces ----------------------------------------------------

    def show_more_menu(self):
        from .gamemenu import SHADOW, GameMenu, Item

        menu = GameMenu(None, "", None, [
            "Library",
            Item("Reload Game List", "refresh", self.reload_shortcuts, shortcut="Ctrl+R"),
            Item("Open Artwork Folder", "images", self.open_grid_folder, bool(self.user)),
            Item("Restore Steam's Game List…", "history", self.restore_backup, bool(self.sfile)),
            "Settings",
            Item("Steam Folder…", "folder", self.show_folder_popover),
            Item("SteamGridDB API Key…", "key", self.ask_api_key),
            Item("Restart Steam When I Apply", "restart", self._menu_auto_restart, toggle=self.auto_restart),
            Item("Apply When I Quit", "check", self._menu_apply_on_quit, toggle=self.apply_on_quit),
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
        for t, name in ((title, "title"), (text, "muted"), (hint, "faint")):
            if t:
                lab = label(t, name, wrap=True)
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lab.setMaximumWidth(460)
                lay.addWidget(lab, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(12)
        b = QPushButton(button)
        if kind:
            b.setProperty("kind", kind)
        b.clicked.connect(fn)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(2)
        return w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.centralWidget().width() - self.toast.width()) // 2,
                            self.centralWidget().height() - self.toast.height() - 52)

    # ---- state helpers used by the pages ----------------------------------

    def slot_path(self, sc: Shortcut, slot: Slot) -> Path | None:
        if slot.key == "icon":
            return current_icon(sc, self.user.grid_dir)
        return existing(self.user.grid_dir, sc.appid, slot)

    def coverage(self, sc: Shortcut) -> list[bool]:
        return [self.slot_path(sc, SLOTS[k]) is not None for k in ORDER]

    def cover_path(self, sc: Shortcut) -> Path | None:
        return self.slot_path(sc, SLOTS["grid"])

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
        def done(data):
            pm = load_pixmap(data=data, key=("thumb", asset.thumb))
            try:
                if isinstance(target, ArtView):
                    target.set_pixmap(pm)
                elif isinstance(target, QListWidgetItem):
                    target.setData(ROLE_PIX, pm)
                elif callable(target):
                    target(pm)
            except RuntimeError:  # the widget was replaced meanwhile
                pass

        workers.run(workers.cached_download, asset.thumb, self.sgdb.download, on_done=done)

    def fetch_assets(self, game_id: int, slot_key: str, page: int, callback):
        key = (game_id, slot_key, page)
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

        workers.run(self.sgdb.assets, slot_key, game_id, page, on_done=done, on_error=failed)

    # ---- waiting changes ----------------------------------------------------

    def pick_pixmap(self, sc: Shortcut, slot_key: str) -> QPixmap | None:
        pick = self.pending.get(sc.appid, slot_key)
        if pick is None or pick.clears:
            return None
        return self.pick_pix.get((sc.appid, slot_key))

    def _stage(self, sc: Shortcut, slot_key: str, pick: Pick):
        self.pending.set(sc.appid, slot_key, pick)
        key = (sc.appid, slot_key)
        self.pick_pix.pop(key, None)
        if pick.clears:
            return
        if pick.data:
            self.pick_pix[key] = load_pixmap(data=pick.data, key=("pick", pick.asset.id))
            return

        def loaded(pm, key=key, asset=pick.asset):
            cur = self.pending.get(*key)
            if cur is not None and cur.asset is asset:
                self.pick_pix[key] = pm
                self.refresh_all()

        self.load_thumb(pick.asset, loaded)

    def stage_pick(self, slot: Slot, asset: Asset):
        sc = self.current_shortcut()
        if not sc or self.busy == "apply":
            return
        self._stage(sc, slot.key, Pick(asset))
        self.refresh_all()

    def stage_clear(self, slot: Slot):
        sc = self.current_shortcut()
        if not sc or self.busy == "apply":
            return
        if self.slot_path(sc, slot) is None:
            self.pending.drop(sc.appid, slot.key)  # nothing on disk: just drop the waiting pick
        else:
            self._stage(sc, slot.key, Pick(None))
        self.refresh_all()

    def unstage(self, slot: Slot):
        sc = self.current_shortcut()
        if sc:
            self.pending.drop(sc.appid, slot.key)
            self.refresh_all()

    def _unstaged_missing(self, sc: Shortcut) -> list[Slot]:
        return [SLOTS[k] for k, have in zip(ORDER, self.coverage(sc))
                if not have and self.pending.get(sc.appid, k) is None]

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

    def show_folder_popover(self):
        if self.folderpop.is_open():
            return self.folderpop.dismiss()
        self.popover.dismiss()
        self.keypop.dismiss()
        self._sync_folder_popover()
        self.folderpop.open_for(self.statusbar.steam_anchor())

    def _sync_folder_popover(self):
        idx = self.users.index(self.user) if self.user in self.users else -1
        self.folderpop.set_current(self.steam_root, [u.label for u in self.users], idx, self._avatar())

    def use_steam_root(self, root: Path):
        """Switch to another Steam folder (from the pop-over), remembering it for next time."""
        if root == self.steam_root:
            return
        if self.pending and not self._ask_discard():
            return
        self.pending.clear()
        self.config.data["steam_folder"] = str(root)
        self.config.save()
        self.detect_steam([root])

    def choose_steam_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose your Steam folder", str(Path.home()))
        if d:
            roots = find_steam_roots([Path(d)])
            if not roots:
                QMessageBox.warning(self, APP_NAME, "That folder doesn't have a Steam “userdata” folder inside it.")
                return
            self.use_steam_root(roots[0])

    def _account_changed(self, idx: int):
        if not 0 <= idx < len(self.users):
            return
        if self.pending and self.user is not self.users[idx]:
            if not self._ask_discard():
                self.header.set_users([u.label for u in self.users], self.users.index(self.user), self._avatar())
                return
        self.user = self.users[idx]
        self.header.set_users([u.label for u in self.users], idx, self._avatar())
        self._sync_folder_popover()
        self.reload_shortcuts()

    def _avatar(self) -> QPixmap | None:
        p = avatar_path(self.steam_root, self.user.account_id) if self.user else None
        pm = load_pixmap(p) if p else None
        return pm if pm is not None and not pm.isNull() else None

    def _ask_discard(self) -> bool:
        n = len(self.pending.games())
        return QMessageBox.question(
            self, APP_NAME, f"New artwork for {plural(n, 'game')} hasn't been applied yet. Discard it?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def reload_shortcuts(self):
        if not self.user:
            return self.detect_steam()
        from ..shortcuts import ShortcutsFile

        try:
            self.sfile = ShortcutsFile(self.user.shortcuts_path)
        except Exception as e:
            QMessageBox.critical(
                self, APP_NAME,
                f"Steam's shortcut list couldn't be read:\n{e}\n\nYou can restore a backup from the ⋯ menu.")
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
        if self.current >= len(self.sfile.shortcuts):
            self.current = -1
        self.game_list.rebuild()
        self.start.rebuild()
        self.show_index(self.current)
        workers.run(prune_replaced, self.user.grid_dir)
        self.match_all()
        self.update_steam_state()

    def _after_reload(self, appid: int):
        """The shortcut list was re-read from disk mid-change: rebuild what indexes into it."""
        self.current = next((i for i, s in enumerate(self.sfile.shortcuts) if s.appid == appid), -1) \
            if self.current >= 0 else -1
        self.game_list.rebuild()
        self.start.rebuild()

    # ---- Steam itself ---------------------------------------------------------

    def steam_is_running(self) -> bool:
        return bool(self.user and steam_running(self.steam_root))

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
        for appid in self.pending.games():
            sc = self.sfile.find(appid) if self.sfile else None
            if sc is None:
                continue
            picks = self.pending.for_game(appid)
            ordered = sorted(picks.items(), key=lambda kp: ORDER.index(kp[0]))
            names = [SLOTS[k].label.lower() for k, _ in ordered]
            clears = {p.clears for _, p in ordered}
            if clears == {False}:
                what = "new " + _join(names)
            elif clears == {True}:
                what = _join(names) + " removed"
            else:
                what = _join([f"{n} removed" if p.clears else f"new {n}" for n, (_, p) in zip(names, ordered)])
            cover = self.pick_pixmap(sc, "grid")
            if cover is None and not (picks.get("grid") and picks["grid"].clears):
                path = self.cover_path(sc)
                cover = load_pixmap(path) if path else None
            games.append((sc.name or "(unnamed)", what[:1].upper() + what[1:], len(picks), cover))
        dlg = QuitDialog(self, games)
        if not dlg.exec():
            return None
        if dlg.choice == "apply" and dlg.remember.isChecked():
            self._set_apply_on_quit(True)
            self.popover.set_apply_on_quit(True)
        return dlg.choice

    def _set_auto_restart(self, on: bool):
        self.config.data["auto_restart_steam"] = on
        self.config.save()

    def steam_state(self) -> str:
        running = self.steam_is_running()
        if running and (self.restart_needed or self.config.pending_icons):
            return "restart"
        return "running" if running else "closed"

    def update_steam_state(self):
        running = self.steam_is_running()
        if not running:
            self.restart_needed = False  # Steam loads the new artwork when it next starts
            self.apply_pending_icons()
        self.statusbar.set_process(self.steam_state())

    def show_steam_popover(self):
        if self.popover.is_open():
            return self.popover.dismiss()
        self.keypop.dismiss()
        self.folderpop.dismiss()
        self.popover.set_auto_restart(self.auto_restart)
        self.popover.show_for(self.steam_state(), self.statusbar.process_anchor(), len(self.config.pending_icons))

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

    def apply_pending_icons(self):
        """Link icons that were saved while Steam was running, now that it has closed."""
        pending = dict(self.config.pending_icons)
        if not pending or not self.sfile or self.steam_is_running():
            return
        linked = 0
        for key, path in pending.items():
            sc = self.sfile.find(int(key))
            if sc is not None:
                try:
                    artwork.set_icon(self.sfile, sc, path)
                    linked += 1
                except Exception as e:
                    self.toast.show_message(f"Couldn't link the icon for {sc.name}: {e}")
                    continue
            self.config.set_pending_icon(int(key), None)
        if linked:
            self.refresh_all()

    # ---- SteamGridDB connection ---------------------------------------------

    def set_connection(self, state: str):
        if not self.config.api_key:
            state = "disconnected"
        self.conn = state
        self.statusbar.set_connection(state)
        if state == "connected":
            QTimer.singleShot(0, self.match_all)

    def match_all(self):
        """Look every unmatched game up on SteamGridDB in the background, so best
        guesses show "Check Match" in the list without opening each game first."""
        if self._matching or self.conn != "connected" or not self.sfile:
            return
        todo = [(sc.appid, sc.name) for sc in self.sfile.shortcuts
                if sc.name and not self.config.get_match(sc.appid)
                and sc.appid not in self.guesses and sc.appid not in self.status]
        if not todo:
            return
        self._matching = True
        sfile = self.sfile

        def work(progress):
            for appid, name in todo:
                try:
                    game, n = self.sgdb.match(name)
                except Exception:
                    continue  # tried again next start, or when the game is opened
                progress((appid, game, n))

        def got(result):
            appid, game, n = result
            if self.sfile is not sfile or self.config.get_match(appid) or appid in self.guesses:
                return
            sc = next((g for g in sfile.shortcuts if g.appid == appid), None)
            if sc is None or appid in self.status:
                return
            self._record_match(sc, game, n)
            if sc is self.current_shortcut() and self.stage.currentWidget() is self.page:
                self.page.update_match()
            self.refresh_all()

        def finished(*_):
            self._matching = False

        workers.run(work, on_progress=got, on_done=finished, on_error=finished)

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

    def update_chrome(self):
        """The content bar's title and buttons, and the status bar's messages."""
        self.statusbar.set_steam(bool(self.users), str(self.steam_root).replace(str(self.home), "~")
                                 if self.steam_root else "")
        games = self.sfile.shortcuts if self.sfile else []
        sc = self.current_shortcut()
        on_page = sc is not None and self.stage.currentWidget() is self.page
        self.cbar.setVisible(not on_page)  # the game page's banner header has its own title and buttons
        if on_page:
            self.page.card.set_game(sc.name or "(unnamed)")
            self.page.card.set_actions(bool(self._unstaged_missing(sc)), self.pending.count(sc.appid), bool(self.busy))
        else:
            self.cbar.show_library(len(games) if self.users else None)
            need = sum(1 for g in games if self._unstaged_missing(g) and self.status.get(g.appid) != "notfound")
            ready = len(self.pending.games())
            specs = []
            if games and (need or not ready):
                specs.append((f"Get Artwork ({plural(need, 'Game')})" if need else "Get Artwork",
                              "ghost" if ready else "go", bool(need) and not self.busy, self.fill_everything))
            if ready:
                specs.append((f"Apply ({plural(ready, 'Game')})", "go", not self.busy, self.apply_all))
            self.cbar.set_buttons(specs)
        if self.busy:
            self.statusbar.set_message("busy", self.progress_text)
        else:
            self.statusbar.set_message(None, "")

    def refresh_all(self):
        if not self.sfile or not self.sfile.shortcuts:
            self.update_chrome()
            return
        self.game_list.update_counts()
        self.game_list.refresh()
        self.start.refresh()
        if self.current >= 0:
            self.page.refresh_current()
            if self.tray.isVisible():
                self.tray.mark_in_use()
        self.update_chrome()

    # ---- navigation -------------------------------------------------------

    def go_home(self):
        if self.sfile and self.sfile.shortcuts:
            self.game_list.select(-1)
            self.show_index(-1)

    def open_game(self, idx: int):
        self.game_list.select(idx)
        self.show_index(idx)

    def show_index(self, idx: int):
        self.current = idx
        if not self.sfile or not self.sfile.shortcuts:
            return
        if idx < 0:
            self.tray.hide()
            self.stage.setCurrentWidget(self.start)
            self.start.refresh()
            self.update_chrome()
            return
        self.stage.setCurrentWidget(self.page)
        self.page.scroll.verticalScrollBar().setValue(0)
        self.page.show_game()
        self.load_options()
        if self.tray.isVisible():
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
            self.page.set_all_messages("This game wasn't found on SteamGridDB. Use “Search by Another Name” above.")
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

        workers.run(self.sgdb.match, sc.name, on_done=matched,
                    on_error=lambda m: (self.page.set_all_messages(m), self._key_problem(m)))

    def _record_match(self, sc: Shortcut, game: Game | None, candidates: int = 0, commit: bool = False):
        """Remember what SteamGridDB matched. Exact matches are saved; a best guess is
        kept in memory and tagged "Check match" until the user acts on it (or `commit`)."""
        if game is None:
            self.status[sc.appid] = "notfound"
            return
        exact = is_exact(sc.name, game.name)
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

            def got(assets, row=row, key=key):
                cur = self.current_shortcut()
                if cur is None or cur.appid != appid:
                    return
                if assets is None:
                    row.set_message("Couldn't reach SteamGridDB. Check your connection and open the game again.", "warning")
                else:
                    more = len(assets) >= 50
                    row.set_assets(assets, more)
                    if key == "grid":
                        self.page.update_match()

            self.fetch_assets(game.id, key, 0, got)

    def open_tray(self, slot: Slot):
        if self.current_game() is None:
            self.change_match() if self.config.api_key else self.ask_api_key()
            return
        self.tray.open_slot(slot)
        self.tray.list.setFocus()

    def _tray_preview(self, slot: Slot, pm):
        row = self.page.rows.get(slot.key)
        if row is None:
            return
        if pm is not None and not pm.isNull():
            backdrop = row.art.backdrop if slot.key == "logo" else None
            row.art.set_pixmap(pm, backdrop)
            row.caption.setText("Previewing")
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
        have = sum(self.coverage(sc))
        n = self.pending.count(sc.appid)
        menu = GameMenu(sc.name or "(unnamed)", "All 5 pieces" if have == 5 else f"{have} of 5 pieces",
                        self.icon_pixmap(sc), [
            Item("Get All Artwork", "download", lambda: self._run_fill([sc], single=True),
                 bool(self._unstaged_missing(sc)) and not self.busy),
            Item("Refresh Artwork", "refresh", lambda: self.refresh_options(sc), bool(self.match_info(sc))),
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
            if self.tray.isVisible():
                self.tray.open_slot(self.tray.slot)
        self.toast.show_message(f"Refreshed the SteamGridDB options for {sc.name}.")

    def remove_all(self, sc: Shortcut):
        if QMessageBox.question(
                self, APP_NAME, f"Remove all artwork from {sc.name}? You can undo it straight after.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.pending.drop(sc.appid)
        jobs = [(sc, k, Pick(None)) for k in ORDER if self.slot_path(sc, SLOTS[k]) is not None]
        done, errors = self._write(jobs, self.steam_is_running())
        self._after_write(done, errors, f"Removed all artwork from {sc.name}.")

    # ---- writing changes ------------------------------------------------------------

    def _write(self, jobs, defer_icons: bool):
        """Save or clear each (shortcut, slot key, Pick); returns the undo records and errors."""
        done, errors = [], []
        for sc, key, pick in jobs:
            slot = SLOTS[key]
            before = self.config.get_applied(sc.appid, key)
            defer = key == "icon" and defer_icons
            try:
                if pick.clears:
                    change = remove_asset(self.user.grid_dir, sc, slot, None if defer else self.sfile)
                else:
                    change = save_asset(self.user.grid_dir, sc, slot, pick.data, pick.asset.mime,
                                        None if defer else self.sfile)
            except Exception as e:
                errors.append(f"{sc.name} {slot.label.lower()}: {e}")
                continue
            if defer:
                self.config.set_pending_icon(sc.appid, "" if pick.clears else str(change.new_file))
            if pick.clears:
                self.config.set_applied(sc.appid, key, None)
            else:
                self.commit_match(sc)
                if self.status.get(sc.appid) == "check":
                    self.status.pop(sc.appid, None)
                self.config.set_applied(sc.appid, key, {"id": pick.asset.id, "author": pick.asset.author})
            self.pending.drop(sc.appid, key)
            self.pick_pix.pop((sc.appid, key), None)
            if change.reloaded:
                self._after_reload(sc.appid)
            done.append((change, before, defer))
        return done, errors

    def _after_write(self, done, errors, message: str):
        self.refresh_all()
        if self.current >= 0:
            self.page.show_game()
        if errors:
            self.last_errors = errors
            self._summary_notice(f"{plural(len(errors), 'piece')} couldn't be saved.")
        if not done:
            return

        def revert():
            for change, before, defer in reversed(done):
                try:
                    undo(change, self.sfile)
                except Exception as e:
                    self.toast.show_message(f"Couldn't undo: {e}")
                    return
                if defer:
                    self.config.set_pending_icon(change.shortcut.appid, None)
                self.config.set_applied(change.shortcut.appid, change.slot.key, before)
                if change.reloaded:
                    self._after_reload(change.shortcut.appid)
            self.refresh_all()
            if self.current >= 0:
                self.page.show_game()
            self.update_steam_state()
            self.toast.show_message("Put back the way it was.")

        self.toast.show_message(message, undo=revert)

    def apply_all(self):
        self.apply_games(self.pending.games())

    def apply_games(self, appids: list[int]):
        if self.busy or not self.sfile:
            return
        jobs = [(self.sfile.find(a), k, p) for a in appids for k, p in self.pending.for_game(a).items()]
        jobs = [j for j in jobs if j[0] is not None]
        if not jobs:
            return
        root = self.steam_root
        restart = self.steam_is_running() and self.auto_restart
        games = len({j[0].appid for j in jobs})
        self._set_busy("apply", "Closing Steam…" if restart else f"Applying artwork for {plural(games, 'game')}…")

        def work(progress):
            closed = False
            if restart:
                shutdown_steam(root)
                closed = wait_until_closed(root)
            todo = [p for _, _, p in jobs if not p.clears and p.data is None]
            for i, pick in enumerate(todo):
                progress(i)
                pick.data = self.sgdb.download(pick.asset.url)
            return closed

        def on_progress(i):
            total = sum(1 for _, _, p in jobs if not p.clears and p.data is None) or 1
            self.progress_text = f"Downloading artwork  {i + 1} of {total}"
            self.update_chrome()

        def done(closed):
            running = self.steam_is_running()
            written, errors = self._write(jobs, running)
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
            self._after_write(written, errors, f"Applied new artwork to {plural(games, 'game')}.{note}")
            if self._close_after_apply:
                self.close()

        def failed(msg):
            self._set_busy("")
            self._close_after_apply = False
            if restart:
                start_steam(root)
            self.toast.show_message(f"Couldn't apply the artwork: {msg}")

        workers.run(work, on_progress=on_progress, on_done=done, on_error=failed)

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
        if not sc or not self.ensure_key():
            return
        dlg = MatchDialog(self, self.sgdb, sc.name, sc.exe)
        if dlg.exec() and dlg.selected():
            g = dlg.selected()
            old = self.match_info(sc)
            self.config.set_match(sc.appid, g.id, g.name, g.year)
            self.guesses.pop(sc.appid, None)
            self.status.pop(sc.appid, None)
            if not old or old.get("id") != g.id:
                self.pending.drop(sc.appid)  # picks from the other game no longer fit
            self.page.update_match()
            self.load_options()
            self.refresh_all()
            if self.tray.isVisible():
                self.tray.open_slot(self.tray.slot)

    # ---- getting artwork in bulk ---------------------------------------------

    def _fetch_missing(self, jobs, progress):
        """Worker: for each (shortcut, slots, saved match, single), match and download the top pick."""
        report = {"found": 0, "notfound": [], "gaps": 0, "errors": [], "unsure": 0}
        for i, (sc, slots, saved, single) in enumerate(jobs):
            progress(("step", i, sc))
            try:
                if saved:
                    game, n = Game(saved["id"], saved["name"], saved.get("year", "")), 0
                else:
                    game, n = self.sgdb.match(sc.name)
            except AuthError:
                raise
            except Exception as e:
                report["errors"].append(f"{sc.name}: {e}")
                continue
            progress(("match", sc, game, n, bool(saved)))
            if game is None:
                report["notfound"].append(sc.name)
                continue
            if not saved and not single and not is_exact(sc.name, game.name):
                report["unsure"] += 1  # a best guess: let the user check it before fetching art
                continue
            got = False
            for slot in slots:
                try:
                    assets = self.sgdb.assets(slot.key, game.id)
                    if not assets:
                        report["gaps"] += 1
                        continue
                    data = self.sgdb.download(assets[0].url)
                    progress(("asset", sc, slot, data, assets[0]))
                    got = True
                except AuthError:
                    raise
                except Exception as e:
                    report["errors"].append(f"{sc.name} {slot.label.lower()}: {e}")
            report["found"] += got
        return report

    def _run_fill(self, shortcuts: list[Shortcut], single: bool = False):
        if self.busy or not self.ensure_key():
            return
        jobs = [(sc, self._unstaged_missing(sc), self.match_info(sc), single) for sc in shortcuts]
        skipped = sum(1 for j in jobs if j[1] and self.status.get(j[0].appid) == "notfound" and not single)
        jobs = [j for j in jobs if j[1] and (single or self.status.get(j[0].appid) != "notfound")]
        if not jobs:
            self.toast.show_message("Nothing to fill in." if not skipped else
                                    f"Nothing to fill in: {plural(skipped, 'game')} not found on SteamGridDB.")
            return
        total = len(jobs)
        self.sgdb.on_wait = lambda secs: self._progress(f"SteamGridDB asked us to wait {int(secs)} s…")
        self._set_busy("fetch", f"Getting artwork  1 of {plural(total, 'game')}")

        def on_progress(ev):
            kind = ev[0]
            if kind == "step":
                _, i, sc = ev
                self._progress(f"Getting artwork  {i + 1} of {plural(total, 'game')}")
                self.status[sc.appid] = "getting"
                self.game_list.refresh()
            elif kind == "match":
                _, sc, game, n, saved = ev
                self.status.pop(sc.appid, None)
                if not saved:
                    self._record_match(sc, game, n, commit=single)
                if self.current_shortcut() is sc:
                    self.page.update_match()
                self.game_list.refresh()
            elif kind == "asset":
                _, sc, slot, data, asset = ev
                self._stage(sc, slot.key, Pick(asset, data))
                self.refresh_all()

        def done(report):
            self._set_busy("")
            self.set_connection("connected")
            self.last_errors = list(report["errors"])
            for sc, *_ in jobs:
                if self.status.get(sc.appid) == "getting":
                    self.status.pop(sc.appid, None)
            self.refresh_all()
            if self.current >= 0:
                self.load_options()
            if single:
                sc = jobs[0][0]
                n = self.pending.count(sc.appid)
                msg = (f"Found {plural(n, 'piece')} of artwork for {sc.name}, ready to apply." if n
                       else f"SteamGridDB has nothing new for {sc.name} yet.")
                self.toast.show_message(msg)
                if not report["errors"]:
                    return
            parts = [f"Found artwork for {plural(report['found'], 'game')}, ready to apply."]
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
            self._summary_notice(" ".join(parts))

        def failed(msg):
            self._set_busy("")
            self._key_problem(msg)
            for sc, *_ in jobs:
                if self.status.get(sc.appid) == "getting":
                    self.status.pop(sc.appid, None)
            self.refresh_all()
            QMessageBox.warning(self, APP_NAME, msg)

        workers.run(self._fetch_missing, jobs, on_progress=on_progress, on_done=done, on_error=failed)

    def fill_current(self):
        sc = self.current_shortcut()
        if sc:
            if self.status.get(sc.appid) == "notfound":
                self.status.pop(sc.appid, None)
            self._run_fill([sc], single=True)

    def fill_everything(self):
        if self.sfile:
            self._run_fill(list(self.sfile.shortcuts))

    def _progress(self, text: str):
        self.progress_text = text
        if self.busy:
            self.statusbar.set_message("busy", text)

    def _set_busy(self, busy: str, text: str = ""):
        self.busy = busy
        self.progress_text = text
        self.header.setEnabled(not busy)
        self.update_chrome()

    def _summary_notice(self, text: str):
        def dismiss():
            self.notice.hide()

        actions = [("Dismiss", dismiss)]
        if self.last_errors:
            actions.insert(0, ("Show details", lambda: QMessageBox.information(
                self, APP_NAME, "These couldn't be downloaded:\n\n" + "\n".join(self.last_errors[:40]))))
        if any(v in ("notfound", "check") for v in self.status.values()):
            actions.insert(0, ("Show games to check", lambda: self.game_list.set_filter("check")))
        self.notice.show_message("info", text, actions)

    # ---- menu actions -----------------------------------------------------

    def restore_backup(self):
        if not self.sfile or self.busy:
            return
        running = self.steam_is_running()
        dlg = RestoreDialog(self, self.sfile.backups(), self.sfile.shortcuts, running)
        if not dlg.exec() or not dlg.selected():
            return
        path = dlg.selected()
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
            self._restore(path)

        workers.run(work, on_done=done, on_error=lambda m: (self._set_busy(""), self.toast.show_message(m)))

    def _restore(self, path: Path):
        try:
            self.sfile.restore(path)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't restore that copy: {e}")
            return
        self.reload_shortcuts()
        self.toast.show_message("Game list restored. The list you had before is saved as a copy too.")

    def open_grid_folder(self):
        if self.user:
            self.user.grid_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.user.grid_dir)))

    def about(self):
        QMessageBox.about(
            self, APP_NAME,
            f"{APP_NAME} {__version__}<br><br>Artwork comes from "
            "<a href='https://www.steamgriddb.com'>SteamGridDB</a> and its artists.<br>"
            "Not affiliated with Valve or SteamGridDB.")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.tray.isVisible():
            self.tray.close_tray()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        if self.busy == "apply":
            self._close_after_apply = True
            e.ignore()
            return
        if self.pending and not self._close_after_apply:
            choice = "apply" if self.apply_on_quit else self._ask_on_quit()
            if choice is None:
                e.ignore()
                return
            if choice == "apply":
                self._close_after_apply = True
                e.ignore()
                self.apply_all()
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
