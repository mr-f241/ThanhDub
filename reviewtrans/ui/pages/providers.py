from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtMultimedia, QtWidgets

from ...core.config import TRANSLATE_KINDS, TTS_KINDS, ProviderProfile
from ...core.models import clone, new_id
from ...core.providers.translate import (
    SUGGESTED_MODELS,
    ZEN_BASE_URL,
    ZEN_FREE_MODELS,
    create_translator,
)
from ...core.providers.tts import create_tts
from ..jobs import run_background
from ..state import AppState
from ..widgets.common import ComboBox, confirm, form_layout, hint, push_button, title_label
from ..widgets.provider_field import PoolStrip
from ..widgets.voice_picker import open_voice_picker

TTS_MODELS = {
    "blaze": ["v2.0_pro", "v2.0_flash", "v1.5_pro", "v1.5_flash"],
}

# gợi ý theo loại: (placeholder base_url, placeholder model, các option thêm)
KIND_HINTS = {
    "google": ("https://translation.googleapis.com", "",
               "Không cần key: dùng endpoint web miễn phí (bị giới hạn tần suất → 429 nếu bắn liên tục). "
               "Có key Google Cloud: dùng Cloud Translation v2 (ổn định hơn). "
               "Base URL để trống = dùng thẳng Google; điền thì đi qua proxy/mirror của bạn."),
    "microsoft": ("https://api.cognitive.microsofttranslator.com", "", "Không cần key: dùng token miễn phí của Edge. Có key Azure: điền key + region."),
    "riva": ("(để trống = grpc.nvcf.nvidia.com:443)", "",
             "Dịch máy chuyên dụng NVIDIA (riva-translate-1.6b, 36 ngôn ngữ) qua cloud NIM. "
             "API key = NVIDIA_API_KEY (nvapi-…, lấy tại build.nvidia.com). Không bị 429 theo IP như Google free."),
    "deep": ("(trống = dùng URL của engine; LibreTranslate thì điền mirror riêng)", "",
             "Bọc thư viện deep-translator — chọn engine ở ô Tuỳ chọn bên dưới: "
             "google/mymemory không cần key; libre/deepl/microsoft/yandex cần key; "
             "papago gõ `client_id:secret`, baidu gõ `appid:appkey`."),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini",
               "Mọi API tương thích OpenAI Chat Completions: OpenAI, DeepSeek, OpenRouter, Groq, LM Studio, Ollama (…/v1)."),
    "zen": (ZEN_BASE_URL, "big-pickle",
            "OpenCode Zen — gateway model free của OpenCode: Big Pickle, MiMo V2.6 Flash, Space Bunny… "
            "Giá $0/1M token nhưng vẫn cần key lấy tại opencode.ai/auth. Giai đoạn free thì prompt "
            "có thể được dùng để cải thiện model."),
    "opencode": ("(trống = tự lấy từ service.json của OpenCode đang mở)", "big-pickle",
                 "OpenCode (máy cục bộ) — dịch qua chính app OpenCode đang mở trên máy, **không cần key**, "
                 "model free ($0). App OpenCode phải đang mở trước khi dịch; hệ thống tự đọc service.json "
                 "để lấy địa chỉ server. Chưa mở app → báo lỗi rõ ràng thay vì im lặng."),
    "gemini": ("(để trống = Google AI Studio)", "gemini-2.5-flash", "API kiểu Gemini (google-genai). Có thể điền base URL của proxy tương thích."),
    "anthropic": ("(để trống = api.anthropic.com)", "claude-opus-5", "Claude qua SDK chính thức. Base URL để trống sẽ bật fallback phía server khi model từ chối."),
    "blaze": ("https://api.blaze.vn", "v2.0_pro",
              "Token Blaze: mỗi dòng một token, app tự xoay vòng — token hết quota sẽ nghỉ, "
              "token trả 401 bị loại. Mỗi token có 100 request/10 phút · 600 request/giờ. "
              "Bấm “Chọn giọng…” để nghe thử trước khi dùng."),
    "edge": ("", "", "Miễn phí, không cần key. Giọng ví dụ: vi-VN-HoaiMyNeural, vi-VN-NamMinhNeural."),
    "vbee": ("https://vbee.vn/api/v1", "", "Cần App ID + token (điền token vào ô API key)."),
    "openai_speech": ("https://api.openai.com/v1", "trống = tự lấy từ {base}/models",
                      "Mọi server có POST {base}/audio/speech kiểu OpenAI. Giọng/model lấy từ GET {base}/voices và /models nếu server có."),
    "legacy_custom": ("https://…", "", "API cũ: POST form {text, language} → {code:0, data:url}."),
}

def zen_free_profile(model: str) -> ProviderProfile:
    """Profile kind `zen` — trỏ thẳng vào OpenCode Zen, người dùng chỉ còn dán key vào."""
    name = ZEN_FREE_MODELS.get(model, model).split(" — ")[0]
    return ProviderProfile(
        name=f"Zen {name} (free)",
        kind="zen",
        base_url=ZEN_BASE_URL,
        model=model,
        options={"temperature": "0.3"},
    )


OPTION_FIELDS = {
    "blaze": [("voice", "Giọng mặc định", "HN-Nam-1-BL"), ("voices", "Thêm giọng (phẩy)", ""),
              ("language", "Ngôn ngữ", "vi"),
              ("quality", "Chất lượng (1-100)", "64"), ("format", "Định dạng", "mp3"),
              ("normalization", "Chuẩn hoá", "basic"), ("boost", "Nâng âm lượng (on/off)", "on"),
              ("timeout", "Chờ tối đa (giây)", "120"), ("concurrency", "Số luồng", "4")],
    "riva": [("function_id", "Function ID", "trống = riva-translate-1.6b chuẩn")],
    "deep": [("engine", "Engine", "google (google/mymemory/libre/deepl/microsoft/yandex/papago/baidu)"),
             ("email", "Email (MyMemory, ưu tiên quota)", "")],
    "openai": [("temperature", "Temperature", "0.3")],
    "zen": [("temperature", "Temperature", "0.3")],
    "gemini": [("temperature", "Temperature", "0.3")],
    "anthropic": [("temperature", "Temperature", "0.3"), ("fallbacks", "Fallback khi từ chối (on/off)", "on")],
    "edge": [("voice", "Giọng mặc định", "vi-VN-HoaiMyNeural"), ("pitch_hz", "Cao độ (Hz)", "0"), ("concurrency", "Số luồng", "4")],
    "vbee": [("app_id", "App ID", ""), ("voice", "Voice code", ""), ("max_polls", "Số lần chờ tối đa", "30"),
             ("callback_url", "Callback URL", "https://example.com/callback"), ("concurrency", "Số luồng", "2")],
    "openai_speech": [("voice", "Giọng mặc định", "trống = giọng đầu tiên của server"), ("voices", "Thêm giọng (phẩy)", ""),
                      ("format", "Định dạng", "wav"), ("instructions", "Chỉ dẫn giọng đọc", ""), ("concurrency", "Số luồng", "3")],
    "legacy_custom": [("language", "Language", "vi")],
}


class ProfileEditor(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()

    def __init__(self, kinds: dict[str, str], parent=None):
        super().__init__(parent)
        self.kinds = kinds
        self.profile: ProviderProfile | None = None
        self._loading = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        box = QtWidgets.QGroupBox("Cấu hình")
        self.form = form_layout()
        box.setLayout(self.form)
        self.name = QtWidgets.QLineEdit()
        self.kind = ComboBox(list(kinds.items()))
        self.base_url = QtWidgets.QLineEdit()
        self.keys = QtWidgets.QPlainTextEdit()
        self.keys.setPlaceholderText("Mỗi dòng một key — sẽ tự xoay vòng khi gặp giới hạn")
        self.keys.setMaximumHeight(80)
        self.model = ComboBox()
        self.model.setEditable(True)
        self.region = QtWidgets.QLineEdit()
        self.form.addRow("Tên", self.name)
        self.form.addRow("Loại", self.kind)
        self.form.addRow("Base URL", self.base_url)
        self.form.addRow("API key", self.keys)
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.fetch_models = QtWidgets.QPushButton("Tải danh sách model")
        self.fetch_models.clicked.connect(self._fetch_models)
        model_row.addWidget(self.fetch_models)
        self.form.addRow("Model", model_row)
        self.form.addRow("Region", self.region)
        layout.addWidget(box)
        self.options_box = QtWidgets.QGroupBox("Tuỳ chọn")
        self.options_form = form_layout()
        self.options_box.setLayout(self.options_form)
        layout.addWidget(self.options_box)
        self.hint = hint("")
        layout.addWidget(self.hint)
        layout.addStretch()
        self.option_edits: dict[str, QtWidgets.QLineEdit] = {}
        self.name.editingFinished.connect(self._commit)
        self.base_url.editingFinished.connect(self._commit)
        self.base_url.editingFinished.connect(self._base_url_changed)
        self.keys.textChanged.connect(self._commit)
        self.model.currentTextChanged.connect(self._commit)
        self.region.editingFinished.connect(self._commit)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.setEnabled(False)

    def set_profile(self, profile: ProviderProfile | None) -> None:
        self.profile = profile
        self.setEnabled(profile is not None)
        if profile is None:
            return
        self._loading = True
        self.name.setText(profile.name)
        self.kind.set_value(profile.kind)
        self.base_url.setText(profile.base_url)
        self.keys.setPlainText("\n".join(profile.api_keys))
        self._fill_models(profile.kind)
        self.model.setEditText(profile.model)
        self.region.setText(profile.region)
        self._build_options(profile)
        self._loading = False

    def _fill_models(self, kind: str) -> None:
        self.model.blockSignals(True)
        self.model.clear()
        if kind == "zen" or (kind == "openai" and self.profile is not None and self._is_zen(self.profile.base_url)):
            self.model.addItems(list(ZEN_FREE_MODELS))  # kind Zen (hoặc dán base Zen) = dropdown model free
        else:
            self.model.addItems(SUGGESTED_MODELS.get(kind, []) or TTS_MODELS.get(kind, []))
        self.model.blockSignals(False)

    @staticmethod
    def _is_zen(base_url: str) -> bool:
        return "opencode.ai/zen" in (base_url or "")

    def _base_url_changed(self, *_args) -> None:
        """Gõ xong base URL của Zen → nạp ngay danh sách model free vào ô Model."""
        if self._loading or not self.profile:
            return
        if not self._is_zen(self.profile.base_url):
            return
        current = self.model.currentText()
        self._fill_models(self.profile.kind)
        self.model.setEditText(current or self.profile.model)

    def _build_options(self, profile: ProviderProfile) -> None:
        while self.options_form.rowCount():
            self.options_form.removeRow(0)
        self.option_edits.clear()
        fields = OPTION_FIELDS.get(profile.kind, [])
        self.options_box.setVisible(bool(fields))
        for key, label, default in fields:
            edit = QtWidgets.QLineEdit(str(profile.options.get(key, "")))
            edit.setPlaceholderText(default)
            edit.editingFinished.connect(self._commit)
            self.option_edits[key] = edit
            if key == "voice" and profile.kind in ("edge", "openai_speech", "blaze"):
                row = QtWidgets.QWidget()
                row_layout = QtWidgets.QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(edit, 1)
                pick = QtWidgets.QPushButton("Chọn giọng…")
                pick.clicked.connect(lambda _=False, b=pick: self._pick_voice(b))
                row_layout.addWidget(pick)
                self.options_form.addRow(label, row)
            else:
                self.options_form.addRow(label, edit)
        url_hint, model_hint, text = KIND_HINTS.get(profile.kind, ("", "", ""))
        self.base_url.setPlaceholderText(url_hint)
        self.keys.setPlaceholderText(
            {
                "riva": "Dán NVIDIA_API_KEY (nvapi-…) — lấy tại build.nvidia.com, mỗi dòng một key",
                "blaze": "Mỗi dòng một token Blaze — tự xoay vòng, token hết quota sẽ tự nghỉ",
                "deep": "google/mymemory để trống; libre/deepl/microsoft/yandex: mỗi dòng một key; "
                         "papago: client_id:secret; baidu: appid:appkey",
                "openai_zen": "Key Zen: lấy tại opencode.ai/auth — model free giá $0/1M token",
                "opencode": "Không cần key — tự lấy mật khẩu local từ service.json của OpenCode đang mở",
            }.get(
                "openai_zen"
                if profile.kind == "zen" or (profile.kind == "openai" and self._is_zen(profile.base_url))
                else profile.kind,
                "Mỗi dòng một key — sẽ tự xoay vòng khi gặp giới hạn",
            )
        )
        self.model.lineEdit().setPlaceholderText(model_hint)
        self.region.setEnabled(profile.kind == "microsoft")
        self.model.setEnabled(profile.kind not in ("google", "microsoft", "riva", "deep", "edge", "vbee", "legacy_custom"))
        self.fetch_models.setVisible(profile.kind in ("openai", "zen", "opencode", "gemini", "anthropic", "openai_speech"))
        self.hint.setText(text)

    def _fetch_models(self) -> None:
        if not self.profile:
            return
        self._commit()
        snapshot = clone(self.profile)
        self.fetch_models.setEnabled(False)
        self.fetch_models.setText("Đang tải…")

        def done(models: list[str]) -> None:
            self.fetch_models.setEnabled(True)
            self.fetch_models.setText("Tải danh sách model")
            current = self.model.currentText()
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItems(models)
            self.model.setEditText(current)
            self.model.blockSignals(False)
            self.model.showPopup()

        def fail(message: str) -> None:
            self.fetch_models.setEnabled(True)
            self.fetch_models.setText("Tải danh sách model")
            QtWidgets.QMessageBox.critical(self, "Không lấy được danh sách model", message[:500])

        if snapshot.kind == "openai_speech":
            run_background(lambda _p, _s: create_tts(snapshot).remote_models(), done, fail)
        else:
            run_background(lambda _p, _s: create_translator(snapshot).list_models(), done, fail)

    def _pick_voice(self, button: QtWidgets.QPushButton) -> None:
        if not self.profile:
            return
        self._commit()
        snapshot = clone(self.profile)
        if snapshot.kind == "blaze":
            button.setEnabled(False)
            button.setText("Chọn giọng…")

            def restore() -> None:
                button.setEnabled(True)

            def chosen(voice_id: str) -> None:
                self.option_edits["voice"].setText(voice_id)
                self._commit()
                restore()

            open_voice_picker(self, snapshot, self.option_edits["voice"].text().strip(),
                              on_done=chosen)
            restore()
            return
        button.setEnabled(False)
        button.setText("Đang tải…")

        def done(voices: list[tuple[str, str]]) -> None:
            button.setEnabled(True)
            button.setText("Chọn giọng…")
            if not voices:
                QtWidgets.QMessageBox.information(self, "Chọn giọng", "Server không trả danh sách giọng.")
                return
            labels = [label for _id, label in voices]
            current = self.option_edits["voice"].text().strip()
            index = next((i for i, (vid, _l) in enumerate(voices) if vid == current), 0)
            label, ok = QtWidgets.QInputDialog.getItem(self, "Chọn giọng", "Giọng:", labels, index, False)
            if ok:
                self.option_edits["voice"].setText(voices[labels.index(label)][0])
                self._commit()

        def fail(message: str) -> None:
            button.setEnabled(True)
            button.setText("Chọn giọng…")
            QtWidgets.QMessageBox.critical(self, "Không lấy được danh sách giọng", message[:500])

        run_background(lambda _p, _s: create_tts(snapshot).list_voices("vi"), done, fail)

    def _kind_changed(self, _index: int) -> None:
        if self._loading or not self.profile:
            return
        old_label = self.kinds.get(self.profile.kind, "")
        if self.profile.name in (f"{old_label} mới", old_label):
            self.profile.name = f"{self.kinds.get(self.kind.value(), '')} mới"
            self.name.setText(self.profile.name)
        self.profile.kind = self.kind.value()
        self._fill_models(self.profile.kind)
        self._build_options(self.profile)
        self._commit()

    def _commit(self, *_args) -> None:
        if self._loading or not self.profile:
            return
        p = self.profile
        p.name = self.name.text().strip() or p.name
        p.base_url = self.base_url.text().strip()
        p.api_keys = [k.strip() for k in self.keys.toPlainText().splitlines() if k.strip()]
        p.model = self.model.currentText().strip()
        p.region = self.region.text().strip()
        for key, edit in self.option_edits.items():
            value = edit.text().strip()
            if value:
                p.options[key] = value
            else:
                p.options.pop(key, None)
        self.changed.emit()


class ProfileSection(QtWidgets.QWidget):
    def __init__(self, state: AppState, kind: str, parent=None):
        super().__init__(parent)
        self.state = state
        self.kind = kind  # "translate" | "tts"
        self.kinds = TRANSLATE_KINDS if kind == "translate" else TTS_KINDS
        layout = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        left.addWidget(self.list, 1)
        buttons = QtWidgets.QGridLayout()
        buttons.addWidget(push_button("Thêm", self.add, "add"), 0, 0)
        buttons.addWidget(push_button("Nhân bản", self.duplicate, "copy"), 0, 1)
        buttons.addWidget(push_button("Xoá", self.delete, "delete"), 1, 0)
        buttons.addWidget(push_button("Đặt mặc định", self.make_default, "check"), 1, 1)
        free = push_button("Model free (Zen)", self.free_menu, "translate")
        free.setToolTip("Thêm nhanh model free của OpenCode Zen — $0/1M token, chỉ cần key Zen")
        buttons.addWidget(free, 2, 0, 1, 2)
        left.addLayout(buttons)
        layout.addLayout(left, 1)
        right = QtWidgets.QVBoxLayout()
        self.editor = ProfileEditor(self.kinds)
        self.pool_strip = PoolStrip()
        self.pool_strip.setVisible(False)
        right.addWidget(self.editor, 1)
        right.addWidget(self.pool_strip)
        test_row = QtWidgets.QHBoxLayout()
        self.test_button = push_button("Kiểm tra kết nối", self.test, "run", "Primary")
        test_row.addWidget(self.test_button)
        self.test_result = QtWidgets.QLabel()
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_result, 1)
        right.addLayout(test_row)
        layout.addLayout(right, 2)
        self.save_timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=self._save)
        self.editor.changed.connect(self._on_changed)
        self.player = QtMultimedia.QMediaPlayer(self)
        self.audio = QtMultimedia.QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.refresh()

    @property
    def profiles(self) -> list[ProviderProfile]:
        s = self.state.settings
        return s.translate_profiles if self.kind == "translate" else s.tts_profiles

    @property
    def default_id(self) -> str:
        s = self.state.settings
        return s.default_translate_profile if self.kind == "translate" else s.default_tts_profile

    def refresh(self, select: str | None = None) -> None:
        current = select or (self.editor.profile.id if self.editor.profile else None)
        self.list.blockSignals(True)
        self.list.clear()
        for profile in self.profiles:
            label = self._label(profile)
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, profile.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = [p.id for p in self.profiles]
        row = ids.index(current) if current in ids else (0 if ids else -1)
        self.list.setCurrentRow(row)
        self._on_row(row)

    def _label(self, profile: ProviderProfile) -> str:
        kind = self.kinds.get(profile.kind, profile.kind)
        label = profile.name if profile.name == kind else f"{profile.name}  ·  {kind}"
        return ("★ " if profile.id == self.default_id else "    ") + label

    def _on_row(self, row: int) -> None:
        profile = self.profiles[row] if 0 <= row < len(self.profiles) else None
        self.editor.set_profile(profile)
        self.test_result.clear()
        if profile is not None and profile.kind == "blaze" and profile.api_keys:
            self.pool_strip.attach(profile)
        else:
            self.pool_strip.detach()

    def _on_changed(self) -> None:
        item = self.list.currentItem()
        profile = self.editor.profile
        if item and profile:
            item.setText(self._label(profile))
        if profile is not None and profile.kind == "blaze":
            self.pool_strip.attach(profile)
        self.save_timer.start()

    def _save(self) -> None:
        self.state.save_settings()

    def add(self) -> None:
        kind = next(iter(self.kinds))
        if self.kind == "translate":
            kind = "openai"
        profile = ProviderProfile(name=f"{self.kinds[kind]} mới", kind=kind)
        self.profiles.append(profile)
        self.state.save_settings()
        self.refresh(profile.id)

    def free_menu(self) -> None:
        if self.kind != "translate":
            return
        menu = QtWidgets.QMenu(self)
        for model, label in ZEN_FREE_MODELS.items():
            action = menu.addAction(label)
            action.triggered.connect(lambda _=False, m=model: self.add_free(m))
        menu.exec(QtGui.QCursor.pos())

    def add_free(self, model: str) -> None:
        """Thêm profile model free của Zen và đặt làm mặc định — đúng thứ vừa bấm chọn."""
        profile = zen_free_profile(model)
        self.profiles.append(profile)
        if self.kind == "translate":
            self.state.settings.default_translate_profile = profile.id
        self.state.save_settings()
        self.refresh(profile.id)

    def duplicate(self) -> None:
        profile = self.editor.profile
        if not profile:
            return
        copy = clone(profile)
        copy.id = new_id()
        copy.name += " (bản sao)"
        self.profiles.append(copy)
        self.state.save_settings()
        self.refresh(copy.id)

    def delete(self) -> None:
        profile = self.editor.profile
        if not profile or not confirm(self, "Xoá provider", f"Xoá “{profile.name}”?"):
            return
        self.profiles.remove(profile)
        self.state.save_settings()
        self.refresh()

    def make_default(self) -> None:
        profile = self.editor.profile
        if not profile:
            return
        if self.kind == "translate":
            self.state.settings.default_translate_profile = profile.id
        else:
            self.state.settings.default_tts_profile = profile.id
        self.state.save_settings()
        self.refresh(profile.id)

    def test(self) -> None:
        profile = self.editor.profile
        if not profile:
            return
        self._save()
        snapshot = clone(profile)
        self.test_button.setEnabled(False)
        self.test_result.setText("Đang kiểm tra…")

        if self.kind == "translate":
            def work(_p, _s):
                return create_translator(snapshot).test()

            def done(result) -> None:
                self.test_button.setEnabled(True)
                self.test_result.setText(f"✓ Hoạt động: “你好，世界” → “{result}”")
        else:
            target = Path(tempfile.mkdtemp(prefix="tts_test_")) / "test"

            def work(_p, _s):
                return create_tts(snapshot).test(target)

            def done(result) -> None:
                self.test_button.setEnabled(True)
                note = ""
                if snapshot.kind == "blaze":
                    from ...core.providers.tts.blaze import BlazeKeyPool

                    pool = BlazeKeyPool(list(snapshot.api_keys))
                    info = pool.summary()
                    note = f" · token {info['ready']}/{info['total']} sẵn sàng"
                self.test_result.setText(f"✓ Hoạt động — đang phát thử ({Path(result).name}){note}")
                self.player.setSource(QtCore.QUrl.fromLocalFile(str(result)))
                self.player.play()
                if snapshot.kind == "blaze":
                    self.pool_strip.refresh()

        def fail(message: str) -> None:
            self.test_button.setEnabled(True)
            self.test_result.setText(f"✕ {message[:400]}")

        run_background(work, done, fail)


class ProvidersPage(QtWidgets.QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(title_label(
            "Providers",
            "Cấu hình dịch và lồng tiếng dùng chung cho mọi project. Mỗi project chọn provider riêng trong “Cấu hình project”.",
        ))
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(ProfileSection(state, "translate"), "Dịch")
        tabs.addTab(ProfileSection(state, "tts"), "Lồng tiếng (TTS)")
        layout.addWidget(tabs, 1)
