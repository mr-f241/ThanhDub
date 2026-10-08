from __future__ import annotations

from PyQt6 import QtGui, QtWidgets

# ------------------------------------------------------------------ bảng màu
# Mỗi bảng màu là một dict; các hằng số ở dưới module được nạp lại bởi set_mode()
# TRƯỚC khi các module UI import (xem ui/app.py) để mọi widget nhận đúng màu.

DARK = {
    "BG": "#15171c",
    "PANEL": "#1d2026",
    "PANEL_2": "#262a33",
    "BORDER": "#363b47",
    "TEXT": "#e8eaf0",
    "TEXT_DIM": "#8f96a6",
    "ACCENT": "#8b7cf8",
    "ACCENT_BRIGHT": "#a99cff",
    "ACCENT_DIM": "#453c86",
    "DANGER": "#f05b5b",
    "SUCCESS": "#41c07e",
    "WARNING": "#e8a83c",
    "HOVER": "#2d323d",
    "FOCUS_BG": "#2a2e39",
    "DISABLED": "#5d626d",
    "SCROLL": "#454a56",
    "DANGER_HOVER": "rgba(240, 91, 91, 0.15)",
    "ROW_ALT": "#21242b",
}

LIGHT = {
    "BG": "#f3f4f7",
    "PANEL": "#ffffff",
    "PANEL_2": "#e9ebf1",
    "BORDER": "#d2d5dd",
    "TEXT": "#21252d",
    "TEXT_DIM": "#69707f",
    "ACCENT": "#7c5cff",
    "ACCENT_BRIGHT": "#6a4be0",
    "ACCENT_DIM": "#6a4be0",
    "DANGER": "#d8434b",
    "SUCCESS": "#1e9e64",
    "WARNING": "#b97a12",
    "HOVER": "#e0e3ea",
    "FOCUS_BG": "#f0edff",
    "DISABLED": "#a2a8b4",
    "SCROLL": "#c3c7d1",
    "DANGER_HOVER": "rgba(216, 67, 75, 0.12)",
    "ROW_ALT": "#f4f5f9",
}

# màu track trên timeline (giống nhau cho cả hai giao diện)
TRACK_COLORS = {
    "image": "#8f7df5",
    "text": "#c076e8",
    "blur": "#55a8e0",
    "subtitle": "#e0ae4b",
    "dub": "#41c07e",
    "dub_stale": "#9a7a42",
    "original": "#656c78",
    "bgm": "#558a7a",
    "video": "#565b66",
}

MODE = "dark"


def set_mode(mode: str) -> None:
    """Chọn bảng màu tối/sáng và nạp lại các hằng số của module.

    Phải gọi TRƯỚC khi import các module UI khác (chúng import hằng số lúc nạp module).
    """
    global MODE
    palette = dict(LIGHT if mode == "light" else DARK)
    for key, value in palette.items():
        globals()[key] = value
    globals()["QSS"] = _build_qss(palette)
    # cập nhật tại chỗ để module đã import dict này thấy giá trị mới
    STATE_COLORS.update({"done": SUCCESS, "stale": WARNING, "error": DANGER, "running": ACCENT, "none": TEXT_DIM})
    MODE = "light" if mode == "light" else "dark"


def _build_qss(c: dict) -> str:
    return f"""
* {{
    font-family: "Segoe UI", "Inter", "Noto Sans", "Ubuntu", sans-serif;
    outline: none;
}}
QWidget {{ background: {c['BG']}; color: {c['TEXT']}; font-size: 13px; }}
QMainWindow::separator {{ background: {c['BORDER']}; width: 1px; height: 1px; }}
QToolTip {{
    background: {c['PANEL_2']}; color: {c['TEXT']}; border: 1px solid {c['ACCENT_DIM']};
    border-radius: 5px; padding: 5px 8px; font-size: 12px;
}}
QFrame#Panel, QWidget#Panel {{ background: {c['PANEL']}; border: 1px solid {c['BORDER']}; border-radius: 8px; }}
QFrame#NavRail {{ background: {c['PANEL']}; border-right: 1px solid {c['BORDER']}; }}
QLabel#Title {{ font-size: 19px; font-weight: 700; background: transparent; }}
QLabel#Subtitle {{ color: {c['TEXT_DIM']}; background: transparent; }}
QLabel {{ background: transparent; }}

QPushButton, QToolButton {{
    background: {c['PANEL_2']}; border: 1px solid {c['BORDER']}; border-radius: 6px; padding: 5px 12px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {c['ACCENT']}; background: {c['HOVER']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {c['ACCENT_DIM']}; color: white; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['DISABLED']}; border-color: {c['BORDER']}; background: {c['PANEL_2']}; }}
QPushButton#Primary {{
    background: {c['ACCENT']}; border-color: {c['ACCENT']}; color: white; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {c['ACCENT_BRIGHT']}; border-color: {c['ACCENT_BRIGHT']}; }}
QPushButton#Primary:pressed {{ background: {c['ACCENT_DIM']}; }}
QPushButton#Danger {{ border-color: {c['DANGER']}; color: {c['DANGER']}; }}
QPushButton#Danger:hover {{ background: {c['DANGER_HOVER']}; }}
QToolButton:checked {{ background: {c['ACCENT_DIM']}; border-color: {c['ACCENT']}; color: white; }}
QToolButton:disabled {{ background: transparent; }}
QToolButton#NavButton {{
    background: transparent; border: none; border-radius: 10px; padding: 6px 2px; color: {c['TEXT_DIM']};
}}
QToolButton#NavButton:hover {{ background: {c['PANEL_2']}; color: {c['TEXT']}; }}
QToolButton#NavButton:checked {{ background: {c['ACCENT_DIM']}; color: white; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {c['PANEL_2']}; border: 1px solid {c['BORDER']}; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {c['ACCENT']}; selection-color: white;
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {c['SCROLL']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {c['ACCENT']}; background: {c['FOCUS_BG']};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {c['DISABLED']}; background: {c['BG']}; border-color: {c['BORDER']};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {c['TEXT_DIM']}; margin-right: 7px;
}}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{ border-top-color: {c['ACCENT']}; }}
QComboBox QAbstractItemView {{
    background: {c['PANEL_2']}; border: 1px solid {c['BORDER']}; border-radius: 6px; padding: 3px;
    selection-background-color: {c['ACCENT_DIM']}; selection-color: white; outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; background: transparent; }}

QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; border-radius: 4px; }}
QCheckBox::indicator {{ border: 1px solid {c['BORDER']}; background: {c['PANEL_2']}; }}
QCheckBox::indicator:hover {{ border-color: {c['ACCENT']}; }}
QCheckBox::indicator:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT']}; }}
QCheckBox::indicator:disabled {{ border-color: {c['BORDER']}; background: {c['BG']}; }}
QRadioButton::indicator {{ border-radius: 8px; border: 1px solid {c['BORDER']}; background: {c['PANEL_2']}; }}
QRadioButton::indicator:hover {{ border-color: {c['ACCENT']}; }}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.8, fx:0.5, fy:0.5, stop:0.45 {c['ACCENT']}, stop:0.6 {c['PANEL_2']});
    border-color: {c['ACCENT']};
}}

QTableView, QTreeView, QListView, QListWidget, QTableWidget {{
    background: {c['PANEL']}; alternate-background-color: {c['ROW_ALT']}; border: 1px solid {c['BORDER']};
    border-radius: 6px; gridline-color: {c['BORDER']};
    selection-background-color: {c['ACCENT_DIM']}; selection-color: white;
}}
QTableView::item, QTreeView::item, QListView::item {{ padding: 2px 4px; border: none; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ border-radius: 3px; }}
QHeaderView::section {{
    background: {c['PANEL_2']}; border: none; border-right: 1px solid {c['BORDER']};
    border-bottom: 1px solid {c['BORDER']}; padding: 5px 8px; color: {c['TEXT_DIM']}; font-weight: 600;
}}
QTableCornerButton::section {{ background: {c['PANEL_2']}; border: none; }}

QTabWidget::pane {{ border: 1px solid {c['BORDER']}; border-radius: 6px; top: -1px; background: {c['PANEL']}; }}
QTabBar::tab {{ background: transparent; padding: 6px 14px; border: none; color: {c['TEXT_DIM']}; }}
QTabBar::tab:selected {{ color: {c['TEXT']}; border-bottom: 2px solid {c['ACCENT']}; }}
QTabBar::tab:hover {{ color: {c['TEXT']}; }}

QGroupBox {{
    border: 1px solid {c['BORDER']}; border-radius: 8px; margin-top: 14px; padding-top: 10px; background: {c['PANEL']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {c['TEXT_DIM']}; background: transparent; }}

QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {c['DISABLED']}; }}

QProgressBar {{
    background: {c['PANEL_2']}; border: 1px solid {c['BORDER']}; border-radius: 5px; text-align: center; height: 15px; color: {c['TEXT']};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['ACCENT_DIM']}, stop:1 {c['ACCENT']});
    border-radius: 4px;
}}

QSlider::groove:horizontal {{ height: 4px; background: {c['BORDER']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['ACCENT']}; width: 13px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {c['ACCENT_BRIGHT']}; }}
QSlider::sub-page:horizontal {{ background: {c['ACCENT_DIM']}; border-radius: 2px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['SCROLL']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['ACCENT_DIM']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['SCROLL']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['ACCENT_DIM']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {c['BG']}; }}
QSplitter::handle:hover {{ background: {c['ACCENT_DIM']}; }}

QMenu {{ background: {c['PANEL_2']}; border: 1px solid {c['BORDER']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['ACCENT_DIM']}; color: white; }}
QMenu::item:disabled {{ color: {c['DISABLED']}; }}
QMenu::separator {{ height: 1px; background: {c['BORDER']}; margin: 4px 8px; }}

QStatusBar {{ background: {c['PANEL']}; border-top: 1px solid {c['BORDER']}; color: {c['TEXT_DIM']}; }}
QStatusBar::item {{ border: none; }}
QScrollArea {{ border: none; }}
QDialog {{ background: {c['BG']}; }}
"""


STATE_COLORS: dict[str, str] = {}
QSS = ""

set_mode("dark")


def apply_theme(app: QtWidgets.QApplication, mode: str | None = None) -> None:
    if mode in ("dark", "light") and mode != MODE:
        set_mode(mode)
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.ColorRole.Window, BG),
        (QtGui.QPalette.ColorRole.WindowText, TEXT),
        (QtGui.QPalette.ColorRole.Base, PANEL),
        (QtGui.QPalette.ColorRole.AlternateBase, PANEL_2),
        (QtGui.QPalette.ColorRole.Text, TEXT),
        (QtGui.QPalette.ColorRole.Button, PANEL_2),
        (QtGui.QPalette.ColorRole.ButtonText, TEXT),
        (QtGui.QPalette.ColorRole.Highlight, ACCENT),
        (QtGui.QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ColorRole.ToolTipBase, PANEL_2),
        (QtGui.QPalette.ColorRole.ToolTipText, TEXT),
        (QtGui.QPalette.ColorRole.PlaceholderText, TEXT_DIM),
    ):
        palette.setColor(role, QtGui.QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(QSS)
