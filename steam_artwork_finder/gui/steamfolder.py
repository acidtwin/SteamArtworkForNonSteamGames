"""The pop-over above "Steam Detected": it searches for Steam as it opens, shows the account
in use, and lets you choose a folder by hand."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..steam import Place, find_steam_roots, list_users, search_places
from . import icons, workers
from .chrome import Overlay, _Avatar, _label, font
from .theme import C, q

STEP_DELAY = 0.35  # seconds per place, so you can see it look; the checks themselves are instant


@dataclass
class Found:
    place: Place
    root: Path
    games: int


def _games(root: Path) -> int:
    """Non-Steam games for the account Steam would pick first."""
    from ..shortcuts import ShortcutsFile

    try:
        users = list_users(root)
        return len(ShortcutsFile(users[0].shortcuts_path).shortcuts) if users else 0
    except Exception:
        return 0


def search(places: list[Place], progress, delay: float = STEP_DELAY):
    """Check each place in turn, reporting ("now", i) then ("done", i, [Found])."""
    seen: set[Path] = set()
    for i, place in enumerate(places):
        progress(("now", i))
        if delay:
            time.sleep(delay)
        hits = []
        for root in find_steam_roots(place.candidates):
            if root not in seen:
                seen.add(root)
                hits.append(Found(place, root, _games(root)))
        progress(("done", i, hits))


def tilde(path: Path | str) -> str:
    s, home = str(path), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


class Pulse(QWidget):
    """A round folder badge that sends out rings while searching, then turns into a tick (or a cross)."""

    D = 44  # the badge; the widget has room around it for the rings

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.D + 16, self.D + 16)
        self.state = "search"
        self._t = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1200, loopCount=-1)
        self._anim.valueChanged.connect(self._step)

    def set_state(self, state: str):
        """"search", "ok" or "none"."""
        self.state = state
        if state == "search":
            self._anim.start()
        else:
            self._anim.stop()
            self._t = 0.0
        self.update()

    def _step(self, v):
        self._t = float(v)
        self.update()

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if self.state == "search":
            self._anim.start()
        super().showEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QRectF(self.rect()).center()
        r = self.D / 2
        tint = {"search": q("blue"), "ok": QColor(C["ok"]), "none": QColor(C["text3"])}[self.state]
        if self.state == "search":
            ring = QColor(tint)
            ring.setAlphaF(0.45 * (1 - self._t))
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            grow = 7 * self._t
            p.drawEllipse(c, r + grow, r + grow)
        bg = QColor(tint)
        bg.setAlphaF(0.14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(c, r, r)
        name, col = {"search": ("folder", C["blue_ink"]), "ok": ("check-circle", C["ok"]),
                     "none": ("x-circle", C["text3"])}[self.state]
        icons.draw(p, name, QRectF(c.x() - 10, c.y() - 10, 20, 20), col)


class Dots(QWidget):
    """One short bar per place: blue while it looks there, green where Steam is, grey where it isn't."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels: list[str] = []
        self.states: list[str] = []
        self.setFixedHeight(30)
        self._blink = 1.0
        self._anim = QVariantAnimation(self, startValue=1.0, endValue=0.4, duration=800, loopCount=-1)
        self._anim.setKeyValueAt(0.5, 0.4)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._step)

    def reset(self, labels: list[str]):
        self.labels = list(labels)
        self.states = [""] * len(labels)
        self.update()

    def set(self, i: int, state: str):
        """"now", "hit", "miss" or ""."""
        self.states[i] = state
        running = "now" in self.states
        if running and self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()
        elif not running:
            self._anim.stop()
            self._blink = 1.0
        self.update()

    def _step(self, v):
        self._blink = float(v)
        self.update()

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        if not self.labels:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gap, n = 6, len(self.labels)
        w = (self.width() - gap * (n - 1)) / n
        f = font(0.84)
        for i, (text, state) in enumerate(zip(self.labels, self.states)):
            x = i * (w + gap)
            col = {"now": QColor(C["blue"]), "hit": QColor(C["ok"]), "miss": q("track_off")}.get(state, q("raised"))
            if state == "now":
                col.setAlphaF(self._blink)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, 0, w, 6), 3, 3)
            f.setWeight(QFont.Weight.DemiBold if state == "hit" else QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(q("text") if state == "hit" else q("text3"))
            p.drawText(QRectF(x, 10, w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class _Skeleton(QWidget):
    """Grey placeholder bars where the account will show."""

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(q("raised"))
        h = self.height()
        p.drawRoundedRect(QRectF(0, (h - 36) / 2, 36, 36), 3, 3)
        p.drawRoundedRect(QRectF(46, h / 2 - 11, 110, 10), 3, 3)
        p.drawRoundedRect(QRectF(46, h / 2 + 4, 170, 8), 3, 3)


class AccountCard(QFrame):
    """The Steam account in use, with Switch when there's more than one."""

    switch_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("facct")
        self.setFixedHeight(58)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(10)
        self.stack = QStackedWidget()
        real = QWidget()
        rl = QHBoxLayout(real)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        self.avatar = _Avatar()
        rl.addWidget(self.avatar)
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addStretch(1)
        self.name = _label("", font(1.0, QFont.Weight.DemiBold), C["text"])
        words.addWidget(self.name)
        self.sub = _label("Signed in on this computer", font(0.92), C["text3"])
        words.addWidget(self.sub)
        words.addStretch(1)
        rl.addLayout(words, 1)
        self.stack.addWidget(real)
        self.skeleton = _Skeleton()
        self.stack.addWidget(self.skeleton)
        lay.addWidget(self.stack, 1)
        self.switch = QPushButton("Switch")
        self.switch.setAccessibleDescription("Switch Steam account")
        self.switch.clicked.connect(self.switch_clicked)
        lay.addWidget(self.switch)

    def set_account(self, name: str, avatar: QPixmap | None, others: bool):
        self.name.setText(name or "No account")
        self.sub.setText("Signed in on this computer" if name else "Nobody has signed in to this Steam yet")
        self.avatar.pm = avatar
        self.avatar.letter = (name or "?").strip()[:1].upper()
        self.avatar.update()
        self._others = others

    def set_waiting(self, waiting: bool):
        self.stack.setCurrentWidget(self.skeleton if waiting else self.stack.widget(0))
        self.switch.setEnabled(not waiting and getattr(self, "_others", False))


class DropZone(QFrame):
    """Drop a folder here, or click to browse."""

    chosen = Signal(Path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fdrop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Choose your Steam folder")
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("folder", 22, C["text3"], self.devicePixelRatioF()))
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(_label("Drop your Steam folder here", font(1.0, QFont.Weight.DemiBold), C["text"]),
                      0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(_label("or click to browse", font(0.95), C["text2"]), 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self._hot(False)

    def _hot(self, on: bool):
        self.setStyleSheet(f"#fdrop {{ border: 1.5px dashed {C['blue'] if on else C['dash']}; border-radius: 4px;"
                           f" background: {C['panel'] if on else 'transparent'}; }}")

    def enterEvent(self, e):
        self._hot(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hot(self.hasFocus())
        super().leaveEvent(e)

    def focusInEvent(self, e):
        self._hot(True)  # the keyboard's on it: lit like a hover
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._hot(self.underMouse())
        super().focusOutEvent(e)

    def _dir_from(self, e) -> Path | None:
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if url.isLocalFile() and p.is_dir():
                return p
        return None

    def dragEnterEvent(self, e):
        if self._dir_from(e):
            self._hot(True)
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._hot(self.hasFocus())

    def dropEvent(self, e):
        self._hot(self.hasFocus())
        p = self._dir_from(e)
        if p:
            e.acceptProposedAction()
            self.chosen.emit(p)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.browse()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.browse()
        else:
            super().keyPressEvent(e)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self.window(), "Choose your Steam folder", str(Path.home()))
        if d:
            self.chosen.emit(Path(d))


class SteamFolderPopover(Overlay):
    """Automatic: searches as it opens, with a pulsing badge and a bar per place, then the account.
    Choose Folder: a drop zone. Both tabs are the same size, so it never jumps."""

    use_root = Signal(Path)  # use this Steam folder
    account_chosen = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName("Steam folder")
        self.setFixedWidth(440)
        self.places: list[Place] = search_places()
        self.found: list[Found] = []
        self.searching = False
        self.delay = STEP_DELAY
        self._run = 0
        self.root: Path | None = None
        self.users: list[str] = []
        self.user_index = -1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        seg = QFrame()
        seg.setObjectName("fseg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(3)
        self.tabs = []
        for i, text in enumerate(("Automatic", "Choose Folder")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.clicked.connect(lambda _=False, i=i: self.show_tab(i))
            sl.addWidget(b)
            self.tabs.append(b)
        lay.addWidget(seg)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages)

        auto = QWidget()
        al = QVBoxLayout(auto)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(10)
        radar = QHBoxLayout()
        radar.setContentsMargins(-8, 0, 0, 0)  # the pulse's ring room overhangs the edge
        radar.setSpacing(4)
        self.pulse = Pulse()
        radar.addWidget(self.pulse, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = _label("", font(1.0, QFont.Weight.Bold), C["text"])
        words.addWidget(self.title)
        self.sub = QLabel()
        self.sub.setFont(font(0.92))
        self.sub.setTextFormat(Qt.TextFormat.RichText)
        self.sub.linkActivated.connect(self._link)
        words.addWidget(self.sub)
        self.dots = Dots()
        words.addSpacing(4)
        words.addWidget(self.dots)
        radar.addLayout(words, 1)
        al.addLayout(radar)
        al.addWidget(_label("ACCOUNT", font(0.84, QFont.Weight.Bold, 0.08), C["text3"]))
        self.account = AccountCard()
        self.account.switch_clicked.connect(self._switch)
        al.addWidget(self.account)
        al.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.again = QPushButton("Detect Again")
        self.again.setIcon(icons.icon("search", 15, C["text"]))
        self.again.clicked.connect(self.start_search)
        row.addWidget(self.again)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.dismiss)
        row.addWidget(close)
        al.addLayout(row)
        self.pages.addWidget(auto)

        manual = QWidget()
        ml = QVBoxLayout(manual)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(10)
        self.drop = DropZone()
        self.drop.chosen.connect(self._chosen)
        ml.addWidget(self.drop, 1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setFont(font(0.92))
        ml.addWidget(self.hint)
        mrow = QHBoxLayout()
        mrow.addStretch(1)
        close2 = QPushButton("Close")
        close2.clicked.connect(self.dismiss)
        mrow.addWidget(close2)
        ml.addLayout(mrow)
        self.pages.addWidget(manual)
        self._say_hint()
        self.show_tab(0)

    # ---- what the app tells it --------------------------------------------------

    def set_current(self, root: Path | None, users: list[str], index: int, avatar: QPixmap | None):
        self.root, self.users, self.user_index = root, list(users), index
        name = users[index] if 0 <= index < len(users) else ""
        self.account.set_account(name, avatar, len(users) > 1)
        self.account.set_waiting(self.searching)
        if not self.searching:
            self._settle()

    def open_for(self, anchor: QPoint):
        self.show_tab(0)
        self._say_hint()
        self.open_above(anchor)
        self.start_search()

    # ---- the search -------------------------------------------------------------

    def start_search(self):
        self._run += 1
        run = self._run
        self.searching = True
        self.found = []
        self.places = search_places()
        self.dots.reset([pl.short for pl in self.places])
        self.pulse.set_state("search")
        self.title.setText("Looking for Steam…")
        self.sub.setText("")
        self.again.setEnabled(False)
        self.account.set_waiting(True)
        workers.run(search, self.places, delay=self.delay,
                    on_progress=lambda m: self._progress(run, m),
                    on_done=lambda _r: self._finished(run),
                    on_error=lambda _m: self._finished(run))

    def _progress(self, run: int, msg):
        if run != self._run:
            return  # an earlier search, replaced by Detect Again
        if msg[0] == "now":
            i = msg[1]
            self.dots.set(i, "now")
            self._sub_text(f"Checking {self.places[i].name}", C["text3"])
        else:
            _, i, hits = msg
            self.found += hits
            self.dots.set(i, "hit" if hits else "miss")

    def _finished(self, run: int):
        if run != self._run:
            return
        self.searching = False
        self.again.setEnabled(True)
        self.account.set_waiting(False)
        if self.root is None and self.found:
            self.use_root.emit(self.found[0].root)  # Steam was missing and the search found it
        self._settle()

    def _settle(self):
        """The title and line under it once the search is done."""
        if self.root is None:
            self.pulse.set_state("none")
            self.title.setText("Steam not found")
            self._sub_html(f"Is it somewhere else? <a href='manual' style='color:{C['blue_ink']}; "
                           f"text-decoration:none'><b>Choose Folder</b></a>")
            return
        self.pulse.set_state("ok")
        hit = next((f for f in self.found if f.root == self.root), None)
        self.title.setText(f"Using {hit.place.name}" if hit else "Using your chosen folder")
        games = hit.games if hit else None
        tail = f" · {games} game{'' if games == 1 else 's'}" if games is not None else ""
        link = f" · <a href='change' style='color:{C['blue_ink']}; text-decoration:none'><b>Change</b></a>"
        room = self.sub.width() or 300
        fm = self.sub.fontMetrics()
        path = fm.elidedText(tilde(self.root), Qt.TextElideMode.ElideMiddle,
                             max(80, room - fm.horizontalAdvance(tail + " · Change") - 8))
        self._sub_html(f"<span style='color:{C['text2']}'>{path}{tail}</span>{link}")
        self.sub.setToolTip(str(self.root))

    def _sub_text(self, text: str, colour: str):
        self.sub.setText(f"<span style='color:{colour}'>{text}</span>")

    def _sub_html(self, html: str):
        self.sub.setText(html)

    # ---- actions ------------------------------------------------------------------

    def show_tab(self, i: int):
        self.tabs[i].setChecked(True)
        self.pages.setCurrentIndex(i)

    def _link(self, href: str):
        if href == "manual" or len(self.found) < 2:
            return self.show_tab(1)
        menu = QMenu(self)
        group = QActionGroup(menu)
        for f in self.found:
            act = QAction(f"{f.place.name}  ·  {tilde(f.root)}", menu, checkable=True, checked=f.root == self.root)
            act.triggered.connect(lambda _=False, r=f.root: self.use_root.emit(r) if r != self.root else None)
            group.addAction(act)
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(QAction("Choose another folder…", menu, triggered=lambda: self.show_tab(1)))
        menu.exec(self.sub.mapToGlobal(QPoint(0, self.sub.height() + 2)))
        menu.deleteLater()

    def _switch(self):
        if len(self.users) < 2:
            return
        menu = QMenu(self)
        group = QActionGroup(menu)
        for i, name in enumerate(self.users):
            act = QAction(name, menu, checkable=True, checked=i == self.user_index)
            act.triggered.connect(lambda _=False, i=i: self.account_chosen.emit(i) if i != self.user_index else None)
            group.addAction(act)
            menu.addAction(act)
        b = self.account.switch
        menu.exec(b.mapToGlobal(QPoint(0, b.height() + 2)))
        menu.deleteLater()

    def _say_hint(self, error: str = ""):
        if error:
            self.hint.setText(f"<span style='color:{C['bad']}'>{error}</span>")
        else:
            self.hint.setText(f"<span style='color:{C['text3']}'>It's the folder with <b>userdata</b> inside, "
                              f"usually <b>~/.local/share/Steam</b>.</span>")

    def _chosen(self, path: Path):
        roots = find_steam_roots([path])
        if not roots:
            return self._say_hint(f"{tilde(path)} doesn't have a Steam “userdata” folder inside it.")
        self._say_hint()
        self.use_root.emit(roots[0])
        self.show_tab(0)
        self._settle()
