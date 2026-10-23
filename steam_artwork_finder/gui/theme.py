"""Colours and the Qt stylesheet, from DESIGN.md."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QObject, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPalette
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget

C = {
    "ground": "#171a21",
    "panel": "#1e2329",
    "stage": "#1b2838",
    "raised": "#2a3442",
    "tray": "#202935",
    "line": "#313b48",
    "select": "#3d4c5e",
    "text": "#dcdedf",
    "text2": "#a3adb8",
    "text3": "#8a95a2",
    "blue": "#1a9fff",
    "blue_ink": "#66c0f4",
    "action": "#1474c8",
    "action_hover": "#1a84dc",
    "go": "#3f7e1d",
    "go_hover": "#4b9123",
    "warn": "#e3b341",
    "warn_ground": "#3a3220",
    "info_ground": "#1f3346",
    "bad": "#ef7b6c",
    "danger": "#b8463a",  # the filled button of a question that removes or throws away
    "danger_hover": "#c95346",
    "input": "#14181e",
    "bar": "#202935",
    "ok": "#90ba3c",
    "wait": "#e3b341",
    "off": "#ef5b4c",
    "bad_ground": "#3b2422",  # behind red tags (MISSING, NOT FOUND)
    "ok_ground": "#2c3a1a",  # behind green tags (2 CHANGES)
    "chg_ground": "#23364a",  # behind blue tags (1 NAME DIFFERENT)
    "get": "#1f7a55",  # Get All Artwork: a cooler green than Apply
    "get_hover": "#258d63",
    "disabled_bg": "#242c37",
    "disabled_text": "#5d6875",  # a disabled menu row
    "disabled_icon": "#4d5764",
    "disabled_line": "rgba(255,255,255,0.08)",  # the faint outline round a disabled button (not a QColor)
    "menu": "#1d232b",  # popup menus
    "foot": "#1a1e25",  # the button strip at the bottom of a dialog
    "track_off": "#4a5462",  # a switch that's off; a place Steam wasn't found
    "dash": "#3a4656",  # dashed outlines: drop zone, empty option strip
    "undo_ink": "#0f5aa0",  # the Undo link on the light toast
    # Buttons are drawn the Steam way (style B): green and blue gradients for the main actions,
    # and Steam's pale-blue "hover fade" for everything else. Each pair runs left to right.
    "fade": "rgba(103,193,245,0.2)",  # a plain button, and what a hovered quiet one fills with
    "fade_ink": "#67c1f5",
    "fade_a": "#66c0f4", "fade_b": "#417a9b",  # a plain button under the pointer
    "fade_down_a": "#4f9cc8", "fade_down_b": "#2f5c76",
    "go_a": "#75b022", "go_b": "#588a1b", "go_ink": "#d2efa9",  # Apply
    "go_hot_a": "#8ed629", "go_hot_b": "#6aa621",
    "go_down_a": "#5e8f1b", "go_down_b": "#466e15",
    "get_a": "#2fb88a", "get_b": "#1d7a58", "get_ink": "#d4f5e7",  # Get All Artwork
    "get_hot_a": "#3fd6a2", "get_hot_b": "#23916a",
    "get_down_a": "#26966f", "get_down_b": "#176247",
    "blue_a": "#47bfff", "blue_b": "#1a44c2",  # Restart Steam, Save and the other confirm buttons
    "blue_hot_a": "#6fd0ff", "blue_hot_b": "#2c5be0",
    "blue_down_a": "#3aa0d8", "blue_down_b": "#163aa5",
    "red_a": "#d8604f", "red_b": "#9a3529", "red_ink": "#ffe1dc",  # a button that throws something away
    "red_hot_a": "#ef7461", "red_hot_b": "#b5402f",
    "red_down_a": "#b84f40", "red_down_b": "#7f2b21",
}


def grad(a: str, b: str) -> str:
    """A Steam button fill: colour `a` on the left to `b` on the right (keys of C)."""
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0.05 {C[a]}, stop:0.95 {C[b]})"


def q(name: str) -> QColor:
    return QColor(C[name])


# Type scale from DESIGN.md, as multiples of the system UI font so the user's
# font-size setting (KDE, Steam Deck) carries through the whole window.
SCALE = {
    "title": 1.9, "heading": 1.3, "appname": 1.2, "body": 1.0, "control": 1.0,
    "faint": 0.9, "caption": 0.85, "label": 0.8, "tag": 0.72, "go": 1.1,
}


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """`a` blended toward `b` by t (0 to 1)."""
    return QColor.fromRgbF(*(x + (y - x) * t for x, y in zip(a.getRgbF(), b.getRgbF())))


class SwitchGlide(QObject):
    """Slides a switch's knob (and fades its track colour) when its button is checked or
    unchecked, instead of jumping. `at` runs from 0 (off) to 1 (on); paint from it."""

    MS = 120

    def __init__(self, button: QAbstractButton):
        super().__init__(button)
        self.button = button
        self.at = 1.0 if button.isChecked() else 0.0
        self._anim = QVariantAnimation(self, duration=self.MS, easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        button.toggled.connect(self._go)

    def _go(self, on: bool):
        if not self.button.isVisible():  # set while hidden: no one to see it slide
            self._anim.stop()
            self.at = 1.0 if on else 0.0
            return
        self._anim.stop()
        self._anim.setStartValue(self.at)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _step(self, v):
        self.at = float(v)
        self.button.update()


def base_pt() -> float:
    app = QGuiApplication.instance()
    size = app.font().pointSizeF() if app else 10.0
    return size if size > 0 else 10.0


def pt(name: str) -> float:
    return round(base_pt() * SCALE[name], 1)


def text_width(text: str) -> int:
    """How wide `text` is in the base font, for widths that must fit copy at any font size."""
    app = QGuiApplication.instance()
    return QFontMetrics(app.font()).horizontalAdvance(text) if app else 6 * len(text)


# Focus rings show only for keyboard focus, as in Steam: not after a click or when the app
# moves focus itself after a click (a pop-over closing, a page opening).
_NAV_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End,
             Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}


class _FocusTracker(QObject):
    """Marks the focused widget with a `kbd` property when the focus came from the keyboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_key = False  # was the latest input a key press (rather than a click or a touch)?

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.KeyPress:
            self.last_key = True
            focus = QApplication.focusWidget()
            if focus is not None and obj is focus and e.key() in _NAV_KEYS and not focus.property("kbd"):
                _mark(focus, True)  # clicked, then went on with the arrow keys
        elif t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.TouchBegin):
            self.last_key = False
        elif t == QEvent.Type.FocusIn and isinstance(obj, QWidget):
            # Qt also gives Tab as the reason when it moves focus on by itself (the focused
            # button was disabled or hidden), so only a key press counts as the keyboard.
            r = e.reason()
            _mark(obj, r != Qt.FocusReason.MouseFocusReason and self.last_key)
        elif t == QEvent.Type.FocusOut and isinstance(obj, QWidget) and obj.property("kbd"):
            _mark(obj, False)
        return False


def _mark(w: QWidget, kbd: bool):
    if bool(w.property("kbd")) == kbd:
        return
    w.setProperty("kbd", kbd)
    if isinstance(w, QAbstractButton):  # the stylesheet ring
        w.style().unpolish(w)
        w.style().polish(w)
    w.update()


_tracker: _FocusTracker | None = None


def track_focus():
    """Start telling keyboard focus from mouse focus (once per app)."""
    global _tracker
    app = QApplication.instance()
    if app is not None and _tracker is None:
        _tracker = _FocusTracker(app)
        app.installEventFilter(_tracker)


def last_input_was_key() -> bool:
    """Was the latest input a key press, rather than a click or a touch?"""
    return _tracker is not None and _tracker.last_key


def kbd_focus(w: QWidget) -> bool:
    """Should `w` draw a focus ring: it has focus, and the focus came from the keyboard."""
    return w.hasFocus() and bool(w.property("kbd"))


def _set_open(btn: QWidget, on: bool):
    try:
        btn.setProperty("open", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    except RuntimeError:  # the button was deleted while its menu showed
        pass


def hold_open(btn: QWidget):
    """Draw `btn` pressed for as long as the popup menu it just opened stays open."""
    pop = QApplication.activePopupWidget()
    if pop is None or not hasattr(pop, "closed"):
        return
    _set_open(btn, True)
    pop.closed.connect(lambda: _set_open(btn, False))


def stylesheet() -> str:
    c = C
    tick = (Path(__file__).parent / "icons" / "tick.svg").as_posix()
    return f"""
    QWidget {{ color: {c['text']}; }}
    QMainWindow, #header, QDialog, QMenu {{ background: {c['ground']}; }}
    #side {{ background: {c['panel']}; }}
    #stage, #stage > QWidget > QWidget {{ background: {c['stage']}; }}
    #tray {{ background: {c['tray']}; border-left: 1px solid {c['line']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line']}; }}

    QPushButton {{
        background: {c['fade']}; color: {c['fade_ink']}; border: none; border-radius: 2px;
        min-height: 40px; padding: 0 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {grad('fade_a', 'fade_b')}; color: white; }}
    QPushButton:pressed {{ background: {grad('fade_down_a', 'fade_down_b')}; color: white; }}
    QPushButton:focus {{ outline: none; }}
    QPushButton[kbd="true"]:focus {{ border: 2px solid white; }}
    QPushButton[kind="go"] {{ background: {grad('go_a', 'go_b')}; color: {c['go_ink']}; padding: 0 18px; font-size: {pt('go')}pt; font-weight: 700; }}
    QPushButton[kind="go"]:hover {{ background: {grad('go_hot_a', 'go_hot_b')}; color: white; }}
    QPushButton[kind="go"]:pressed {{ background: {grad('go_down_a', 'go_down_b')}; color: white; }}
    QPushButton[kind="confirm"] {{ background: {grad('blue_a', 'blue_b')}; color: white; font-weight: 700; }}
    QPushButton[kind="confirm"]:hover {{ background: {grad('blue_hot_a', 'blue_hot_b')}; }}
    QPushButton[kind="confirm"]:pressed {{ background: {grad('blue_down_a', 'blue_down_b')}; }}
    QPushButton[kind="danger"] {{ background: {grad('red_a', 'red_b')}; color: {c['red_ink']}; font-weight: 700; }}
    QPushButton[kind="danger"]:hover {{ background: {grad('red_hot_a', 'red_hot_b')}; color: white; }}
    QPushButton[kind="danger"]:pressed {{ background: {grad('red_down_a', 'red_down_b')}; color: white; }}
    QPushButton[drop="true"] {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(102,192,244,0.35), stop:1 rgba(65,122,155,0.35));
        border: 1px dashed {c['fade_ink']}; color: white;
    }}
    QPushButton[kind="quiet"] {{ background: transparent; color: {c['fade_ink']}; padding: 0 6px; min-height: 40px; }}
    QPushButton[kind="quiet"]:hover {{ background: {c['fade']}; color: white; }}
    QPushButton[kind="quiet"]:pressed {{ background: rgba(103,193,245,0.12); color: white; }}
    QPushButton[kind="seg"] {{ background: transparent; color: {c['text2']}; min-height: 40px; padding: 0 10px; }}
    QPushButton[kind="seg"]:hover {{ background: {c['fade']}; color: white; }}
    QPushButton[kind="seg"]:checked {{ background: {grad('fade_a', 'fade_b')}; color: white; }}
    QPushButton[kind="chip"] {{
        background: rgba(103,193,245,0.12); border: none; border-radius: 20px;
        min-height: 40px; padding: 0 14px; color: #8fcff4;
    }}
    QPushButton[kind="chip"]:hover {{ background: {c['fade']}; color: white; }}
    QPushButton[kind="chip"]:checked {{ background: {grad('fade_a', 'fade_b')}; color: white; }}
    /* Off: a flat, faint fill inside a faint 1px outline. The outline takes the place of 1px of
       padding, so nothing moves. Last, so it wins over every kind above. */
    QPushButton:disabled {{
        color: {c['text3']}; background: rgba(255,255,255,0.04);
        border: 1px solid {c['disabled_line']}; min-height: 38px; padding: 0 13px;
    }}
    QPushButton[kind="go"]:disabled {{ padding: 0 17px; }}
    QPushButton[kind="seg"]:disabled {{ padding: 0 9px; }}
    QPushButton[kind="quiet"]:disabled {{ background: transparent; border: none; min-height: 40px; padding: 0 6px; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 2px; min-width: 40px; min-height: 40px; }}
    QToolButton:hover {{ background: {c['fade']}; }}
    QToolButton:pressed, QToolButton[open="true"] {{ background: rgba(103,193,245,0.32); border: 1px solid {c['fade_ink']}; }}
    QToolButton::menu-indicator {{ image: none; }}

    QLineEdit {{
        background: {c['input']}; border: 1px solid transparent; border-radius: 3px;
        min-height: 40px; padding: 0 10px; selection-background-color: {c['action']};
    }}
    QLineEdit:focus {{ border-color: {c['blue']}; }}
    QComboBox {{ background: {c['raised']}; border: none; border-radius: 3px; min-height: 36px; padding: 0 10px; }}
    QComboBox QAbstractItemView {{ background: {c['raised']}; selection-background-color: {c['select']}; }}
    QCheckBox {{ color: {c['text2']}; spacing: 8px; min-height: 40px; }}
    QCheckBox::indicator {{ width: 22px; height: 22px; border-radius: 3px; background: {c['raised']}; border: 1px solid {c['line']}; }}
    QCheckBox::indicator:checked {{ background: {c['action']}; border-color: {c['action']}; image: url("{tick}"); }}

    QMenu {{ border: 1px solid {c['line']}; padding: 4px; }}
    QMenu::item {{ padding: 8px 18px; border-radius: 3px; }}
    QMenu::item:selected {{ background: {c['select']}; }}
    QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 6px; }}

    QListView, QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item:selected {{ background: {c['select']}; border-radius: 3px; }}
    QListWidget[plain="true"]::item {{ min-height: 44px; padding: 0 10px; }}
    QTabBar {{ background: {c['input']}; border-radius: 3px; }}
    QTabBar::tab {{ background: transparent; color: {c['text2']}; min-height: 40px; padding: 0 12px; border-radius: 3px; margin: 3px; font-weight: 600; }}
    #slottabs::tab {{ padding: 0 7px; margin: 3px 1px; }}
    QTabBar::tab:selected {{ background: {c['select']}; color: white; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c['raised']}; border-radius: 5px; min-height: 40px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['select']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {c['raised']}; border-radius: 5px; min-width: 40px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QSplitter::handle {{ background: {c['line']}; }}
    QProgressBar {{ background: {c['raised']}; border: none; border-radius: 2px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {c['blue']}; border-radius: 2px; }}

    #title {{ font-size: {pt('title')}pt; font-weight: 700; }}
    #heading {{ font-size: {pt('heading')}pt; font-weight: 700; }}
    #appname {{ font-size: {pt('appname')}pt; font-weight: 700; }}
    #muted {{ color: {c['text2']}; }}
    #faint {{ color: {c['text3']}; font-size: {pt('faint')}pt; }}
    #label {{ color: {c['text3']}; font-size: {pt('label')}pt; font-weight: 700; letter-spacing: 1px; }}
    #tagmiss {{ background: {c['bad_ground']}; color: {c['bad']}; border-radius: 2px; padding: 2px 6px; font-size: {pt('label')}pt; font-weight: 700; }}
    #summary {{ background: {c['input']}; border-radius: 3px; }}
    #notice[tone="warn"] {{ background: {c['warn_ground']}; }}
    #notice[tone="info"] {{ background: {c['info_ground']}; }}
    #toast {{ background: {c['text']}; border-radius: 4px; }}
    #toast QLabel {{ color: {c['ground']}; font-weight: 600; }}
    #toast QPushButton {{ background: transparent; color: {c['undo_ink']}; min-height: 40px; }}
    #toast QPushButton:hover {{ color: {c['action']}; text-decoration: underline; }}
    #rowline {{ background: {c['line']}; max-height: 1px; }}
    #spop {{ background: {c['ground']}; border: 1px solid {c['line']}; border-radius: 4px; }}
    #spop QPushButton {{ min-height: 36px; padding: 0 12px; }}
    #spopcheck {{ color: {c['text']}; spacing: 10px; }}
    #spopx {{ min-width: 26px; min-height: 26px; max-width: 26px; max-height: 26px; }}
    #fseg {{ background: rgba(0,0,0,0.3); border-radius: 4px; }}
    #fseg QPushButton {{ background: transparent; color: {c['text2']}; min-height: 34px; border-radius: 2px; }}
    #fseg QPushButton:hover {{ background: {c['fade']}; color: white; }}
    #fseg QPushButton:checked {{ background: {grad('fade_a', 'fade_b')}; color: white; }}
    #facct {{ background: {c['panel']}; border-radius: 3px; }}
    #spopcheck::indicator {{ width: 18px; height: 18px; border-radius: 3px; border: 1px solid {c['text3']}; background: {c['raised']}; }}
    #spopcheck::indicator:checked {{ background: {c['action']}; border-color: {c['action']}; image: url("{tick}"); }}
    #dialogfoot {{ background: {c['foot']}; border-top: 1px solid {c['line']}; }}
    #empty {{ background: {c['raised']}; border-radius: 3px; color: {c['text2']}; padding: 12px; }}
    """


def palette() -> QPalette:
    pal = QPalette()
    R = QPalette.ColorRole
    for role, name in (
        (R.Window, "ground"), (R.Base, "input"), (R.AlternateBase, "panel"), (R.Button, "raised"),
        (R.Text, "text"), (R.WindowText, "text"), (R.ButtonText, "text"), (R.ToolTipBase, "raised"),
        (R.ToolTipText, "text"), (R.Highlight, "action"), (R.Link, "blue_ink"), (R.PlaceholderText, "text3"),
    ):
        pal.setColor(role, q(name))
    pal.setColor(R.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.Text, q("text3"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.ButtonText, q("text3"))
    return pal
