"""Colours and the Qt stylesheet, from DESIGN.md."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPalette

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
    "input": "#14181e",
}


def q(name: str) -> QColor:
    return QColor(C[name])


# Type scale from DESIGN.md, as multiples of the system UI font so the user's
# font-size setting (KDE, Steam Deck) carries through the whole window.
SCALE = {
    "title": 1.9, "heading": 1.3, "appname": 1.2, "body": 1.0, "control": 1.0,
    "faint": 0.9, "caption": 0.85, "label": 0.8, "tag": 0.72, "go": 1.1,
}


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


def stylesheet() -> str:
    c = C
    return f"""
    QWidget {{ color: {c['text']}; }}
    QMainWindow, #header, QDialog, QMenu {{ background: {c['ground']}; }}
    #side {{ background: {c['panel']}; }}
    #stage, #stage > QWidget > QWidget {{ background: {c['stage']}; }}
    #tray {{ background: {c['tray']}; border-left: 1px solid {c['line']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line']}; }}

    QPushButton {{
        background: {c['raised']}; border: none; border-radius: 3px;
        min-height: 40px; padding: 0 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {c['select']}; }}
    QPushButton:disabled {{ color: {c['text3']}; background: #242c37; }}
    QPushButton:focus {{ outline: none; border: 2px solid {c['blue']}; }}
    QPushButton[kind="go"] {{ background: {c['go']}; color: white; padding: 0 18px; font-size: {pt('go')}pt; }}
    QPushButton[kind="go"]:hover {{ background: {c['go_hover']}; }}
    QPushButton[kind="confirm"] {{ background: {c['action']}; color: white; }}
    QPushButton[kind="confirm"]:hover {{ background: {c['action_hover']}; }}
    QPushButton[kind="confirm"]:disabled {{ background: #242c37; color: {c['text3']}; }}
    QPushButton[kind="quiet"] {{ background: transparent; color: {c['blue_ink']}; padding: 0 6px; min-height: 40px; }}
    QPushButton[kind="quiet"]:hover {{ text-decoration: underline; }}
    QPushButton[kind="seg"] {{ background: transparent; color: {c['text2']}; min-height: 40px; padding: 0 10px; }}
    QPushButton[kind="seg"]:checked {{ background: {c['select']}; color: white; }}
    QPushButton[kind="chip"] {{
        background: transparent; border: 1px solid {c['line']}; border-radius: 20px;
        min-height: 40px; padding: 0 14px; color: {c['text2']};
    }}
    QPushButton[kind="chip"]:checked {{ background: {c['text']}; color: {c['ground']}; }}
    QToolButton {{ background: transparent; border: none; border-radius: 3px; min-width: 40px; min-height: 40px; }}
    QToolButton:hover {{ background: {c['raised']}; }}
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
    QCheckBox::indicator:checked {{ background: {c['action']}; border-color: {c['action']}; }}

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
    #tagmiss {{ background: #3b2422; color: {c['bad']}; border-radius: 2px; padding: 2px 6px; font-size: {pt('label')}pt; font-weight: 700; }}
    #keybar {{ background: {c['info_ground']}; border-radius: 3px; }}
    #summary {{ background: {c['input']}; border-radius: 3px; }}
    #notice[tone="warn"] {{ background: {c['warn_ground']}; }}
    #notice[tone="info"] {{ background: {c['info_ground']}; }}
    #toast {{ background: {c['text']}; border-radius: 4px; }}
    #toast QLabel {{ color: {c['ground']}; font-weight: 600; }}
    #toast QPushButton {{ background: transparent; color: #0f5aa0; min-height: 40px; }}
    #rowline {{ background: {c['line']}; max-height: 1px; }}
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
