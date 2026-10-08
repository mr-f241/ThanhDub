from __future__ import annotations

import pytest

from reviewtrans.core.config import ProviderProfile
from reviewtrans.core.providers import FatalProviderError
from reviewtrans.core.providers.translate import DEEP_ENGINES, TranslateJob, create_translator
from reviewtrans.core.providers.translate.deep import DeepTranslator, engine_lang

pytest.importorskip("deep_translator")


def test_registry_and_engine_list():
    translator = create_translator(ProviderProfile(name="d", kind="deep", options={"engine": "mymemory"}))
    assert isinstance(translator, DeepTranslator)
    assert translator.engine == "mymemory"
    assert set(DEEP_ENGINES) == {
        "google", "mymemory", "libre", "deepl", "microsoft", "yandex", "papago", "baidu",
    }


def test_engine_lang_maps_internal_codes():
    assert engine_lang("google", "zh") == "zh-CN"
    assert engine_lang("mymemory", "vi") == "vi-VN"        # bảng của engine là mã vùng
    assert engine_lang("libre", "zh") == "zh"              # khớp theo tên "chinese"
    assert engine_lang("baidu", "vi") == "vie"             # mã riêng của Baidu
    assert engine_lang("papago", "zh") == "zh-CN"          # bảng {mã: tên} → giữ nguyên mã
    assert engine_lang("google", "auto") == "auto"


def test_engine_lang_rejects_unsupported_language():
    with pytest.raises(FatalProviderError, match="không hỗ trợ"):
        engine_lang("deepl", "vi")  # DeepL không có tiếng Việt
    with pytest.raises(FatalProviderError, match="không hỗ trợ"):
        engine_lang("libre", "th")


def test_bad_engine_and_missing_keys_fail_fast():
    with pytest.raises(FatalProviderError, match="Engine không hợp lệ"):
        DeepTranslator(ProviderProfile(name="d", kind="deep", options={"engine": "khong-ton-tai"}))

    for options, keys in (
        ({"engine": "libre"}, []),
        ({"engine": "deepl"}, []),
        ({"engine": "papago"}, ["thieu-dau-ngac"]),  # phải là client_id:secret
    ):
        translator = DeepTranslator(ProviderProfile(name="d", kind="deep", api_keys=keys, options=options))
        with pytest.raises(FatalProviderError):
            translator._build("en", "vi")


def test_translate_applies_glossary(monkeypatch):
    translator = DeepTranslator(ProviderProfile(name="d", kind="deep", options={"engine": "google"}))
    seen: list[str] = []

    class FakeEngine:
        def translate(self, text: str) -> str:
            seen.append(text)
            return f"VI:{text}"

    monkeypatch.setattr(translator, "_build", lambda source, target: FakeEngine())
    job = TranslateJob(
        source_lang="zh",
        target_lang="vi",
        glossary=[("林凡", "Lâm Phàm"), ("灵石", "linh thạch")],
    )
    out = translator.translate(["林凡拿灵石", "然后离开"], job)
    assert out == ["VI:Lâm Phàm拿linh thạch", "VI:然后离开"]
    assert seen == ["Lâm Phàm拿linh thạch", "然后离开"]  # glossary áp vào NGUỒN trước khi gọi engine


def test_free_engine_falls_back_on_429(monkeypatch):
    """Google free chặn IP → nhảy sang mymemory ngay, không đợi retry và không fail job."""
    from deep_translator.exceptions import TooManyRequests

    translator = DeepTranslator(ProviderProfile(name="d", kind="deep", options={"engine": "google"}))
    used: list[str] = []

    class Flaky:
        def translate(self, text: str) -> str:
            used.append(translator.engine)
            if translator.engine == "google":
                raise TooManyRequests("bị chặn")
            return "VI ok"

    monkeypatch.setattr(translator, "_build", lambda source, target: Flaky())
    logs: list[str] = []
    out = translator.translate(["你好"], TranslateJob(source_lang="zh", target_lang="vi", log=logs.append))
    assert out == ["VI ok"]
    assert used == ["google", "mymemory"]
    assert translator.engine == "mymemory"
    assert any("chuyển sang engine mymemory" in m for m in logs)
