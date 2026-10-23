"""The dimmed backdrop behind menus and pop-outs: it darkens the window, keeps the mouse
(hover, clicks, scrolling) off everything underneath, and a click on it closes whatever
is open."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QWidget
from shiboken6 import isValid

DIM = 110  # alpha of the darkening, fully faded in
FADE_MS = 140  # as the pop-overs' and menus' own fades


class Scrim(QWidget):
    """One per main window, covering all of it. Whatever opens over it calls cover(owner,
    close) and, on closing, uncover(owner); several can be open at once (a menu over a
    pop-over), and the last one open is the one a click closes."""

    clicked = Signal()

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setMouseTracking(True)  # swallows hover, so nothing underneath lights up
        self._alpha = 0.0
        self._owners: list[tuple[QWidget, Callable | None]] = []
        self._anim = QVariantAnimation(self, duration=FADE_MS)
        self._anim.valueChanged.connect(self._set_alpha)
        self._anim.finished.connect(self._faded)
        window.installEventFilter(self)
        self._popups = _PopupWatch(self)
        self.hide()

    # -- opening and closing -------------------------------------------------

    def cover(self, owner: QWidget, close: Callable | None = None):
        """Dim the window under `owner`. `close` runs when the dim part is clicked (menus that
        are popup windows close themselves, so they pass none). Raise `owner` after this."""
        self._owners = [(w, c) for w, c in self._owners if w is not owner] + [(owner, close)]
        self.setGeometry(self.parentWidget().rect())
        if not self.isVisible():
            self._alpha = 0.0
            self.show()
        self.raise_()
        self._fade(1.0)
        QTimer.singleShot(0, self, lambda: self._take_focus(owner))

    def _take_focus(self, owner: QWidget):
        """Typing goes to what's open, not to the page underneath it."""
        if not self._owners or self._owners[-1][0] is not owner or not owner.isVisible() or owner.isWindow():
            return
        focus = QApplication.focusWidget()
        if focus is None or not (focus is owner or owner.isAncestorOf(focus)):
            owner.setFocus(Qt.FocusReason.PopupFocusReason)

    def uncover(self, owner: QWidget):
        if not self._owners:
            return
        before = len(self._owners)
        self._owners = [(w, c) for w, c in self._owners if w is not owner]
        if before and not self._owners:
            self._fade(0.0)
        elif len(self._owners) < before:
            under = self._owners[-1][0]
            if isValid(under) and not under.isWindow():
                under.raise_()  # what was open under it (a pop-over under a dialog) comes back above the dim

    def is_covering(self) -> bool:
        return bool(self._owners)

    def top(self) -> QWidget | None:
        """What's open on top: the one a click on the dim part closes."""
        return self._owners[-1][0] if self._owners else None

    def _top_window(self) -> QWidget | None:
        """The dialog on top, when what's on top is a window of its own."""
        top = self.top()
        return top if top is not None and isValid(top) and top.isWindow() and top.isVisible() else None

    def _fade(self, end: float):
        self._anim.stop()
        self._anim.setStartValue(self._alpha)
        self._anim.setEndValue(end)
        self._anim.start()

    def _set_alpha(self, v):
        self._alpha = float(v)
        self.update()

    def _faded(self):
        if not self._owners:
            self.hide()

    # -- drawing and the mouse ------------------------------------------------

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, round(DIM * self._alpha)))

    def mousePressEvent(self, e):
        e.accept()
        if self._owners:
            _owner, close = self._owners[-1]
            self.clicked.emit()
            if close is not None:
                close()

    def mouseReleaseEvent(self, e):
        e.accept()

    def mouseDoubleClickEvent(self, e):
        e.accept()

    def mouseMoveEvent(self, e):
        e.accept()

    def wheelEvent(self, e):
        e.accept()  # the page underneath doesn't scroll

    def contextMenuEvent(self, e):
        e.accept()

    def eventFilter(self, obj, e):
        if obj is not self.parentWidget():
            return False
        t = e.type()
        if t == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        elif t == QEvent.Type.WindowActivate and self._top_window() is not None:
            # The window behind a dialog came to the front (its title bar, or the task switcher):
            # bring the dialog back over it, so typing can't reach the dimmed page.
            QTimer.singleShot(0, self, self._front)
        elif t == QEvent.Type.Close and self._top_window() is not None:
            # Closing the window behind a dialog cancels the dialog first, as a modal one would.
            _owner, close = self._owners[-1]
            if close is not None:
                close()
            e.ignore()
            return True
        return False

    def _front(self):
        dlg = self._top_window()
        if dlg is not None:
            dlg.raise_()
            dlg.activateWindow()


def scrim_of(w: QWidget) -> Scrim | None:
    s = getattr(w.window(), "scrim", None)
    return s if isinstance(s, Scrim) else None


class _PopupWatch(QObject):
    """Menus are popup windows of their own: dim the main window while one is open."""

    def __init__(self, scrim: Scrim):
        super().__init__(scrim)
        self.scrim = scrim
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        t = e.type()
        # A spontaneous hide is the window being minimised: what's open stays open (and dimmed)
        # for when it comes back.
        if t == QEvent.Type.Hide and isinstance(obj, QWidget) and not e.spontaneous():
            self.scrim.uncover(obj)
        elif t == QEvent.Type.Show and isinstance(obj, QWidget) and _is_menu(obj):
            parent = obj.parentWidget()
            # A menu made without a parent belongs to whichever window was in front.
            over = parent.window() if parent is not None else QApplication.activeWindow()
            if over is self.scrim.parentWidget():
                self.scrim.cover(obj)
        return False


def _is_menu(w: QWidget) -> bool:
    if not (w.isWindow() and w.windowType() == Qt.WindowType.Popup):
        return False
    from .gamemenu import FadingPopup

    return isinstance(w, (QMenu, FadingPopup))


def run_dialog(dlg: QDialog, delete: bool = False) -> int:
    """Show `dlg` over its window, which dims behind it; a click on the dim part cancels it, as
    for the pop-overs and menus. Returns its result, as exec() would. `delete` deletes it after.

    The dim does the blocking instead of a modal exec(): a modal dialog would keep the click on
    the dim part from ever arriving."""
    parent = dlg.parentWidget() if isinstance(dlg, QDialog) else None
    scrim = scrim_of(parent) if parent is not None else None
    if scrim is None or type(dlg).exec is not QDialog.exec:
        result = dlg.exec()  # no window to dim, or a dialog that runs itself
    else:
        result = QDialog.DialogCode.Rejected.value
        loop = QEventLoop()

        def finished(r: int):
            nonlocal result
            result = r
            loop.quit()

        dlg.finished.connect(finished)
        dlg.destroyed.connect(loop.quit)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        scrim.cover(dlg, dlg.reject)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        try:
            loop.exec()
        finally:
            scrim.uncover(dlg)
        if isValid(dlg):
            dlg.finished.disconnect(finished)
    if delete and isValid(dlg):
        dlg.deleteLater()
    return int(result)
