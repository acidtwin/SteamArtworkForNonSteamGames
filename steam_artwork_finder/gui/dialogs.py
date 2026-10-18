"""Dialogs: API key, game match, and restoring a backup."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..sgdb import Game, SteamGridDB
from . import workers

API_KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"


def _confirm(bb: QDialogButtonBox, button):
    b = bb.button(button)
    if b is not None:
        b.setProperty("kind", "confirm")


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
        _confirm(bb, QDialogButtonBox.StandardButton.Save)
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
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Use this game")
        _confirm(bb, QDialogButtonBox.StandardButton.Ok)
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



class RestoreDialog(QDialog):
    """Pick an earlier copy of Steam's shortcut list to put back."""

    def __init__(self, parent, backups):
        super().__init__(parent)
        self.setWindowTitle("Restore a backup")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "A copy of Steam's shortcut list (shortcuts.vdf) is saved before every change. "
            "Restoring one puts back the game names, launch options and icons it had then. "
            "Artwork images are not deleted. Close Steam before restoring."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.list = QListWidget()
        for path, count in backups:
            when = time.strftime("%d %b %Y, %H:%M:%S", time.localtime(path.stat().st_mtime))
            games = "unreadable" if count < 0 else f"{count} game{'s' if count != 1 else ''}"
            it = QListWidgetItem(f"{when}    ·    {games}")
            it.setData(Qt.ItemDataRole.UserRole, path)
            if count < 0:
                it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
        if not backups:
            self.list.addItem("No backups yet. One is made the first time the app changes the list.")
            self.list.setEnabled(False)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Restore")
        _confirm(bb, QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def selected(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None
