"""The main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..artwork import SLOTS, Slot, current_icon, existing, save_asset
from ..config import Config
from ..sgdb import AuthError, Game, SteamGridDB
from ..shortcuts import Shortcut, ShortcutsFile
from ..steam import SteamUser, find_steam_roots, list_users, steam_running
from . import workers

API_KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"


def pixmap_from(data: bytes | None = None, path: Path | None = None) -> QPixmap:
    pm = QPixmap()
    if path is not None:
        pm.load(str(path))
    elif data:
        pm.loadFromData(data)
    return pm


class ApiKeyDialog(QDialog):
    def __init__(self, parent, current: str):
        super().__init__(parent)
        self.setWindowTitle("SteamGridDB API key")
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Artwork comes from SteamGridDB. Paste your free API key below.<br>"
            f'You can get one at <a href="{API_KEY_URL}">steamgriddb.com</a> '
            "(sign in, then Preferences → API)."
        )
        intro.setOpenExternalLinks(True)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.edit = QLineEdit(current)
        self.edit.setPlaceholderText("API key")
        self.edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        lay.addWidget(self.edit)
        self.status = QLabel()
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._check)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.buttons = bb

    def _check(self):
        key = self.edit.text().strip()
        if not key:
            self.status.setText("Please paste a key first.")
            return
        self.status.setText("Checking key…")
        self.buttons.setEnabled(False)

        def done(ok):
            self.buttons.setEnabled(True)
            if ok:
                self.accept()
            else:
                self.status.setText("SteamGridDB didn't accept that key.")

        def failed(msg):
            self.buttons.setEnabled(True)
            self.status.setText(msg)

        workers.run(SteamGridDB(key).check_key, on_done=done, on_error=failed)

    def key(self) -> str:
        return self.edit.text().strip()


class MatchDialog(QDialog):
    """Search SteamGridDB and pick which game a shortcut is."""

    def __init__(self, parent, sgdb: SteamGridDB, name: str):
        super().__init__(parent)
        self.sgdb = sgdb
        self.setWindowTitle(f"Which game is “{name}”?")
        self.resize(460, 420)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.edit = QLineEdit(name)
        self.edit.returnPressed.connect(self.search)
        btn = QPushButton("Search")
        btn.clicked.connect(self.search)
        row.addWidget(self.edit)
        row.addWidget(btn)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list)
        self.status = QLabel()
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.search()

    def search(self):
        self.status.setText("Searching…")
        self.list.clear()
        workers.run(self.sgdb.search, self.edit.text(), on_done=self._show, on_error=self.status.setText)

    def _show(self, games: list[Game]):
        self.status.setText("" if games else "No matches. Try a shorter name.")
        for g in games:
            label = g.name + (f" ({g.year})" if g.year else "") + ("  ✓" if g.verified else "")
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, g)
            self.list.addItem(it)
        if games:
            self.list.setCurrentRow(0)

    def selected(self) -> Game | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None


class PickerDialog(QDialog):
    """A grid of SteamGridDB options for one artwork slot."""

    def __init__(self, parent, sgdb: SteamGridDB, slot: Slot, game: Game):
        super().__init__(parent)
        self.sgdb, self.slot, self.game = sgdb, slot, game
        self.setWindowTitle(f"{slot.label} for {game.name}")
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSpacing(8)
        h = 180
        w = int(h * slot.aspect)
        if w > 320:
            w, h = 320, int(320 / slot.aspect)
        self.thumb = QSize(w, h)
        self.list.setIconSize(self.thumb)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.status = QLabel("Loading options…")
        row.addWidget(self.status, 1)
        self.more = QPushButton("Load more")
        self.more.clicked.connect(self.load)
        self.more.setEnabled(False)
        row.addWidget(self.more)
        lay.addLayout(row)
        bb = QDialogButtonBox()
        bb.addButton("Use this", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.page = 0
        self.load()

    def load(self):
        self.more.setEnabled(False)
        workers.run(
            self.sgdb.assets, self.slot.key, self.game.id, self.page,
            on_done=self._show, on_error=self.status.setText,
        )
        self.page += 1

    def _show(self, assets):
        if not assets and self.list.count() == 0:
            self.status.setText(f"SteamGridDB has no {self.slot.label.lower()} for this game yet.")
            return
        self.status.setText(f"{self.list.count() + len(assets)} options. Double-click to use one.")
        self.more.setEnabled(len(assets) >= 50)
        placeholder = QPixmap(self.thumb)
        placeholder.fill(Qt.GlobalColor.darkGray)
        for a in assets:
            it = QListWidgetItem(QIcon(placeholder), f"{a.width}×{a.height}")
            it.setToolTip(f"by {a.author or 'unknown'} · {a.style} · score {a.score}")
            it.setData(Qt.ItemDataRole.UserRole, a)
            it.setSizeHint(QSize(self.thumb.width() + 16, self.thumb.height() + 32))
            self.list.addItem(it)
            workers.run(
                workers.cached_download, a.thumb, self.sgdb.download,
                on_done=lambda data, it=it: self._set_thumb(it, data),
            )

    def _set_thumb(self, item, data):
        pm = pixmap_from(data)
        if not pm.isNull() and self.isVisible():
            item.setIcon(QIcon(pm.scaled(self.thumb, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)))

    def selected(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None


class SlotCard(QFrame):
    def __init__(self, slot: Slot, on_choose, preview_height: int):
        super().__init__()
        self.slot = slot
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel(f"<b>{slot.label}</b>")
        top.addWidget(title, 1)
        self.button = QPushButton("Change…")
        self.button.clicked.connect(lambda: on_choose(slot))
        top.addWidget(self.button)
        lay.addLayout(top)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_size = QSize(int(preview_height * slot.aspect), preview_height)
        self.preview.setMinimumSize(min(self.preview_size.width(), 360), preview_height)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.preview, 1)

    def show_file(self, path: Path | None):
        if path is None:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Missing")
            self.button.setText("Choose…")
            return
        pm = pixmap_from(path=path)
        if pm.isNull():
            self.preview.setText(path.name)
        else:
            self.preview.setPixmap(pm.scaled(
                self.preview.size().boundedTo(self.preview_size).expandedTo(QSize(64, 64)),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.button.setText("Change…")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)  # Steam Deck screen
        self.config = Config()
        self.sgdb = SteamGridDB(self.config.api_key)
        self.users: list[SteamUser] = []
        self.user: SteamUser | None = None
        self.sfile: ShortcutsFile | None = None
        self.busy = False

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        bar = QHBoxLayout()
        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self._account_changed)
        bar.addWidget(QLabel("Steam account:"))
        bar.addWidget(self.account)
        bar.addStretch(1)
        self.fill_all = QPushButton("Get missing artwork for all games")
        self.fill_all.clicked.connect(self.fill_everything)
        bar.addWidget(self.fill_all)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload_shortcuts)
        bar.addWidget(refresh)
        settings = QPushButton("API key…")
        settings.clicked.connect(self.ask_api_key)
        bar.addWidget(settings)
        outer.addLayout(bar)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#5a4500;color:white;padding:6px;border-radius:4px;")
        self.banner.hide()
        outer.addWidget(self.banner)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)

        # Page 0: Steam not found
        nf = QWidget()
        nfl = QVBoxLayout(nf)
        nfl.addStretch(1)
        msg = QLabel("<h2>Couldn't find Steam</h2>Is Steam installed and have you signed in at least once?")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nfl.addWidget(msg)
        pick = QPushButton("Choose Steam folder…")
        pick.clicked.connect(self.choose_steam_folder)
        nfl.addWidget(pick, 0, Qt.AlignmentFlag.AlignCenter)
        nfl.addStretch(1)
        self.pages.addWidget(nf)

        # Page 1: games
        split = QSplitter()
        self.games = QListWidget()
        self.games.setIconSize(QSize(32, 32))
        self.games.currentRowChanged.connect(self.show_game)
        split.addWidget(self.games)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size:20px;font-weight:600;")
        head.addWidget(self.title, 1)
        self.fill_one = QPushButton("Get missing artwork")
        self.fill_one.clicked.connect(self.fill_current)
        head.addWidget(self.fill_one)
        dl.addLayout(head)
        mrow = QHBoxLayout()
        self.match_label = QLabel()
        mrow.addWidget(self.match_label, 1)
        self.match_btn = QPushButton("Wrong game?")
        self.match_btn.clicked.connect(self.change_match)
        mrow.addWidget(self.match_btn)
        dl.addLayout(mrow)

        grid = QGridLayout()
        self.cards = {
            "grid": SlotCard(SLOTS["grid"], self.choose_asset, 240),
            "wide": SlotCard(SLOTS["wide"], self.choose_asset, 110),
            "icon": SlotCard(SLOTS["icon"], self.choose_asset, 80),
            "hero": SlotCard(SLOTS["hero"], self.choose_asset, 100),
            "logo": SlotCard(SLOTS["logo"], self.choose_asset, 90),
        }
        grid.addWidget(self.cards["grid"], 0, 0, 3, 1)
        grid.addWidget(self.cards["wide"], 0, 1)
        grid.addWidget(self.cards["icon"], 0, 2)
        grid.addWidget(self.cards["hero"], 1, 1, 1, 2)
        grid.addWidget(self.cards["logo"], 2, 1, 1, 2)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 1)
        dl.addLayout(grid, 1)
        split.addWidget(detail)
        split.setSizes([300, 900])
        self.detail = detail
        self.pages.addWidget(split)

        # Page 2: no shortcuts
        empty = QLabel("<h2>No non-Steam games yet</h2>Add one in Steam with "
                       "<i>Games → Add a Non-Steam Game to My Library</i>, then press Refresh.")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages.addWidget(empty)

        self.progress = QProgressBar()
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        self.detect_steam()

    # ---- Steam & shortcuts -------------------------------------------------

    def detect_steam(self, roots: list[Path] | None = None):
        roots = roots if roots is not None else find_steam_roots()
        self.users = [u for r in roots for u in list_users(r)]
        self.account.blockSignals(True)
        self.account.clear()
        for u in self.users:
            self.account.addItem(u.label, u)
        self.account.blockSignals(False)
        self.account.setEnabled(len(self.users) > 1)
        if not self.users:
            self.pages.setCurrentIndex(0)
            return
        self._account_changed(0)

    def choose_steam_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose your Steam folder", str(Path.home()))
        if d:
            roots = find_steam_roots([Path(d)])
            if not roots:
                QMessageBox.warning(self, APP_NAME, "That folder doesn't have a Steam “userdata” folder inside it.")
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
            QMessageBox.critical(self, APP_NAME, f"Couldn't read your Steam shortcuts:\n{e}")
            self.sfile = None
            self.pages.setCurrentIndex(2)
            return
        self._update_banner()
        prev = self.games.currentRow()
        self.games.clear()
        if not self.sfile.shortcuts:
            self.pages.setCurrentIndex(2)
            return
        self.pages.setCurrentIndex(1)
        for sc in self.sfile.shortcuts:
            it = QListWidgetItem()
            self.games.addItem(it)
            self._refresh_item(it, sc)
        self.games.setCurrentRow(max(0, min(prev, self.games.count() - 1)))

    def _refresh_item(self, item: QListWidgetItem, sc: Shortcut):
        have = self._count_art(sc)
        item.setText(f"{sc.name}\n{have} of 5 artwork" if have < 5 else f"{sc.name}\nAll artwork set")
        icon = current_icon(sc, self.user.grid_dir)
        item.setIcon(QIcon(str(icon)) if icon else QIcon())

    def _count_art(self, sc: Shortcut) -> int:
        n = sum(1 for k, s in SLOTS.items() if k != "icon" and existing(self.user.grid_dir, sc.appid, s))
        return n + (1 if current_icon(sc, self.user.grid_dir) else 0)

    def _update_banner(self):
        if self.user and steam_running(self.user.userdata.parent.parent):
            self.banner.setText(
                "Steam is running. New artwork appears after you restart Steam. "
                "For icons, close Steam first so it doesn't overwrite the change when it exits.")
            self.banner.show()
        else:
            self.banner.hide()

    def current(self) -> Shortcut | None:
        row = self.games.currentRow()
        if self.sfile and 0 <= row < len(self.sfile.shortcuts):
            return self.sfile.shortcuts[row]
        return None

    def show_game(self, *_):
        sc = self.current()
        if not sc:
            return
        self.title.setText(sc.name or "(unnamed)")
        m = self.config.get_match(sc.appid)
        self.match_label.setText(f"SteamGridDB: {m['name']}" if m else "SteamGridDB: not matched yet")
        for key, card in self.cards.items():
            if key == "icon":
                card.show_file(current_icon(sc, self.user.grid_dir))
            else:
                card.show_file(existing(self.user.grid_dir, sc.appid, SLOTS[key]))

    def _refresh_current(self):
        row = self.games.currentRow()
        sc = self.current()
        if sc:
            self._refresh_item(self.games.item(row), sc)
            self.show_game()

    # ---- SteamGridDB ------------------------------------------------------

    def ensure_key(self) -> bool:
        if self.config.api_key:
            return True
        return self.ask_api_key()

    def ask_api_key(self) -> bool:
        dlg = ApiKeyDialog(self, self.config.api_key)
        if dlg.exec():
            self.config.api_key = dlg.key()
            self.sgdb.api_key = self.config.api_key
            return True
        return False

    def _match(self, sc: Shortcut) -> Game | None:
        """Runs in a worker: the saved match, or SteamGridDB's best guess."""
        m = self.config.get_match(sc.appid)
        if m:
            return Game(m["id"], m["name"])
        return self.sgdb.best_match(sc.name)

    def _remember(self, sc: Shortcut, game: Game | None):
        if game and not self.config.get_match(sc.appid):
            self.config.set_match(sc.appid, game.id, game.name)

    def change_match(self):
        sc = self.current()
        if not sc or not self.ensure_key():
            return
        dlg = MatchDialog(self, self.sgdb, sc.name)
        if dlg.exec() and dlg.selected():
            g = dlg.selected()
            self.config.set_match(sc.appid, g.id, g.name)
            self.show_game()

    def choose_asset(self, slot: Slot):
        sc = self.current()
        if not sc or not self.ensure_key():
            return
        self.statusBar().showMessage(f"Finding {sc.name} on SteamGridDB…")

        def matched(game):
            self.statusBar().clearMessage()
            if game is None:
                QMessageBox.information(self, APP_NAME, f"Couldn't find “{sc.name}” on SteamGridDB. "
                                        "Use “Wrong game?” to search for it by another name.")
                return
            self._remember(sc, game)
            self.show_game()
            dlg = PickerDialog(self, self.sgdb, slot, game)
            if dlg.exec() and dlg.selected():
                a = dlg.selected()
                self.statusBar().showMessage(f"Downloading {slot.label.lower()}…")
                workers.run(self.sgdb.download, a.url,
                            on_done=lambda data: self._apply(sc, slot, data, a.mime),
                            on_error=self._error)

        workers.run(self._match, sc, on_done=matched, on_error=self._error)

    def _apply(self, sc: Shortcut, slot: Slot, data: bytes, mime: str):
        try:
            save_asset(self.user.grid_dir, sc, slot, data, mime, self.sfile)
        except Exception as e:
            self._error(f"Couldn't save {slot.label.lower()}: {e}")
            return
        self.statusBar().showMessage(f"Saved {slot.label.lower()} for {sc.name}", 4000)
        for i, s in enumerate(self.sfile.shortcuts):
            if s is sc:
                self._refresh_item(self.games.item(i), sc)
        self.show_game()

    def _missing_slots(self, sc: Shortcut) -> list[Slot]:
        out = []
        for key, slot in SLOTS.items():
            have = current_icon(sc, self.user.grid_dir) if key == "icon" else existing(self.user.grid_dir, sc.appid, slot)
            if not have:
                out.append(slot)
        return out

    def _fetch_missing(self, jobs, progress):
        """Worker: for each (shortcut, slots), match and download the top pick."""
        notes = []
        for i, (sc, slots) in enumerate(jobs):
            progress(("step", i, sc.name))
            try:
                game = self._match(sc)
            except AuthError:
                raise
            except Exception as e:
                notes.append(f"{sc.name}: {e}")
                continue
            if game is None:
                notes.append(f"{sc.name}: not found on SteamGridDB")
                continue
            progress(("match", sc, game))
            for slot in slots:
                try:
                    assets = self.sgdb.assets(slot.key, game.id)
                    if not assets:
                        notes.append(f"{sc.name}: no {slot.label.lower()} available")
                        continue
                    data = self.sgdb.download(assets[0].url)
                    progress(("asset", sc, slot, data, assets[0].mime))
                except AuthError:
                    raise
                except Exception as e:
                    notes.append(f"{sc.name} {slot.label.lower()}: {e}")
        return notes

    def _run_fill(self, shortcuts: list[Shortcut]):
        if self.busy or not self.ensure_key():
            return
        jobs = [(sc, self._missing_slots(sc)) for sc in shortcuts]
        jobs = [j for j in jobs if j[1]]
        if not jobs:
            self.statusBar().showMessage("Nothing missing. Use Change… on any artwork to swap it.", 5000)
            return
        self._set_busy(True, len(jobs))

        def on_progress(ev):
            kind = ev[0]
            if kind == "step":
                self.progress.setValue(ev[1])
                self.statusBar().showMessage(f"Getting artwork for {ev[2]}…")
            elif kind == "match":
                self._remember(ev[1], ev[2])
            elif kind == "asset":
                _, sc, slot, data, mime = ev
                try:
                    save_asset(self.user.grid_dir, sc, slot, data, mime, self.sfile)
                except Exception as e:
                    self.statusBar().showMessage(f"Couldn't save {slot.label.lower()} for {sc.name}: {e}")
                for i, s in enumerate(self.sfile.shortcuts):
                    if s is sc:
                        self._refresh_item(self.games.item(i), sc)

        def done(notes):
            self._set_busy(False)
            self.show_game()
            self.statusBar().showMessage("Done. Restart Steam to see the new artwork.", 8000)
            if notes:
                QMessageBox.information(self, APP_NAME, "Finished, with a few gaps:\n\n" + "\n".join(notes[:30]))

        def failed(msg):
            self._set_busy(False)
            self._error(msg)

        workers.run(self._fetch_missing, jobs, on_progress=on_progress, on_done=done, on_error=failed)

    def fill_current(self):
        sc = self.current()
        if sc:
            self._run_fill([sc])

    def fill_everything(self):
        if self.sfile:
            self._run_fill(list(self.sfile.shortcuts))

    def _set_busy(self, busy: bool, total: int = 0):
        self.busy = busy
        for w in (self.fill_all, self.fill_one, self.detail, self.account):
            w.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, total)
        self.progress.setValue(0)

    def _error(self, msg: str):
        self.statusBar().clearMessage()
        QMessageBox.warning(self, APP_NAME, msg)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("steam-artwork-finder")
    # Finger-sized controls for the Steam Deck's touchscreen.
    app.setStyleSheet(
        "QPushButton { min-height: 36px; padding: 0 14px; }"
        "QComboBox, QLineEdit { min-height: 34px; }"
        "QListWidget::item { min-height: 44px; }"
    )
    win = MainWindow()
    win.show()
    if not win.config.api_key and win.users:
        win.ask_api_key()
    return app.exec()
