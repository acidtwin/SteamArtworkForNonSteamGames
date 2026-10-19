"""Dialogs: API key, game match, and restoring a backup."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, QRectF, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..sgdb import Game, SteamGridDB
from . import icons, theme, workers
from .chrome import Overlay
from .theme import C

API_KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"


def _confirm(bb: QDialogButtonBox, button):
    b = bb.button(button)
    if b is not None:
        b.setProperty("kind", "confirm")


class KeyBadge(QWidget):
    """The blue rounded square with a key, at the top of the API key window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#1a9fff"))
        g.setColorAt(1, QColor("#1a4f8a"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        icons.draw(p, "key", QRect(11, 11, 22, 22), "#ffffff")


class KeyPopover(Overlay):
    """Asks for the SteamGridDB API key, above the connection item in the status bar."""

    saved = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName("SteamGridDB API key")
        self.setFixedWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(14)

        hero = QHBoxLayout()
        hero.setSpacing(14)
        hero.addWidget(KeyBadge(), 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(3)
        title = QLabel("Connect to SteamGridDB")
        f = QFont(title.font())
        f.setPointSizeF(theme.pt("heading"))
        f.setWeight(QFont.Weight.Bold)
        title.setFont(f)
        words.addWidget(title)
        sub = QLabel("Artwork comes from SteamGridDB. A free API key lets this app search it. "
                     "It stays on this computer.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {C['text2']};")
        words.addWidget(sub)
        hero.addLayout(words, 1)
        lay.addLayout(hero)

        row = QHBoxLayout()
        row.setSpacing(8)
        box = QFrame()
        box.setObjectName("keybox")
        box.setFixedHeight(42)
        box.setStyleSheet(f"""
            #keybox {{ background: {C['input']}; border: 1px solid {C['line']}; border-radius: 3px; }}
            #keybox QLineEdit {{ background: transparent; border: none; min-height: 0; padding: 0 0 0 11px; }}
            #keybox QToolButton {{ min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; padding: 0; }}
        """)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 4, 0)
        bl.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Paste your API key")
        self.edit.setAccessibleName("SteamGridDB API key")
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.textChanged.connect(self._changed)
        self.edit.installEventFilter(self)
        bl.addWidget(self.edit, 1)
        self.eye = QToolButton()
        self.eye.setIcon(icons.icon("eye", 16, C["text3"]))
        self.eye.setToolTip("Show key")
        self.eye.setAccessibleName("Show key")
        self.eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eye.clicked.connect(self._toggle_shown)
        bl.addWidget(self.eye)
        self.keybox = box
        row.addWidget(box, 1)
        paste = QPushButton("Paste")
        paste.setIcon(icons.icon("paste", 16, C["text"]))
        paste.setAccessibleDescription("Paste the key from the clipboard")
        paste.clicked.connect(self._paste)
        row.addWidget(paste)
        lay.addLayout(row)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        link = QPushButton("Get your free key")
        link.setProperty("kind", "quiet")
        link.setIcon(icons.icon("external", 15, C["blue_ink"]))
        link.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # the arrow after the words
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setToolTip(API_KEY_URL)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_KEY_URL)))
        foot.addWidget(link)
        foot.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.dismiss)
        foot.addWidget(cancel)
        self.save = QPushButton("Save")
        self.save.setProperty("kind", "confirm")
        self.save.clicked.connect(self._check)
        self.edit.returnPressed.connect(self._check)
        foot.addWidget(self.save)
        lay.addLayout(foot)
        self._changed()

    def open_for(self, current: str, anchor: QPoint):
        if not self.isVisible():
            self.edit.setText(current)
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.eye.setIcon(icons.icon("eye", 16, C["text3"]))
        self.open_above(anchor, align_right=True)
        self.edit.setFocus()

    def _toggle_shown(self):
        shown = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
        self.eye.setIcon(icons.icon("eye-off" if shown else "eye", 16, C["text3"]))
        self.eye.setToolTip("Hide key" if shown else "Show key")
        self.eye.setAccessibleName(self.eye.toolTip())

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() in (e.Type.FocusIn, e.Type.FocusOut):
            colour = C["blue"] if e.type() == e.Type.FocusIn else C["line"]
            self.keybox.setStyleSheet(self.keybox.styleSheet().split("/*focus*/")[0]
                                      + f"/*focus*/ #keybox {{ border-color: {colour}; }}")
        return super().eventFilter(obj, e)  # the pop-over's own click-outside handling

    def _paste(self):
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.edit.setText(text)
        else:
            self._say("There's nothing to paste. Copy your key on SteamGridDB first.", C["bad"])

    def _changed(self):
        self.save.setEnabled(bool(self.edit.text().strip()))
        self.status.hide()

    def _say(self, text: str, colour: str):
        self.status.setStyleSheet(f"color: {colour};")
        self.status.setText(text)
        self.status.show()

    def _check(self):
        key = self.edit.text().strip()
        if not key:
            return
        self._say("Checking the key…", C["text2"])
        self.save.setEnabled(False)

        def done(ok):
            self.save.setEnabled(True)
            if ok:
                self.dismiss()
                self.saved.emit(key)
            else:
                self._say("SteamGridDB didn't accept that key. Check you copied all of it.", C["bad"])

        def failed(msg):
            self.save.setEnabled(True)
            self._say(msg, C["bad"])

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
        self.list.setProperty("plain", True)
        self.list.setAccessibleName("Search results")
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
            label = g.name + (f" ({g.year})" if g.year else "") + ("  · verified" if g.verified else "")
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
        self.list.setProperty("plain", True)
        self.list.setAccessibleName("Backups")
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
