"""The main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from .. import artwork
from ..artwork import SLOTS, Slot, current_icon, existing, prune_replaced, remove_asset, save_asset, undo
from ..config import Config
from ..sgdb import Asset, AuthError, Game, SteamGridDB, clean_name
from ..shortcuts import Shortcut, ShortcutsChanged, ShortcutsFile
from ..steam import SteamUser, find_steam_roots, list_users, steam_running
from . import icons, theme, workers
from .dialogs import ApiKeyDialog, MatchDialog, RestoreDialog
from .gamepage import GamePage
from .library import GameList, StartPage
from .tray import ROLE_PIX, OptionTray
from .widgets import ArtView, IconLabel, Notice, Toast, label, load_pixmap

ORDER = ("grid", "wide", "hero", "logo", "icon")


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
        self.sfile: ShortcutsFile | None = None
        self.status: dict[int, str] = {}  # appid -> "notfound" | "check" | "getting"
        self.guesses: dict[int, Game] = {}  # best guesses not yet confirmed by an action
        self.asset_cache: dict[tuple, list[Asset]] = {}
        self.last_errors: list[str] = []
        self.current = -1
        self.busy = False
        self.cancel_fetch = False
        self.notice_kind = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        self.notice = Notice()
        outer.addWidget(self.notice)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.pages.addWidget(self._full_message(
            "Couldn't find Steam",
            "Is Steam installed, and have you signed in to it at least once?",
            "Choose Steam folder…", self.choose_steam_folder))
        self.pages.addWidget(self._full_message(
            "No non-Steam games yet",
            "Add one in Steam with Games → Add a Non-Steam Game to My Library, then come back here.",
            "Check again", self.reload_shortcuts))

        self.split = QSplitter()
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        self.game_list = GameList(self)
        self.game_list.setMinimumWidth(260)
        self.game_list.picked.connect(self.show_index)
        self.split.addWidget(self.game_list)
        self.stage = QStackedWidget()
        self.stage.setObjectName("stage")
        self.stage.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.start = StartPage(self)
        self.start.picked.connect(lambda i: self.game_list.select(i))
        self.page = GamePage(self)
        self.page.fill.connect(self.fill_current)
        self.page.change_match.connect(self.change_match)
        self.stage.addWidget(self.start)
        self.stage.addWidget(self.page)
        self.split.addWidget(self.stage)
        self.tray = OptionTray(self)
        self.tray.hide()
        self.tray.use.connect(self.apply_option)
        self.tray.remove.connect(self.remove_art)
        self.tray.preview.connect(self._tray_preview)
        self.split.addWidget(self.tray)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([300, 980, 0])
        self.pages.addWidget(self.split)

        self.toast = Toast(central)
        self.steam_timer = QTimer(self, interval=5000, timeout=self.update_notice)
        self.steam_timer.start()
        for keys, fn in ((QKeySequence.StandardKey.Find, lambda: self.game_list.search.setFocus()),
                         (QKeySequence.StandardKey.Undo, self.toast.undo_now),
                         (QKeySequence.StandardKey.Refresh, self.reload_shortcuts)):
            act = QAction(self)
            act.setShortcut(keys)
            act.triggered.connect(fn)
            self.addAction(act)
        self.detect_steam()

    # ---- layout pieces ----------------------------------------------------

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("header")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 10, 16, 10)
        lay.setSpacing(14)
        mark = IconLabel("app", 32)
        lay.addWidget(mark)
        lay.addWidget(label(APP_NAME, "appname"))
        lay.addSpacing(10)
        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self._account_changed)
        lay.addWidget(self.account)
        self.account_label = label("", "muted")
        lay.addWidget(self.account_label)
        self.summary = label("", "muted")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.summary, 3)
        lay.addStretch(1)
        self.done_note = QWidget()
        dn = QHBoxLayout(self.done_note)
        dn.setContentsMargins(0, 0, 0, 0)
        dn.setSpacing(8)
        dn.addWidget(IconLabel("check", 18, theme.C["blue_ink"]))
        dn.addWidget(label("Every game has all its artwork", "muted"), 1)
        lay.addWidget(self.done_note, 2)
        self.progress_label = label("", "muted")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.progress_label, 2)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        lay.addWidget(self.cancel_btn)
        self.fill_btn = QPushButton()
        self.fill_btn.setProperty("kind", "go")
        self.fill_btn.clicked.connect(self.fill_everything)
        lay.addWidget(self.fill_btn)
        more = QToolButton()
        more.setIcon(icons.icon("more", 22, theme.C["text"]))
        more.setIconSize(QSize(22, 22))
        more.setToolTip("More")
        more.setAccessibleName("More actions")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        for text, fn in (
            ("SteamGridDB API key…", self.ask_api_key),
            ("Restore a backup…", self.restore_backup),
            ("Open the artwork folder", self.open_grid_folder),
            (None, None),
            ("Check for changes", self.reload_shortcuts),
            ("Choose Steam folder…", self.choose_steam_folder),
            (None, None),
            ("About", self.about),
        ):
            if text is None:
                menu.addSeparator()
            else:
                menu.addAction(QAction(text, menu, triggered=fn))
        more.setMenu(menu)
        lay.addWidget(more)
        for w in (self.summary, self.progress_label):
            # Let these shrink rather than force the window wider than the Deck's screen.
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            w.setMinimumWidth(60)
        self.done_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary.setMinimumWidth(120)
        for w in (self.done_note, self.progress_label, self.cancel_btn, self.fill_btn, self.account, self.account_label):
            w.hide()
        return bar

    def _full_message(self, title, text, button, fn) -> QWidget:
        w = QWidget()
        w.setObjectName("stage")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        t = label(title, "title")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        m = label(text, "muted", wrap=True)
        m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(m)
        lay.addSpacing(12)
        b = QPushButton(button)
        b.setProperty("kind", "go")
        b.clicked.connect(fn)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(2)
        return w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.centralWidget().width() - self.toast.width()) // 2,
                            self.centralWidget().height() - self.toast.height() - 28)

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
            callback(assets)

        def failed(msg):
            self._network_problem(msg)
            callback(None)

        workers.run(self.sgdb.assets, slot_key, game_id, page, on_done=done, on_error=failed)

    # ---- Steam & shortcuts -------------------------------------------------

    def detect_steam(self, roots: list[Path] | None = None):
        roots = roots if roots is not None else find_steam_roots()
        self.users = [u for r in roots for u in list_users(r)]
        self.account.blockSignals(True)
        self.account.clear()
        for u in self.users:
            self.account.addItem(u.label, u)
        self.account.blockSignals(False)
        many = len(self.users) > 1
        self.account.setVisible(many)
        self.account_label.setVisible(len(self.users) == 1)
        if not self.users:
            self.user = None
            self.sfile = None
            self.current = -1
            self.pages.setCurrentIndex(0)
            self.update_header()
            return
        self.account_label.setText(self.users[0].label)
        self._account_changed(0)

    def choose_steam_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose your Steam folder", str(Path.home()))
        if d:
            roots = find_steam_roots([Path(d)])
            if not roots:
                QMessageBox.warning(self, APP_NAME, "That folder doesn't have a Steam “userdata” folder inside it.")
                return
            self.detect_steam(roots)

    def _account_changed(self, idx: int):
        if 0 <= idx < len(self.users):
            self.user = self.users[idx]
            self.reload_shortcuts()

    def reload_shortcuts(self):
        if not self.user:
            return self.detect_steam()
        try:
            self.sfile = ShortcutsFile(self.user.shortcuts_path)
        except Exception as e:
            QMessageBox.critical(
                self, APP_NAME,
                f"Steam's shortcut list couldn't be read:\n{e}\n\nYou can restore a backup from the ⋯ menu.")
            self.sfile = None
            self.pages.setCurrentIndex(1)
            return
        self.update_notice()
        if not self.sfile.shortcuts:
            self.pages.setCurrentIndex(1)
            self.update_header()
            return
        self.pages.setCurrentIndex(2)
        if self.current >= len(self.sfile.shortcuts):
            self.current = -1
        self.game_list.rebuild()
        self.start.rebuild()
        self.update_header()
        workers.run(prune_replaced, self.user.grid_dir)
        self.apply_pending_icons()

    def _after_reload(self, appid: int):
        """The shortcut list was re-read from disk mid-change: rebuild what indexes into it."""
        self.current = next((i for i, s in enumerate(self.sfile.shortcuts) if s.appid == appid), -1)
        self.game_list.rebuild()
        self.start.rebuild()
        self.toast.show_message("Steam had changed the game list; it was re-read and your change kept.")

    # ---- icons while Steam is running ---------------------------------------

    def steam_is_running(self) -> bool:
        return bool(self.user and steam_running(self.user.userdata.parent.parent))

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
            self.toast.show_message(f"{linked} icon{'s' if linked != 1 else ''} linked now that Steam has closed.")

    def update_header(self):
        if not self.sfile or not self.sfile.shortcuts:
            self.summary.setText("")
            self.fill_btn.hide()
            return
        n = len(self.sfile.shortcuts)
        need = sum(1 for sc in self.sfile.shortcuts if not all(self.coverage(sc)))
        self.summary.setText(f"<b>{n}</b> non-Steam game{'s' if n != 1 else ''} · <b>{need}</b> missing artwork")
        if self.busy:
            return
        self.fill_btn.setText(f"Get artwork for {need} game{'s' if need != 1 else ''}")
        self.fill_btn.setVisible(need > 0)
        self.done_note.setVisible(need == 0)

    def update_notice(self):
        running = self.steam_is_running()
        if not running:
            self.apply_pending_icons()
        if self.notice_kind == "summary":
            return
        if running:
            n = len(self.config.pending_icons)
            text = ("Steam is running. New artwork shows up after Steam restarts. Icons are saved now "
                    "and linked to their games when Steam closes" + (f" ({n} waiting)." if n else "."))
            if self.notice_kind != "steam" or self.notice.text.text() != text:
                self.notice_kind = "steam"
                self.notice.show_message("warn", text)
        elif self.notice_kind == "steam":
            self.notice_kind = None
            self.notice.hide()

    def _summary_notice(self, text: str):
        self.notice_kind = "summary"

        def dismiss():
            self.notice_kind = None
            self.notice.hide()
            self.update_notice()

        actions = [("Dismiss", dismiss)]
        if self.last_errors:
            actions.insert(0, ("Show details", lambda: QMessageBox.information(
                self, APP_NAME, "These couldn't be downloaded:\n\n" + "\n".join(self.last_errors[:40]))))
        if any(v in ("notfound", "check") for v in self.status.values()):
            actions.insert(0, ("Show games to check", lambda: (self.game_list.filter_buttons["check"].click())))
        self.notice.show_message("info", text, actions)

    def _network_problem(self, msg: str):
        self.toast.show_message(msg)

    def refresh_all(self):
        self.game_list.update_counts()
        self.game_list.refresh()
        self.start.refresh()
        self.update_header()
        if self.current >= 0:
            self.page.refresh_current()
            if self.tray.isVisible():
                self.tray.mark_in_use()

    # ---- navigation -------------------------------------------------------

    def show_index(self, idx: int):
        self.current = idx
        if idx < 0 or not self.sfile:
            self.tray.hide()
            self.stage.setCurrentWidget(self.start)
            self.start.refresh()
            return
        self.stage.setCurrentWidget(self.page)
        self.page.scroll.verticalScrollBar().setValue(0)
        self.page.show_game()
        self.load_options()
        if self.tray.isVisible():
            self.tray.open_slot(self.tray.slot)

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
            self.page.set_all_messages("This game wasn't found on SteamGridDB. Use “Search by another name” above.")
            return
        self.page.set_all_messages("Finding this game on SteamGridDB…")
        appid = sc.appid

        def matched(game):
            self._record_match(sc, game)
            if self.current_shortcut() is not None and self.current_shortcut().appid == appid:
                self.page.update_match()
                self.load_options()
            self.refresh_all()

        workers.run(self.sgdb.best_match, sc.name, on_done=matched,
                    on_error=lambda m: (self.page.set_all_messages(m), self._key_problem(m)))

    def _record_match(self, sc: Shortcut, game: Game | None, commit: bool = False):
        """Remember what SteamGridDB matched. Exact matches are saved; a best guess is
        kept in memory and tagged "Check match" until the user acts on it (or `commit`)."""
        if game is None:
            self.status[sc.appid] = "notfound"
            return
        exact = clean_name(game.name).casefold() == clean_name(sc.name).casefold()
        if self.config.get_match(sc.appid):
            pass
        elif exact or commit:
            self.config.set_match(sc.appid, game.id, game.name, game.year)
        else:
            self.guesses[sc.appid] = game
        if exact:
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
                    row.set_message("Couldn't reach SteamGridDB. Check your connection and open the game again.")
                else:
                    more = len(assets) >= 50
                    row.set_assets(assets, more)

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

    # ---- changing artwork -------------------------------------------------

    def apply_option(self, slot: Slot, asset: Asset):
        sc = self.current_shortcut()
        if not sc:
            return
        if self.busy:
            self.toast.show_message("Wait for the current download to finish, or cancel it.")
            return
        self.toast.show_message(f"Saving {slot.label.lower()}…", ms=20000)

        def done(data):
            self._save(sc, slot, data, asset, toast=True)

        workers.run(self.sgdb.download, asset.url, on_done=done,
                    on_error=lambda m: self.toast.show_message(f"Couldn't download it: {m}"))

    def _save(self, sc: Shortcut, slot: Slot, data: bytes, asset: Asset, toast: bool):
        before = self.config.get_applied(sc.appid, slot.key)
        # Steam rewrites shortcuts.vdf when it exits, so while it runs the icon file is
        # saved but the link into the list waits (see apply_pending_icons).
        defer = slot.key == "icon" and self.steam_is_running()
        try:
            change = save_asset(self.user.grid_dir, sc, slot, data, asset.mime, None if defer else self.sfile)
        except Exception as e:
            self.toast.show_message(f"Couldn't save the {slot.label.lower()}: {e}")
            return
        if defer:
            self.config.set_pending_icon(sc.appid, str(change.new_file))
            self.update_notice()
        self.commit_match(sc)
        self.config.set_applied(sc.appid, slot.key, {"id": asset.id, "author": asset.author})
        if change.reloaded:
            self._after_reload(sc.appid)
        self.refresh_all()
        if toast:
            def revert():
                try:
                    undo(change, self.sfile)
                except Exception as e:
                    self.toast.show_message(f"Couldn't undo: {e}")
                    return
                if defer:
                    self.config.set_pending_icon(sc.appid, None)
                    self.update_notice()
                self.config.set_applied(sc.appid, slot.key, before)
                if change.reloaded:
                    self._after_reload(sc.appid)
                self.refresh_all()
                self.toast.show_message(f"{slot.label} put back.")

            note = " It links to the game when Steam closes." if defer else ""
            self.toast.show_message(f"{slot.label} updated for {sc.name}.{note}", undo=revert)

    def remove_art(self, slot: Slot):
        sc = self.current_shortcut()
        if not sc:
            return
        before = self.config.get_applied(sc.appid, slot.key)
        defer = slot.key == "icon" and self.steam_is_running()
        try:
            change = remove_asset(self.user.grid_dir, sc, slot, None if defer else self.sfile)
        except Exception as e:
            self.toast.show_message(f"Couldn't remove it: {e}")
            return
        if defer:
            self.config.set_pending_icon(sc.appid, "")
            self.update_notice()
        self.config.set_applied(sc.appid, slot.key, None)
        if change.reloaded:
            self._after_reload(sc.appid)
        self.refresh_all()

        def revert():
            try:
                undo(change, self.sfile)
            except Exception as e:
                self.toast.show_message(f"Couldn't undo: {e}")
                return
            if defer:
                self.config.set_pending_icon(sc.appid, None)
                self.update_notice()
            self.config.set_applied(sc.appid, slot.key, before)
            if change.reloaded:
                self._after_reload(sc.appid)
            self.refresh_all()

        self.toast.show_message(f"{slot.label} removed.", undo=revert)

    # ---- SteamGridDB key and matching ---------------------------------------

    def ensure_key(self) -> bool:
        return bool(self.config.api_key) or self.ask_api_key()

    def ask_api_key(self) -> bool:
        dlg = ApiKeyDialog(self, self.config.api_key)
        if dlg.exec():
            self.config.api_key = dlg.key()
            self.sgdb.api_key = self.config.api_key
            self.asset_cache.clear()
            if self.current >= 0:
                self.load_options()
            return True
        return False

    def _key_problem(self, msg: str):
        if "API key" in msg:
            self.toast.show_message(msg)

    def change_match(self):
        sc = self.current_shortcut()
        if not sc or not self.ensure_key():
            return
        dlg = MatchDialog(self, self.sgdb, sc.name)
        if dlg.exec() and dlg.selected():
            g = dlg.selected()
            self.config.set_match(sc.appid, g.id, g.name, g.year)
            self.guesses.pop(sc.appid, None)
            self.status.pop(sc.appid, None)
            self.page.update_match()
            self.load_options()
            self.refresh_all()
            if self.tray.isVisible():
                self.tray.open_slot(self.tray.slot)

    # ---- filling gaps in bulk ---------------------------------------------

    def _missing_slots(self, sc: Shortcut) -> list[Slot]:
        return [SLOTS[k] for k, have in zip(ORDER, self.coverage(sc)) if not have]

    def _fetch_missing(self, jobs, progress):
        """Worker: for each (shortcut, slots), match and download the top pick."""
        report = {"updated": 0, "notfound": [], "gaps": 0, "errors": []}
        for i, (sc, slots, saved) in enumerate(jobs):
            if self.cancel_fetch:
                break
            progress(("step", i, sc))
            try:
                game = Game(saved["id"], saved["name"]) if saved else self.sgdb.best_match(sc.name)
            except AuthError:
                raise
            except Exception as e:
                report["errors"].append(f"{sc.name}: {e}")
                continue
            progress(("match", sc, game, bool(saved)))
            if game is None:
                report["notfound"].append(sc.name)
                continue
            got = False
            for slot in slots:
                if self.cancel_fetch:
                    break
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
            report["updated"] += got
        return report

    def _run_fill(self, shortcuts: list[Shortcut]):
        if self.busy or not self.ensure_key():
            return
        jobs = [(sc, self._missing_slots(sc), self.match_info(sc)) for sc in shortcuts]
        skipped = sum(1 for j in jobs if j[1] and self.status.get(j[0].appid) == "notfound")
        jobs = [j for j in jobs if j[1] and self.status.get(j[0].appid) != "notfound"]
        if not jobs:
            self.toast.show_message("Nothing to fill in. Click any artwork to swap it." if not skipped else
                                    f"Nothing to fill in: {skipped} game{'s' if skipped != 1 else ''} not found on SteamGridDB.")
            return
        self.sgdb.on_wait = lambda secs: self.progress_label.setText(f"SteamGridDB asked us to wait {int(secs)} s…")
        self._set_busy(True)
        total = len(jobs)

        def on_progress(ev):
            kind = ev[0]
            if kind == "step":
                _, i, sc = ev
                self.progress_label.setText(f"Getting artwork…  {i + 1} of {total}")
                self.status[sc.appid] = "getting"
                self.game_list.refresh()
            elif kind == "match":
                _, sc, game, saved = ev
                self.status.pop(sc.appid, None)
                if saved:
                    self.commit_match(sc)
                else:
                    self._record_match(sc, game, commit=True)
                if self.current_shortcut() is sc:
                    self.page.update_match()
                self.game_list.refresh()
            elif kind == "asset":
                _, sc, slot, data, asset = ev
                self._save(sc, slot, data, asset, toast=False)

        def done(report):
            self._set_busy(False)
            self.last_errors = list(report["errors"])
            for sc, *_ in jobs:
                if self.status.get(sc.appid) == "getting":
                    self.status.pop(sc.appid, None)
            self.refresh_all()
            if self.current >= 0:
                self.load_options()
            parts = [f"{report['updated']} game{'s' if report['updated'] != 1 else ''} updated."]
            if report["notfound"]:
                parts.append(f"{len(report['notfound'])} not found on SteamGridDB.")
            checks = sum(1 for v in self.status.values() if v == "check")
            if checks:
                parts.append(f"{checks} matched by best guess, worth a quick check.")
            if report["gaps"]:
                parts.append(f"{report['gaps']} piece{'s' if report['gaps'] != 1 else ''} had no options yet.")
            if report["errors"]:
                parts.append(f"{len(report['errors'])} couldn't be downloaded.")
            if skipped:
                parts.append(f"{skipped} skipped (not found before).")
            pend = len(self.config.pending_icons)
            if pend:
                parts.append(f"{pend} icon{'s' if pend != 1 else ''} will be linked when Steam closes.")
            if self.cancel_fetch:
                parts.insert(0, "Stopped.")
            parts.append("Restart Steam to see the new artwork.")
            self._summary_notice(" ".join(parts))

        def failed(msg):
            self._set_busy(False)
            for sc, *_ in jobs:
                if self.status.get(sc.appid) == "getting":
                    self.status.pop(sc.appid, None)
            self.refresh_all()
            QMessageBox.warning(self, APP_NAME, msg)

        self.cancel_fetch = False
        workers.run(self._fetch_missing, jobs, on_progress=on_progress, on_done=done, on_error=failed)

    def fill_current(self):
        sc = self.current_shortcut()
        if sc:
            if self.status.get(sc.appid) == "notfound":
                self.status.pop(sc.appid, None)
            self._run_fill([sc])

    def fill_everything(self):
        if self.sfile:
            self._run_fill(list(self.sfile.shortcuts))

    def _cancel(self):
        self.cancel_fetch = True
        self.progress_label.setText("Stopping…")

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.fill_btn.setVisible(not busy)
        self.progress_label.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.done_note.hide()
        self.account.setEnabled(not busy)
        self.page.fill_btn.setEnabled(not busy)
        if not busy:
            self.update_header()

    # ---- menu actions -----------------------------------------------------

    def restore_backup(self):
        if not self.sfile:
            return
        dlg = RestoreDialog(self, self.sfile.backups())
        if dlg.exec() and dlg.selected():
            if self.steam_is_running():
                if QMessageBox.question(
                        self, APP_NAME, "Steam is running and may overwrite the restored list when it "
                        "closes. Restore anyway?") != QMessageBox.StandardButton.Yes:
                    return
            try:
                self.sfile.restore(dlg.selected())
            except Exception as e:
                QMessageBox.warning(self, APP_NAME, f"Couldn't restore that backup: {e}")
                return
            self.reload_shortcuts()
            self.toast.show_message("Backup restored. The list you had before is saved as a backup too.")

    def open_grid_folder(self):
        if self.user:
            self.user.grid_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.user.grid_dir)))

    def about(self):
        QMessageBox.about(
            self, APP_NAME,
            f"<b>{APP_NAME}</b> {__version__}<br><br>Artwork comes from "
            "<a href='https://www.steamgriddb.com'>SteamGridDB</a> and its artists.<br>"
            "Not affiliated with Valve or SteamGridDB.")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.tray.isVisible():
            self.tray.close_tray()
        else:
            super().keyPressEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("io.github.acidtwin.SteamArtworkForNonSteamGames")
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(icons.app_icon())
    win = MainWindow()
    screen = app.primaryScreen().availableGeometry() if app.primaryScreen() else None
    if screen is not None and (screen.width() <= 1280 or screen.height() <= 800):
        win.showMaximized()  # Steam Deck: the whole work area, under the panel
    else:
        if screen is not None:
            win.resize(min(1280, screen.width() - 80), min(800, screen.height() - 80))
        win.show()
    win.game_list.list.setFocus()
    return app.exec()
