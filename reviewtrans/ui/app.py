from __future__ import annotations

import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, APP_VERSION
from ..core.config import SettingsStore
from ..core.paths import fonts_dir, register_dll_dirs, resource_path

# bảng màu phải nạp trước khi import các module UI (chúng đọc hằng số màu lúc import)
store = SettingsStore()
from . import theme  # noqa: E402

theme.set_mode(store.settings.theme if store.settings.theme in ("dark", "light") else "dark")

from .main_window import MainWindow  # noqa: E402
from .state import AppState  # noqa: E402


def load_user_fonts() -> None:
    folder = fonts_dir()
    for path in folder.iterdir():
        if path.suffix.lower() in (".ttf", ".otf", ".ttc"):
            QtGui.QFontDatabase.addApplicationFont(str(path))


def main() -> int:
    register_dll_dirs()
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ThanhDub")
    icon_path = resource_path("icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    load_user_fonts()
    theme.apply_theme(app)
    state = AppState(store)
    window = MainWindow(state)
    window.show()
    return app.exec()
