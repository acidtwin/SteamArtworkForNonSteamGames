"""Dialogs: API key, game match, and restoring a backup."""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QGuiApplication, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..sgdb import Game, SteamGridDB
from . import icons, theme, workers
from .chrome import Overlay
from .scrim import run_dialog
from .theme import C
from .widgets import FlowLayout, fill_rect

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
        g.setColorAt(0, QColor(C["blue"]))
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
        box.setFixedHeight(40)  # level with the Paste button beside it
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
        self._run = 0  # each key check; closing the pop-over drops the check on its way
        self._changed()

    def open_for(self, current: str, anchor: QPoint):
        if not self.isVisible():
            self.edit.setText(current)
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.eye.setIcon(icons.icon("eye", 16, C["text3"]))
            self._changed()  # Save back on, if it was closed mid-check
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

    def dismiss(self):
        self._run += 1  # a check still running no longer saves the key
        self.save.setEnabled(bool(self.edit.text().strip()))
        super().dismiss()

    def _check(self):
        key = self.edit.text().strip()
        if not key:
            return
        self._say("Checking the key…", C["text2"])
        self.save.setEnabled(False)
        self._run += 1
        run = self._run

        def done(ok):
            if run != self._run:
                return  # closed (or checked again) while this one was on its way
            self.save.setEnabled(True)
            if ok:
                self.dismiss()
                self.saved.emit(key)
            else:
                self._say("SteamGridDB didn't accept that key. Check you copied all of it.", C["bad"])

        def failed(msg):
            if run != self._run:
                return
            self.save.setEnabled(True)
            self._say(msg, C["bad"])

        workers.run(SteamGridDB(key).check_key, on_done=done, on_error=failed)

    def key(self) -> str:
        return self.edit.text().strip()


PAGE_FULL = 50  # SteamGridDB returns up to this many per page; more means "50+"
SHOWN_RESULTS = 8


def _bold(w: QWidget, size: str | None = None, weight=QFont.Weight.DemiBold):
    f = QFont(w.font())
    if size:
        f.setPointSizeF(theme.pt(size))
    f.setWeight(weight)
    w.setFont(f)


_cropped: dict[tuple, QPixmap] = {}  # (pixmap, size, dpr) -> the pixmap cropped to fill it


def _paint_cover(p: QPainter, r: QRectF, pm: QPixmap | None, name: str = ""):
    """A cover cropped to fill r, or a dark card with the game's initial while it loads."""
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addRoundedRect(r, 2, 2)
    p.save()
    p.setClipPath(path)
    if pm is not None and not pm.isNull():
        dpr = p.device().devicePixelRatioF()
        key = (pm.cacheKey(), r.width(), r.height(), dpr)
        out = _cropped.get(key)
        if out is None:
            if len(_cropped) > 200:
                _cropped.clear()
            out = _cropped[key] = fill_rect(pm, r.size().toSize(), dpr)
        p.drawPixmap(r.topLeft(), out)
    else:
        p.fillRect(r, QColor(C["raised"]))
        if name:
            p.setPen(QColor(C["text3"]))
            f = QFont(p.font())
            f.setWeight(QFont.Weight.ExtraBold)
            f.setPointSizeF(max(8.0, r.height() / 5))
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, name[:1].upper())
    p.restore()


class ResultRow(QAbstractButton):
    """One search result: its cover, name, year and a Verified mark, with a tick when picked."""

    COVER = QSize(40, 60)

    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.cover: QPixmap | None = None
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(72)
        bits = [game.name] + ([game.year] if game.year else []) + (["verified"] if game.verified else [])
        self.setAccessibleName(", ".join(bits))

    def set_cover(self, pm: QPixmap):
        self.cover = pm
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.setChecked(True)
        self.window().accept()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        on = self.isChecked()
        r = QRectF(self.rect())
        if on:
            p.fillRect(r, QColor(C["select"]))
            p.fillRect(QRectF(0, 0, 3, r.height()), QColor(C["blue"]))
        elif self.underMouse():
            p.fillRect(r, QColor(C["panel"]))
        cv = QRectF(6, 6, self.COVER.width(), self.COVER.height())
        _paint_cover(p, cv, self.cover, self.game.name)
        x = cv.right() + 12
        right = r.width() - 40
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        f.setPointSizeF(theme.pt("body") * 1.05)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        fm = p.fontMetrics()
        p.drawText(QRectF(x, 14, right - x, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.game.name, Qt.TextElideMode.ElideRight, int(right - x)))
        y = 14 + fm.height() + 5
        f2 = QFont(self.font())
        f2.setPointSizeF(theme.pt("faint"))
        p.setFont(f2)
        fm2 = p.fontMetrics()
        if self.game.year:
            p.setPen(QColor(C["text3"]))
            w = fm2.horizontalAdvance(self.game.year)
            p.drawText(QRectF(x, y, w + 2, fm2.height()), Qt.AlignmentFlag.AlignVCenter, self.game.year)
            x += w + 10
        if self.game.verified:
            icons.draw(p, "check-circle", QRectF(x, y + (fm2.height() - 12) / 2, 12, 12), C["ok"])
            f3 = QFont(f2)
            f3.setWeight(QFont.Weight.ExtraBold)
            f3.setPointSizeF(theme.pt("tag"))
            f3.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
            p.setFont(f3)
            p.setPen(QColor(C["ok"]))
            p.drawText(QRectF(x + 16, y, 80, fm2.height()), Qt.AlignmentFlag.AlignVCenter, "VERIFIED")
        if on:
            c = QRectF(r.width() - 32, r.height() / 2 - 10, 20, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["blue"]))
            p.drawEllipse(c)
            icons.draw(p, "check", c.adjusted(3.5, 3.5, -3.5, -3.5), "white")


class ArtPreview(QWidget):
    """The picked game's banner with its cover over the corner and its logo on top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(84 + 24)
        self.hero = self.cover = self.logo = None
        self.name = ""

    def show_game(self, name: str):
        self.name = name
        self.hero = self.cover = self.logo = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        hero = QRectF(0, 0, self.width(), 84)
        if self.hero is not None and not self.hero.isNull():
            _paint_cover(p, hero, self.hero)
        else:
            g = QLinearGradient(hero.topLeft(), hero.bottomRight())
            g.setColorAt(0, QColor(C["stage"]))
            g.setColorAt(1, QColor(C["raised"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(hero, 3, 3)
        if self.logo is not None and not self.logo.isNull():
            box = QRectF(hero.width() * 0.42, 8, hero.width() * 0.55, 68)
            s = self.logo.size().scaled(box.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap(QRectF(box.right() - s.width(), box.center().y() - s.height() / 2, s.width(), s.height()),
                         self.logo, QRectF(self.logo.rect()))
        cv = QRectF(10, 84 + 24 - 96, 64, 96)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRoundedRect(cv.adjusted(-1, 2, 1, 4), 3, 3)
        _paint_cover(p, cv, self.cover, self.name)


class Chip(QPushButton):
    """A suggested search term, with where it came from in quieter text."""

    def __init__(self, term: str, source: str, parent=None):
        super().__init__(parent)
        self.term = term
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("matchchip")
        self.setText(f"{term}  ·  {source}")
        self.setAccessibleName(f"Search for {term}, from the {source} name")


class _Choice(QComboBox):
    """A drop-down list in the app's style: the theme's flat box with a chevron of its own."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QComboBox { padding-right: 32px; } QComboBox::drop-down { width: 0; border: none; }")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        icons.draw(p, "chevron-down", QRect(self.width() - 26, (self.height() - 14) // 2, 14, 14), C["text2"])


class MatchDialog(QDialog):
    """Pick which game a shortcut is: smarter search terms on top, SteamGridDB results with
    covers on the left, a preview of the picked game's artwork, and (given `store`) which
    Steam store game its official artwork comes from, or Not on Steam."""

    NOT_ON_STEAM = object()  # the "Not on Steam" choice

    def __init__(self, parent, sgdb: SteamGridDB, name: str, exe: str = "", store=None, steam_now=None):
        from ..sgdb import looks_like_file, suggestions

        super().__init__(parent)
        self.sgdb = sgdb
        self.store = store
        # Whether the Steam game is settled: picked here, or already known. Until then it follows
        # the SteamGridDB pick by name.
        self._steam_touched = steam_now is not None
        self._steam_start = None
        self.games: list[Game] = []
        self.rows: list[ResultRow] = []
        self._run = 0  # newer searches win over slower older ones
        self._gone = False  # closed: answers still on their way are dropped
        self._art: dict[tuple[int, str], list] = {}  # (game id, kind) -> assets
        self._pix: dict[str, QPixmap] = {}
        self._asking: dict[tuple[int, str], list] = {}  # requests in flight -> who wants the answer
        self.setWindowTitle(f"Which game is “{name}”?")
        self.setStyleSheet(
            f"#matchchip {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 15px;"
            f" min-height: 30px; max-height: 30px; padding: 0 12px; color: {C['text']}; font-weight: 600; }}"
            f"#matchchip:hover {{ border-color: {C['fade_ink']}; }}"
            f"#matchchip[kbd=\"true\"]:focus {{ border: 2px solid white; padding: 0 11px; }}"
            f"#matchchip:checked {{ background: {C['fade']}; border: 2px solid {C['fade_ink']}; padding: 0 11px; }}"
            f"#matchtip {{ background: rgba(26,159,255,.08); border-left: 3px solid {C['blue']}; }}"
            f"#matchprev {{ background: {C['panel']}; border-radius: 4px; }}"
            f"#matchstat {{ background: {C['ground']}; border-radius: 3px; }}"
            f"#matchsearch QToolButton {{ min-width: 0; min-height: 0; padding: 0; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(12)
        heading = QLabel(f"Which game is “{name}”?")  # the same opening line as the app's other windows
        heading.setWordWrap(True)
        hf = QFont(heading.font())
        hf.setPointSizeF(theme.pt("heading"))
        hf.setWeight(QFont.Weight.Bold)
        heading.setFont(hf)
        body.addWidget(heading)

        self.terms = suggestions(name, exe)
        first = self.terms[0][0] if self.terms else name
        self.tip = None
        if looks_like_file(name) and self.terms:
            why = ("so we searched the folder it's in instead" if self.terms[0][1] == "folder"
                   else "so we searched without the file ending")
            self.tip = QFrame()
            self.tip.setObjectName("matchtip")
            tl = QHBoxLayout(self.tip)
            tl.setContentsMargins(10, 8, 10, 8)
            tl.setSpacing(8)
            bulb = QLabel()
            bulb.setPixmap(icons.pixmap("bulb", 16, C["blue_ink"], self.devicePixelRatioF()))
            tl.addWidget(bulb)
            tip = QLabel(f"“{name}” looks like a file name, {why}.")
            tip.setWordWrap(True)
            tip.setStyleSheet(f"color: {C['text2']};")
            tl.addWidget(tip, 1)
            body.addWidget(self.tip)

        self.edit = QLineEdit(first)
        self.edit.setPlaceholderText("Search SteamGridDB")
        self.edit.addAction(icons.icon("search", 16, C["text3"]), QLineEdit.ActionPosition.LeadingPosition)
        self.edit.setObjectName("matchsearch")
        self.edit.setClearButtonEnabled(True)
        for act in self.edit.findChildren(QAction):
            if act.objectName() == "_q_qlineeditclearaction":
                act.setIcon(icons.icon("close", 16, C["text2"]))
        self.edit.setAccessibleName("Search SteamGridDB")
        self.edit.textEdited.connect(self._typed)
        self.edit.returnPressed.connect(self.search)
        body.addWidget(self.edit)
        self._wait = QTimer(self)
        self._wait.setSingleShot(True)
        self._wait.setInterval(450)
        self._wait.timeout.connect(self.search)

        self.chips: list[Chip] = []
        if len(self.terms) > 1 or (self.terms and self.terms[0][0] != name):
            cl = QHBoxLayout()
            cl.setSpacing(8)
            tl = QLabel("Try:")
            tl.setStyleSheet(f"color: {C['text3']};")
            cl.addWidget(tl)
            for term, source in self.terms:
                chip = Chip(term, source)
                chip.setChecked(term == first)
                chip.clicked.connect(lambda _=False, c=chip: self._use_chip(c))
                cl.addWidget(chip)
                self.chips.append(chip)
            cl.addStretch(1)
            body.addLayout(cl)

        split = QHBoxLayout()
        split.setSpacing(16)
        holder = QWidget()
        self.list = QVBoxLayout(holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(4)
        # Shown in the results' place when a search finds nothing.
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(16, 40, 16, 16)
        el.setSpacing(8)
        ei = QLabel()
        ei.setPixmap(icons.pixmap("search", 32, C["text3"], self.devicePixelRatioF()))
        el.addWidget(ei, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = QLabel()
        self.empty_title.setWordWrap(True)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _bold(self.empty_title, "body", QFont.Weight.DemiBold)
        el.addWidget(self.empty_title)
        self.empty_text = QLabel()
        self.empty_text.setWordWrap(True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setStyleSheet(f"color: {C['text2']};")
        el.addWidget(self.empty_text)
        self.empty.hide()
        self.list.addWidget(self.empty)
        self.list.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidget(holder)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.scroll.setAccessibleName("Search results")
        split.addWidget(self.scroll, 1)

        prev = QFrame()
        prev.setObjectName("matchprev")
        prev.setFixedWidth(270)
        pl = QVBoxLayout(prev)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.setSpacing(6)
        self.art = ArtPreview()
        pl.addWidget(self.art)
        self.p_name = QLabel()
        self.p_name.setWordWrap(True)
        _bold(self.p_name, "heading", QFont.Weight.Bold)
        pl.addSpacing(4)
        pl.addWidget(self.p_name)
        self.p_meta = QLabel()
        self.p_meta.setTextFormat(Qt.TextFormat.RichText)
        pl.addWidget(self.p_meta)
        grid = QHBoxLayout()
        grid.setSpacing(6)
        cols = [QVBoxLayout(), QVBoxLayout()]
        for c in cols:
            c.setSpacing(6)
            grid.addLayout(c)
        self.stats: dict[str, QLabel] = {}
        for i, (kind, label) in enumerate((("grid", "covers"), ("wide", "wide covers"), ("hero", "banners"),
                                           ("logo", "logos"), ("icon", "icons"))):
            box = QLabel()
            box.setObjectName("matchstat")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setContentsMargins(8, 6, 8, 6)
            box.setProperty("label", label)
            cols[i % 2].addWidget(box)
            self.stats[kind] = box
        pl.addSpacing(4)
        pl.addLayout(grid)
        pl.addStretch(1)
        split.addWidget(prev)
        body.addLayout(split, 1)
        self.steam_box = None
        if store is not None:
            srow = QHBoxLayout()
            srow.setSpacing(10)
            sl = QLabel("Official art from Steam")
            sl.setStyleSheet(f"color: {C['text2']};")
            srow.addWidget(sl)
            self.steam_box = _Choice()
            self.steam_box.setAccessibleName("Steam store game for official artwork")
            self.steam_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.steam_box.setMinimumContentsLength(16)
            self.steam_box.setToolTip("If this game is also sold on Steam, its store page's official artwork\n"
                                      "shows first in each row. Pick Not on Steam to leave it out.")
            self._steam_items(steam_now, [])
            self._steam_start = self.steam_box.currentData()
            self.steam_box.activated.connect(self._steam_picked)
            srow.addWidget(self.steam_box, 1)
            body.addLayout(srow)
        outer.addLayout(body, 1)

        foot = QFrame()
        foot.setObjectName("dialogfoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(8)
        self.status = QLabel()
        self.status.setStyleSheet(f"color: {C['text3']};")
        fl.addWidget(self.status, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        fl.addWidget(cancel)
        self.ok = QPushButton("Use this game")
        self.ok.setIcon(icons.icon("check", 15, "white"))
        self.ok.setProperty("kind", "confirm")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        fl.addWidget(self.ok)
        outer.addWidget(foot)

        self.resize(780, 600)
        self._preview(None)
        self.search()

    # ---- searching ------------------------------------------------------------

    def _typed(self, _text: str):
        for c in self.chips:
            c.setChecked(c.term == self.edit.text().strip())
        self._wait.start()

    def _use_chip(self, chip: Chip):
        for c in self.chips:
            c.setChecked(c is chip)
        self.edit.setText(chip.term)
        self.search()

    def search(self):
        self._wait.stop()
        self._run += 1
        run = self._run
        self.status.setText("Searching…")
        self._fill([])
        workers.run(self.sgdb.search, self.edit.text(),
                    on_done=lambda games: self._show(games, run),
                    on_error=lambda msg: run == self._run and not self._gone and self.status.setText(msg))
        if self.store is not None and self.edit.text().strip():
            workers.run(self.store.search, self.edit.text().strip(),
                        on_done=lambda games: self._steam_results(games, run), on_error=lambda _m: None)

    def done(self, r: int):
        self._gone = True
        self._run += 1  # searches still on their way are dropped
        self._wait.stop()
        super().done(r)

    def _show(self, games: list[Game], run: int | None = None):
        if self._gone or run is not None and run != self._run:
            return
        n = len(games)
        self.status.setText(f"{n} result{'' if n == 1 else 's'}" if games else "No matches")
        self._fill(games[:SHOWN_RESULTS])
        if not games:
            term = self.edit.text().strip()
            self.empty_title.setText(f"Nothing called “{term}” on SteamGridDB" if term else "Type a game's name to search")
            hints = ["Try fewer words" if len(term.split()) > 2 else "Try another spelling",
                     "or one of the names above" if len(self.chips) > 1 else "or the name the game is sold under"]
            self.empty_text.setText(", ".join(hints) + ".")
            self.empty.show()
        for row in self.rows:
            self._load(row.game.id, "grid", row.set_cover)
        if self.rows:
            self.rows[0].setChecked(True)
        else:
            self._preview(None)

    def _fill(self, games: list[Game]):
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.empty.hide()
        self.games = games
        for g in games:
            row = ResultRow(g)
            row.toggled.connect(lambda on, r=row: on and self._preview(r.game))
            self.list.insertWidget(self.list.count() - 2, row)  # above the empty state and the stretch
            self.rows.append(row)
        self.ok.setEnabled(bool(games) or self.steam_box is not None)

    def keyPressEvent(self, e):
        step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1}.get(e.key())
        if step and self.rows:
            i = next((n for n, r in enumerate(self.rows) if r.isChecked()), -1)
            row = self.rows[max(0, min(len(self.rows) - 1, i + step))]
            row.setChecked(True)
            self.scroll.ensureWidgetVisible(row)
            return
        super().keyPressEvent(e)

    # ---- the preview ----------------------------------------------------------

    # ---- the Steam store game ---------------------------------------------------

    def _steam_items(self, keep, games):
        """Fill the Steam list: `keep` (a StoreGame, NOT_ON_STEAM or None) first, then `games`."""
        box = self.steam_box
        box.blockSignals(True)
        box.clear()
        seen = set()
        for g in ([keep] if keep is not None and keep is not self.NOT_ON_STEAM else []) + list(games):
            if g.appid in seen:
                continue
            seen.add(g.appid)
            box.addItem(f"{g.name}   ·   app {g.appid}", g)
        box.addItem("Not on Steam", self.NOT_ON_STEAM)
        box.setCurrentIndex(box.count() - 1 if keep is None or keep is self.NOT_ON_STEAM else 0)
        box.blockSignals(False)

    def _steam_results(self, games, run: int):
        if self._gone or run != self._run or self.steam_box is None:
            return
        self._steam_items(self.steam_box.currentData(), games)
        self._steam_follow(self.selected())

    def _steam_picked(self, _i: int):
        self._steam_touched = True

    def _steam_follow(self, game: Game | None):
        """Until the user picks one themselves, the Steam game follows the SteamGridDB pick by name."""
        from ..steamstore import norm_name

        box = self.steam_box
        if box is None or self._steam_touched or game is None:
            return
        for i in range(box.count()):
            g = box.itemData(i)
            if g is not self.NOT_ON_STEAM and norm_name(g.name) == norm_name(game.name):
                box.setCurrentIndex(i)
                return

    def steam_choice(self):
        """The Steam store game picked (a StoreGame, or NOT_ON_STEAM), or None if it didn't change."""
        if self.steam_box is None:
            return None
        now = self.steam_box.currentData()
        start = self._steam_start
        same = now is start or (now is not self.NOT_ON_STEAM and start is not None
                                and start is not self.NOT_ON_STEAM and now.appid == start.appid)
        return None if same else now

    def _preview(self, game: Game | None):
        self._steam_follow(game)
        self.art.show_game(game.name if game else "")
        self.p_name.setText(game.name if game else "Pick a game")
        if game:
            bits = [f"<span style='color:{C['text3']}'>{game.year}</span>"] if game.year else []
            if game.verified:
                bits.append(f"<b style='color:{C['ok']}; font-size:{theme.pt('tag')}pt'>✓ VERIFIED</b>")
            self.p_meta.setText(" &nbsp; ".join(bits))
        else:
            self.p_meta.setText(f"<span style='color:{C['text3']}'>Its artwork shows here.</span>")
        for kind, box in self.stats.items():
            self._stat(box, None)
            if game:
                self._load(game.id, kind, None, stat=box)
        if game:
            gid = game.id
            self._load(gid, "grid", lambda pm: self._set_art(gid, "cover", pm))
            self._load(gid, "hero", lambda pm: self._set_art(gid, "hero", pm))
            self._load(gid, "logo", lambda pm: self._set_art(gid, "logo", pm))

    def _stat(self, box: QLabel, n: int | None):
        num = "–" if n is None else (f"{PAGE_FULL}+" if n >= PAGE_FULL else str(n))
        box.setText(f"<b style='color:{C['text']}; font-size:{theme.pt('body') * 1.05}pt'>{num}</b>"
                    f"<br><span style='color:{C['text2']}'>{box.property('label')}</span>")

    def _set_art(self, gid: int, which: str, pm: QPixmap):
        g = self.selected()
        if g is None or g.id != gid:
            return
        setattr(self.art, which, pm)
        self.art.update()

    def _load(self, gid: int, kind: str, on_pixmap, stat: QLabel | None = None):
        """Get the game's artwork list for `kind` (once), then its first picture's thumbnail."""
        def got(assets):
            if self._gone:
                return
            if stat is not None:
                g = self.selected()
                if g is not None and g.id == gid:
                    self._stat(stat, len(assets))
            if on_pixmap is not None and assets:
                self._thumb(assets[0].thumb, on_pixmap)

        key = (gid, kind)
        if key in self._art:
            got(self._art[key])
            return
        waiting = self._asking.setdefault(key, [])
        waiting.append(got)
        if len(waiting) > 1:
            return  # already asked; this joins the answer

        def done(assets):
            self._art[key] = assets
            for cb in self._asking.pop(key, []):
                cb(assets)

        workers.run(self.sgdb.assets, kind, gid, on_done=done, on_error=lambda _m: self._asking.pop(key, None))

    def _thumb(self, url: str, on_pixmap):
        from .widgets import load_pixmap

        def done(data):
            if self._gone:
                return
            pm = self._pix.get(url) or load_pixmap(data=data, key=("thumb", url))
            self._pix[url] = pm
            try:
                on_pixmap(pm)
            except RuntimeError:  # the row went away with a newer search
                pass

        if url in self._pix:
            done(None)
        else:
            workers.run_retrying(workers.cached_download, url, self.sgdb.download, on_done=done,
                                 on_error=lambda _m: None)

    def selected(self) -> Game | None:
        return next((r.game for r in self.rows if r.isChecked()), None)


class _Cover(QWidget):
    """A small cover, or a blank card when the game has none yet."""

    def __init__(self, pm: QPixmap | None, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.setFixedSize(30, 45)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self.pm is not None and not self.pm.isNull():
            from PySide6.QtGui import QPainterPath

            path = QPainterPath()
            path.addRoundedRect(r, 2, 2)
            p.setClipPath(path)
            p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["raised"]))
            p.drawRoundedRect(r, 2, 2)


class QuitDialog(QDialog):
    """Asked on quit when artwork is waiting: Apply and Quit, Discard, or Cancel,
    with "always apply when I quit" so it needn't ask again."""

    SHOWN = 3  # games listed; the rest are counted

    def __init__(self, parent, games: list[tuple[str, str, int, QPixmap | None]]):
        """games: (name, what changed, how many changes, cover) for each waiting game."""
        super().__init__(parent)
        self.setWindowTitle(QGuiApplication.applicationDisplayName() or "Steam Artwork for Non-Steam Games")
        self.setMinimumWidth(460)
        self.choice: str | None = None  # "apply", "discard", or None for cancel
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(18, 18, 18, 18)
        body.setSpacing(12)
        title = QLabel("Apply your new artwork before quitting?")
        f = QFont(title.font())
        f.setPointSizeF(theme.pt("heading"))
        f.setWeight(QFont.Weight.Bold)
        title.setFont(f)
        body.addWidget(title)
        for name, what, n, pm in games[:self.SHOWN]:
            row = QFrame()
            row.setObjectName("quitgame")
            row.setStyleSheet(f"#quitgame {{ background: {C['panel']}; border-radius: 3px; }}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 6, 10, 6)
            rl.setSpacing(10)
            rl.addWidget(_Cover(pm))
            words = QVBoxLayout()
            words.setSpacing(1)
            nl = QLabel(name)
            nf = QFont(nl.font())
            nf.setWeight(QFont.Weight.DemiBold)
            nl.setFont(nf)
            words.addWidget(nl)
            wl = QLabel(what)
            wl.setStyleSheet(f"color: {C['text3']};")
            words.addWidget(wl)
            rl.addLayout(words, 1)
            pill = QLabel(f"{n} CHANGE{'' if n == 1 else 'S'}")
            pf = QFont(pill.font())
            pf.setPointSizeF(theme.pt("tag"))
            pf.setWeight(QFont.Weight.ExtraBold)
            pill.setFont(pf)
            pill.setStyleSheet(f"background: {C['ok_ground']}; color: {C['ok']}; border-radius: 2px; padding: 0 7px;")
            pill.setFixedHeight(20)
            rl.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
            row.setAccessibleName(f"{name}: {what}")
            body.addWidget(row)
        if len(games) > self.SHOWN:
            more = len(games) - self.SHOWN
            ml = QLabel(f"and {more} more game{'' if more == 1 else 's'}")
            ml.setStyleSheet(f"color: {C['text3']};")
            body.addWidget(ml)
        self.remember = QCheckBox("Always apply when I quit, and don't ask again")
        self.remember.setObjectName("spopcheck")
        self.remember.setToolTip("You can turn this off again in the Steam pop-over in the status bar")
        body.addWidget(self.remember)
        outer.addLayout(body)

        foot = QFrame()
        foot.setObjectName("dialogfoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 12, 18, 12)
        fl.setSpacing(8)
        discard = QPushButton("Discard")
        discard.setIcon(icons.icon("trash", 15, C["bad"]))
        discard.setStyleSheet(f"QPushButton {{ background: transparent; color: {C['bad']}; padding: 0 8px; }}"
                              f"QPushButton:hover {{ background: rgba(239,123,108,.25); color: white; }}")
        discard.setAccessibleDescription("Quit and throw away the artwork that hasn't been applied")
        discard.clicked.connect(lambda: self._done("discard"))
        fl.addWidget(discard)
        fl.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        fl.addWidget(cancel)
        self.apply = QPushButton("Apply and Quit")
        self.apply.setIcon(icons.icon("check", 15, "white"))
        self.apply.setProperty("kind", "confirm")
        self.apply.setDefault(True)
        self.apply.clicked.connect(lambda: self._done("apply"))
        fl.addWidget(self.apply)
        outer.addWidget(foot)

    def _done(self, choice: str):
        self.choice = choice
        self.accept()


def tell(parent: QWidget, title: str, text: str, icon=QMessageBox.Icon.Warning):
    """A message with an OK button, over the dimmed window (a click on the dim closes it too)."""
    run_dialog(QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent), delete=True)


def _dialog_frame(dlg: QDialog, heading: str) -> tuple[QVBoxLayout, QHBoxLayout]:
    """The frame the app's own windows share: a heading and body, then a button strip at the foot."""
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    body = QVBoxLayout()
    body.setContentsMargins(18, 18, 18, 18)
    body.setSpacing(10)
    title = QLabel(heading)
    title.setWordWrap(True)
    f = QFont(title.font())
    f.setPointSizeF(theme.pt("heading"))
    f.setWeight(QFont.Weight.Bold)
    title.setFont(f)
    body.addWidget(title)
    outer.addLayout(body, 1)
    foot = QFrame()
    foot.setObjectName("dialogfoot")
    fl = QHBoxLayout(foot)
    fl.setContentsMargins(18, 12, 18, 12)
    fl.setSpacing(8)
    fl.addStretch(1)
    outer.addWidget(foot)
    return body, fl


class ConfirmDialog(QDialog):
    """A yes-or-no question in the app's own style: a heading, a sentence, Cancel and a verb button
    (red for something that removes or throws away)."""

    def __init__(self, parent, heading: str, text: str, verb: str, icon: str = "", bad: bool = False):
        super().__init__(parent)
        self.setWindowTitle(QGuiApplication.applicationDisplayName() or "Steam Artwork for Non-Steam Games")
        self.setMinimumWidth(420)
        body, fl = _dialog_frame(self, heading)
        if text:
            words = QLabel(text)
            words.setWordWrap(True)
            words.setStyleSheet(f"color: {C['text2']};")
            body.addWidget(words)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        fl.addWidget(cancel)
        self.ok = QPushButton(verb)
        if icon:
            self.ok.setIcon(icons.icon(icon, 15, "white"))
        self.ok.setProperty("kind", "danger" if bad else "confirm")
        self.ok.clicked.connect(self.accept)
        fl.addWidget(self.ok)
        cancel.setDefault(bad)  # Enter doesn't throw anything away
        self.ok.setDefault(not bad)
        (cancel if bad else self.ok).setFocus()


class AboutDialog(QDialog):
    """The app's name, version and where the artwork comes from."""

    def __init__(self, parent, name: str, version: str):
        super().__init__(parent)
        self.setWindowTitle(f"About {name}")
        self.setMinimumWidth(420)
        body, fl = _dialog_frame(self, name)
        head = body.itemAt(0).widget()
        body.removeWidget(head)
        top = QHBoxLayout()
        top.setSpacing(14)
        mark = QLabel()
        mark.setPixmap(icons.app_pixmap(48, self.devicePixelRatioF()))
        mark.setFixedSize(48, 48)
        top.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(head)
        ver = QLabel(f"Version {version}")
        ver.setStyleSheet(f"color: {C['text3']};")
        col.addWidget(ver)
        top.addLayout(col, 1)
        body.insertLayout(0, top)
        words = QLabel(f"Artwork comes from <a href='https://www.steamgriddb.com' style='color:{C['blue_ink']};"
                       " text-decoration:none'>SteamGridDB</a> and its artists.<br>"
                       "Not affiliated with Valve or SteamGridDB.")
        words.setTextFormat(Qt.TextFormat.RichText)
        words.setOpenExternalLinks(True)
        words.setWordWrap(True)
        words.setStyleSheet(f"color: {C['text2']};")
        body.addWidget(words)
        close = QPushButton("Close")
        close.setProperty("kind", "confirm")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        fl.addWidget(close)


def friendly_time(t: float, now: float | None = None) -> tuple[str, str]:
    """("Today, 06:30", "14 minutes ago") for a file time."""
    now = time.time() if now is None else now
    lt, ln = time.localtime(t), time.localtime(now)
    days = (time.mktime(ln[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(lt[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    clock = time.strftime("%H:%M", lt)
    if round(days) == 0:
        when = f"Today, {clock}"
    elif round(days) == 1:
        when = f"Yesterday, {clock}"
    elif lt.tm_year == ln.tm_year:
        when = time.strftime("%-d %b, %H:%M", lt) if sys.platform != "win32" else time.strftime("%d %b, %H:%M", lt)
    else:
        when = time.strftime("%d %b %Y", lt)
    secs = max(0, now - t)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size:
            n = int(secs // size)
            return when, f"{n} {unit}{'' if n == 1 else 's'} ago"
    return when, "just now"


def _short(name: str, n: int = 26) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def difference_tags(d) -> list[tuple[str, str]]:
    """(text, tone) tags saying what restoring would change; tone is "add", "del", "chg" or ""."""
    tags = []
    if d.back:
        tags.append((f"{_short(d.back[0])} comes back" if len(d.back) == 1 else f"{len(d.back)} games come back",
                     "add"))
    if d.gone:
        tags.append((f"{_short(d.gone[0])} not in it" if len(d.gone) == 1 else f"{len(d.gone)} games not in it",
                     "del"))
    if d.renamed:
        tags.append((f"{d.renamed} name{'' if d.renamed == 1 else 's'} different", "chg"))
    if d.icons:
        tags.append((f"{d.icons} icon{'' if d.icons == 1 else 's'} different", "chg"))
    return tags or [("Same as now", "")]


TAG_STYLE = {"add": ("ok_ground", "ok"), "del": ("bad_ground", "bad"), "chg": ("chg_ground", "blue_ink"), "": ("raised", "text3")}


class BackupRow(QAbstractButton):
    """One saved copy: a radio circle, when it was made, what restoring it changes, and its game count."""

    def __init__(self, path, when: str, ago: str, count: int, tags: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.path = path
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setEnabled(count >= 0)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(42, 10, 12, 10)
        lay.setSpacing(12)
        words = QVBoxLayout()
        words.setSpacing(5)
        extra = [x for x in (ago, f"{count} game{'' if count == 1 else 's'}" if count >= 0 else "") if x]
        title = QLabel(f"<b>{when}</b>" + "".join(f"<span style='color:{C['text3']}'>&nbsp; · &nbsp;{x}</span>"
                                                  for x in extra))
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        words.addWidget(title)
        tag_host = QWidget()
        tag_host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tl = FlowLayout(tag_host, spacing=6)  # the tags wrap onto a second line rather than run off the row
        for text, tone in tags if count >= 0 else [("Can't be read", "del")]:
            bg, fg = TAG_STYLE[tone]
            t = QLabel(text.upper())
            f = QFont(t.font())
            f.setPointSizeF(theme.pt("tag"))
            f.setWeight(QFont.Weight.ExtraBold)
            t.setFont(f)
            t.setFixedHeight(18)
            t.setStyleSheet(f"background: {C[bg]}; color: {C[fg]}; border-radius: 2px; padding: 0 6px;")
            tl.addWidget(t)
        words.addWidget(tag_host)
        lay.addLayout(words, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(f"{when}, {ago}, {count} games: " + ", ".join(t for t, _ in tags))

    def sizeHint(self):
        return self.layout().sizeHint()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self.layout().heightForWidth(w)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        if on:
            p.setBrush(QColor(26, 159, 255, 26))
            p.setPen(QColor(C["blue"]))
        else:
            p.setBrush(QColor(C["raised"] if self.underMouse() and self.isEnabled() else C["panel"]))
            p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 3, 3)
        c = QRectF(12, self.height() / 2 - 9, 18, 18)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if on:
            p.setPen(QPen(QColor(C["blue"]), 5))
            p.drawEllipse(c.adjusted(2.5, 2.5, -2.5, -2.5))
        else:
            p.setPen(QPen(QColor(C["text3"]), 1.5))
            p.drawEllipse(c.adjusted(0.75, 0.75, -0.75, -0.75))
        if theme.kbd_focus(self):
            p.setPen(QPen(QColor(C["blue"]), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 3, 3)
        if not self.isEnabled():
            p.fillRect(self.rect(), QColor(23, 26, 33, 110))


class RestoreDialog(QDialog):
    """Pick an earlier copy of Steam's game list to put back."""

    def __init__(self, parent, backups, now: list | None = None, steam_open: bool = False):
        """backups: (path, game count) newest first; now: the shortcuts in the list today."""
        from ..shortcuts import compare

        super().__init__(parent)
        self.setWindowTitle("Restore Steam's game list")
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(18, 18, 18, 18)
        body.setSpacing(12)
        title = QLabel("Put back an earlier game list")
        f = QFont(title.font())
        f.setPointSizeF(theme.pt("heading"))
        f.setWeight(QFont.Weight.Bold)
        title.setFont(f)
        body.addWidget(title)
        if steam_open:
            warn = QLabel("Steam is open. It'll be closed first, or it would put its own list back when it quits.")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"background: rgba(227,179,65,.1); border-left: 3px solid {C['wait']};"
                               f" padding: 10px 12px; color: {C['text']};")
            body.addWidget(warn)

        rows = QWidget()
        rl = QVBoxLayout(rows)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self.rows: list[BackupRow] = []
        for path, count in backups:
            try:
                when, ago = friendly_time(path.stat().st_mtime)
                tags = difference_tags(compare(path, now)) if now is not None and count >= 0 else []
            except Exception:
                when, ago, tags, count = path.name, "", [], -1
            row = BackupRow(path, when, ago, count, tags)
            row.toggled.connect(self._changed)
            rl.addWidget(row)
            self.rows.append(row)
        if not backups:
            empty = QLabel("No copies yet. One is saved the first time the app changes the list.")
            empty.setStyleSheet(f"color: {C['text3']}; padding: 12px;")
            rl.addWidget(empty)
        rl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(rows)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(min(3, max(1, len(backups))) * 64)
        scroll.setMaximumHeight(min(5, max(1, len(backups))) * 64 + 8)  # hugs its rows: no gap above the note
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        body.addWidget(scroll)
        info = QLabel("A copy is saved before every change. Restoring puts back game names, launch options "
                      "and icons; artwork images stay. Your list now is saved as a copy first.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {C['text3']};")
        fi = QFont(info.font())
        fi.setPointSizeF(theme.pt("faint"))
        info.setFont(fi)
        body.addWidget(info)
        outer.addLayout(body, 1)

        foot = QFrame()
        foot.setObjectName("dialogfoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 12, 18, 12)
        fl.setSpacing(8)
        fl.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        fl.addWidget(cancel)
        self.ok = QPushButton("Close Steam and Restore" if steam_open else "Restore")
        self.ok.setIcon(icons.icon("history", 15, "white"))
        self.ok.setProperty("kind", "confirm")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        fl.addWidget(self.ok)
        outer.addWidget(foot)
        self.resize(600, 460)
        self._changed()

    def _changed(self, *_):
        self.ok.setEnabled(self.selected() is not None)

    def selected(self):
        return next((r.path for r in self.rows if r.isChecked()), None)

