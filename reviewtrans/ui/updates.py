"""Hộp thoại báo bản mới — dùng core.updates để hỏi GitHub."""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ..core import updates
from .jobs import run_background
from .widgets.common import open_path

_last_check: dict = {"running": False}


def check_for_updates(parent: QtWidgets.QWidget, current_version: str, settings_store=None, silent: bool = True) -> None:
    """Hỏi bản mới ở nền. silent=True: chỉ báo khi có bản mới và chưa bị bỏ qua."""
    if _last_check["running"]:
        return

    def work(_progress, _stop):
        return updates.fetch_latest()

    def done(release) -> None:
        _last_check["running"] = False
        if release is None:
            if not silent:
                QtWidgets.QMessageBox.information(
                    parent, "Kiểm tra bản mới", "Không kiểm tra được (mạng?) hoặc chưa có bản phát hành nào."
                )
            return
        if not updates.is_newer(release["version"], current_version):
            if not silent:
                QtWidgets.QMessageBox.information(
                    parent, "Kiểm tra bản mới", f"Bạn đang dùng bản mới nhất ({current_version})."
                )
            return
        if settings_store is not None:
            settings = settings_store.settings
            if settings.skipped_version == release["version"]:
                return  # người dùng đã chọn bỏ qua bản này
        _show_dialog(parent, current_version, release, settings_store)

    def fail(_message: str) -> None:
        _last_check["running"] = False

    _last_check["running"] = True
    run_background(work, done, fail)


def _show_dialog(parent: QtWidgets.QWidget, current_version: str, release: dict, settings_store) -> None:
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("Có bản mới của ThanhDub")
    box.setIcon(QtWidgets.QMessageBox.Icon.Information)
    notes = release["notes"].strip()
    if len(notes) > 700:
        notes = notes[:700] + "…"
    box.setText(
        f"<b>Phiên bản {release['version']}</b> đã ra mắt (bạn đang dùng {current_version})."
        + (f"<br><br><span style='font-size:12px'>{QtWidgets.QPlainTextEdit(notes).toPlainText()}</span>" if notes else "")
    )
    download = box.addButton("Tải bản mới", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    later = box.addButton("Để sau", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    if settings_store is not None:
        skip = box.addButton("Bỏ qua bản này", QtWidgets.QMessageBox.ButtonRole.ResetRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is download:
        open_path(release["url"])
    elif settings_store is not None and clicked is skip:
        settings_store.settings.skipped_version = release["version"]
        settings_store.save()


def schedule_startup_check(window: QtWidgets.QMainWindow, current_version: str, settings_store) -> None:
    """Kiểm tra bản mới một nhịp sau khi mở app (nếu được bật trong Cài đặt)."""
    if not settings_store.settings.check_updates:
        return

    def run() -> None:
        check_for_updates(window, current_version, settings_store, silent=True)

    QtCore.QTimer.singleShot(2500, run)
