"""Hộp thoại chọn giọng kèm nghe thử.

Dùng cho provider Blaze: danh sách giọng lấy từ API, lọc theo tên/mã/tỉnh,
nghe thử trước khi chốt. Mẫu nghe thử được cache nên bấm lại không tốn quota.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6 import QtCore, QtMultimedia, QtWidgets

from ..jobs import run_background
from ..theme import TEXT_DIM
from .common import hint, push_button

Voices = list[tuple[str, str]]


def _split_label(label: str, voice_id: str) -> tuple[str, str]:
    """“BTV Hương Giang — HN-Nu-1-TM · Nu · HN” → (“BTV Hương Giang”, “Nu · HN”).

    Nhãn do provider dựng ra: tên — mã · chi tiết. Bảng đã có cột mã riêng nên
    phần chi tiết cắt từ dấu ``·`` trở đi, không lặp lại mã.
    """
    name, _, rest = label.partition("—")
    detail = rest.partition("·")[2] if rest else ""
    return name.strip() or voice_id, detail.strip()


class VoicePickerDialog(QtWidgets.QDialog):
    """Chọn giọng: lọc, nghe thử, rồi chốt. ``picked`` là mã giọng đã chọn."""

    def __init__(self, profile, voices: Voices, current: str = "", *, preview_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.voices = voices
        self.picked = current
        self._playing = False
        self.setWindowTitle("Chọn giọng nói")
        self.resize(780, 580)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Tìm theo tên, mã giọng, tỉnh…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Tên giọng", "Mã", "Chi tiết"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.preview_text = QtWidgets.QLineEdit(preview_text)
        self.preview_text.setPlaceholderText("Câu muốn đọc thử (để trống dùng câu mặc định)")
        layout.addWidget(self.preview_text)

        row = QtWidgets.QHBoxLayout()
        self.play = push_button("Nghe thử", self.play_preview, "play")
        self.stop = push_button("Dừng", self.stop_preview, "stop")
        self.stop.setEnabled(False)
        row.addWidget(self.play)
        row.addWidget(self.stop)
        row.addStretch()
        row.addWidget(push_button("Chọn giọng này", self.accept_choice, "check", "Primary"))
        row.addWidget(push_button("Huỷ", self.reject))
        layout.addLayout(row)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet(f"color: {TEXT_DIM};")
        layout.addWidget(self.status)
        layout.addWidget(hint("Nhấp đúp vào một dòng để nghe thử giọng đó. Mẫu đã tải sẽ được cache."))

        self.player = QtMultimedia.QMediaPlayer(self)
        self.audio = QtMultimedia.QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(lambda _e, text="": self._say(f"✕ không phát được: {text}"))

        self.search.textChanged.connect(lambda _t: self._fill())
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.itemDoubleClicked.connect(lambda _item: self.play_preview())
        self._fill()
        if current:
            self._select(current)
        elif self.table.rowCount():
            self.table.selectRow(0)

    # ------------------------------------------------------------ danh sách

    def _fill(self) -> None:
        needle = self.search.text().strip().lower()
        rows = [(v, l) for v, l in self.voices if not needle or needle in f"{v} {l}".lower()]
        self.table.setRowCount(len(rows))
        for row, (voice_id, label) in enumerate(rows):
            name, detail = _split_label(label, voice_id)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            id_item = QtWidgets.QTableWidgetItem(voice_id)
            id_item.setData(QtCore.Qt.ItemDataRole.UserRole, voice_id)
            self.table.setItem(row, 1, id_item)
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(detail))
        self._say(f"{len(rows)}/{len(self.voices)} giọng")
        if current_id := self.current_voice():
            self._select(current_id)

    def _select(self, voice_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) == voice_id:
                self.table.selectRow(row)
                return

    def _on_select(self) -> None:
        self.play.setEnabled(bool(self.current_voice()))

    def current_voice(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 1)
        return str(item.data(QtCore.Qt.ItemDataRole.UserRole)) if item else ""

    def selected_label(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.text() if item else ""

    # ------------------------------------------------------------ phát thử

    def _say(self, text: str) -> None:
        self.status.setText(text)

    def stop_preview(self) -> None:
        self.player.stop()
        self.stop.setEnabled(False)

    def _on_status(self, status) -> None:
        if status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop.setEnabled(False)

    def play_preview(self) -> None:
        voice_id = self.current_voice()
        if not voice_id:
            self._say("Chọn một giọng trước")
            return
        self.stop_preview()
        self.play.setEnabled(False)
        self._say(f"Đang tải mẫu nghe thử của {self.selected_label() or voice_id}…")
        voice, text = voice_id, self.preview_text.text().strip()

        def work(_progress, _stop_event):
            from reviewtrans.core.models import clone
            from ...core.providers.tts import create_tts

            return create_tts(clone(self.profile)).preview(voice, text)

        def done(path: Path) -> None:
            self.play.setEnabled(True)
            self.stop.setEnabled(True)
            self._say(f"▶ {Path(path).name}")
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(path)))
            self.player.play()

        def fail(message: str) -> None:
            self.play.setEnabled(True)
            self._say(f"✕ {message[:160]}")
            QtWidgets.QMessageBox.critical(self, "Không nghe thử được", message[:400])

        run_background(work, done, fail)

    def accept_choice(self) -> None:
        voice_id = self.current_voice()
        if not voice_id:
            QtWidgets.QMessageBox.information(self, "Chọn giọng", "Chọn một giọng trong danh sách.")
            return
        self.picked = voice_id
        self.stop_preview()
        self.accept()


def open_voice_picker(
    parent: QtWidgets.QWidget,
    profile,
    current: str = "",
    *,
    preview_text: str = "",
    on_done: Callable[[str], None] | None = None,
    on_loaded: Callable[[Voices], None] | None = None,
) -> None:
    """Tải danh sách giọng ở nền rồi mở hộp thoại. Kết quả đưa về qua ``on_done``."""
    from reviewtrans.core.models import clone

    snapshot = clone(profile)
    if not snapshot.api_keys:
        QtWidgets.QMessageBox.information(
            parent, "Chọn giọng", "Profile chưa có token — dán token vào ô API key trước."
        )
        return

    def work(_progress, _stop_event) -> Voices:
        from ...core.providers.tts import create_tts

        return create_tts(snapshot).list_voices("")

    def done(voices: Voices) -> None:
        if on_loaded:
            on_loaded(voices)
        if not voices:
            QtWidgets.QMessageBox.information(parent, "Chọn giọng", "Server không trả danh sách giọng.")
            return
        dialog = VoicePickerDialog(snapshot, voices, current, preview_text=preview_text, parent=parent)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and on_done:
            on_done(dialog.picked)

    def fail(message: str) -> None:
        QtWidgets.QMessageBox.critical(parent, "Không lấy được danh sách giọng", message[:400])

    run_background(work, done, fail)