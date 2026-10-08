from __future__ import annotations

import json
import time

import pytest

from reviewtrans.core.ass import ass_color, ass_time, build_ass
from reviewtrans.core.config import ProviderProfile, SettingsStore
from reviewtrans.core.context import (
    apply_glossary_to_source,
    build_update_prompt,
    context_block,
    context_dict,
    extract_json,
    glossary_pairs,
    merge_update,
    name_rule,
)
from reviewtrans.core.geometry import layer_rect, wrap_text
from reviewtrans.core.models import (
    Character,
    GlossaryEntry,
    Layer,
    Project,
    ProjectContext,
    Segment,
    SubtitleStyle,
    VideoDoc,
    from_dict,
    to_dict,
)
from reviewtrans.core.pipeline.mix import plan_clips
from reviewtrans.core.proc import atempo_chain
from reviewtrans.core.providers.translate.base import (
    LLMTranslator,
    TranslateJob,
    is_repetitive,
    is_too_long,
    needs_repair,
    parse_items,
)
from reviewtrans.core.srt import match_translation, parse_srt, render_srt
from reviewtrans.core.store import ProjectStore


def test_srt_roundtrip():
    text = "﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n你好\r\n世界\r\n\r\n00:00:03,000 --> 00:00:04,000\r\n再见\r\n"
    items = parse_srt(text)
    assert items == [(1.0, 2.5, "你好 世界"), (3.0, 4.0, "再见")]
    assert parse_srt(render_srt(items)) == items


def test_match_translation_by_time_then_by_order():
    targets = [(10.0, 12.0), (20.0, 22.0), (30.0, 32.0)]
    # file dịch giữ nguyên mốc giờ → ghép theo giờ
    kept = [(10.1, 11.9, "a"), (20.0, 21.0, "b"), (31.0, 33.0, "c")]
    assert match_translation(kept, targets) == [0, 1, 2]
    # tool dịch dời giờ hết nhưng vẫn đủ số dòng → ghép theo thứ tự
    shifted = [(100.0, 102.0, "a"), (110.0, 112.0, "b"), (120.0, 122.0, "c")]
    assert match_translation(shifted, targets) == [0, 1, 2]
    # file gộp 2 câu thành 1 → cả hai câu nhận chung một dòng
    assert match_translation([(10.0, 22.0, "a b")], targets[:2]) == [0, 0]
    # file của video khác (khác số dòng, lệch giờ) → không ghép gì cả
    other = [(0.0, 1.0, "x"), (5.0, 6.0, "y")]
    assert match_translation(other, targets) == [None, None, None]


def test_model_roundtrip_ignores_unknown_keys():
    doc = VideoDoc(name="a", layers=[Layer(type="text", text="hi")])
    data = to_dict(doc)
    data["unknown"] = 1
    data["style"]["bogus"] = 2
    again = from_dict(VideoDoc, json.loads(json.dumps(data)))
    assert again.layers[0].text == "hi"
    assert again.style == doc.style


def test_mark_stale_from():
    doc = VideoDoc(stages={"asr": "done", "translate": "done", "tts": "done", "render": "done"})
    doc.mark_stale_from("tts")
    assert doc.stages == {"asr": "done", "translate": "done", "tts": "stale", "render": "stale"}


def test_ass_color_and_time():
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_color("#000000", 0.0) == "&HFF000000"
    assert ass_time(3661.239) == "1:01:01.24"


def test_build_ass_box_layers():
    style = SubtitleStyle(bg_enabled=True)
    ass = build_ass([Segment(start=0, end=1, text="Xin {chào}")], style, 1280, 720)
    assert "Style: Box" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Box,,0,0,0,,Xin (chào)" in ass
    assert "PlayResY: 720" in ass


def test_wrap_text():
    assert wrap_text("một hai ba bốn năm sáu", 10).count("\n") >= 1
    assert wrap_text("一二三四五六七八", 4) == "一二三四\n五六七八"
    assert wrap_text("ngắn", 0) == "ngắn"


def test_layer_rect_even_and_clamped():
    layer = Layer(x=0.9, y=0.9, w=0.5, h=0.5)
    x, y, w, h = layer_rect(layer, 1920, 1080)
    assert x + w <= 1920 and y + h <= 1080 and w % 2 == 0 and h % 2 == 0
    image = Layer(type="image", x=0, y=0, w=0.1, keep_aspect=True)
    assert layer_rect(image, 1000, 1000, (200, 100))[3] == 50


def test_atempo_chain():
    assert atempo_chain(3.0).count("atempo") == 2
    assert atempo_chain(1.2) == "atempo=1.20000"


def test_parse_items_variants():
    assert parse_items('```json\n[{"id":1,"text":"a"},{"id":2,"text":"b"}]\n```', 2) == ["a", "b"]
    assert parse_items('{"items":[{"id":2,"t":"b"},{"id":1,"t":"a"}]}', 2) == ["a", "b"]
    assert parse_items("a\nb", 2) == ["a", "b"]
    assert parse_items('[{"id":1,"text":"a"}]', 2) is None


def test_parse_items_numbered_output():
    """Model nhỏ sinh '1. …' ổn định hơn JSON → đọc được cả hai."""
    assert parse_items("1. a\n2. b", 2) == ["a", "b"]
    assert parse_items("```text\n1) a\n2) b\n```", 2) == ["a", "b"]
    assert parse_items("1. a\n2. b", 3) is None
    assert parse_items("1. a\n3. c", 3) is None  # thiếu id 2
    assert parse_items("2. b\n1. a", 2) == ["a", "b"]  # số bị đảo vẫn map đúng id


def test_parse_items_numbered_drops_leaked_counters():
    """Model sót số ở dòng đầu → vẫn phải bỏ hết số thứ tự, không được lọt vào bản dịch."""
    raw = ". a\n2. b\n3. c"
    assert parse_items(raw, 3) == ["a", "b", "c"]
    # số dòng không khớp số câu → bỏ qua để retry/chia nhỏ
    assert parse_items(". a\n2. b\n3. c", 2) is None
    assert parse_items("1. a\n2. b\n3. c", 2) == ["a", "b"]  # thừa id vẫn map theo id


def test_extract_json_sloppy_llm_output():
    """Model hay in chữ 'json' trước mảng hoặc quên ngoặc mảng."""
    assert extract_json('json\n[{"id":1,"text":"a"}]') == [{"id": 1, "text": "a"}]
    assert extract_json('```json\n{"id":1,"text":"a"}, {"id":2,"text":"b"}\n```') == [
        {"id": 1, "text": "a"},
        {"id": 2, "text": "b"},
    ]
    assert extract_json('JSON: {"a": 2}') == {"a": 2}


def test_context_merge_respects_locks():
    ctx = ProjectContext(
        characters=[Character(source="林凡", target="Lâm Phàm", locked=True)],
        glossary=[GlossaryEntry(source="宗门", target="tông môn")],
    )
    changes = merge_update(
        ctx,
        {
            "summary": "Tóm tắt mới",
            "characters": [
                {"source": "林凡", "target": "Lin Fan"},
                {"source": "苏雪", "target": "Tô Tuyết", "gender": "nữ", "addressing": "gọi Lâm Phàm là huynh"},
            ],
            "glossary": [{"source": "宗门", "target": "môn phái"}, {"source": "灵石", "target": "linh thạch"}],
        },
        "Tập 1",
    )
    assert ctx.characters[0].target == "Lâm Phàm"  # đã khoá
    assert any(c.source == "苏雪" and c.auto for c in ctx.characters)
    assert {g.source: g.target for g in ctx.glossary} == {"宗门": "môn phái", "灵石": "linh thạch"}
    assert ctx.summary == "Tóm tắt mới"
    assert changes and ctx.changelog[-1].video == "Tập 1"
    block = context_block(ctx, "Giữ giọng hài hước")
    assert "Lâm Phàm" in block and "linh thạch" in block and "hài hước" in block
    pairs = glossary_pairs(ctx)
    assert apply_glossary_to_source("林凡拿灵石", pairs) == "Lâm Phàm拿linh thạch"


def test_extract_json_with_prose():
    assert extract_json('Đây là kết quả:\n{"a": 1}\nHết.') == {"a": 1}


def test_context_dict_unwraps_single_element_list():
    """NIM đôi khi trả [{...}] thay vì {...} — vẫn phải đọc được làm ngữ cảnh."""
    assert context_dict(extract_json('[{"summary":"x"}]')) == {"summary": "x"}
    assert context_dict({"summary": "x"}) == {"summary": "x"}
    # mảng saishape thì giữ nguyên để update_context báo lỗi kèm dữ liệu
    assert context_dict([1, 2]) == [1, 2]
    assert context_dict([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_context_dict_mang_tran_nhan_vat():
    """Model tuột object rồi trả thẳng mảng nhân vật → vẫn phải gộp được, không sập pipeline."""
    chars = [
        {
            "source": "情怒远",
            "target": "Tình Nộ Viễn",
            "gender": "nam",
            "role": "Nhân vật chính",
            "addressing": "Xưng 'tôi', gọi kẻ thù là 'ngươi' hoặc 'mày'.",
        },
        {
            "source": "宋典史",
            "target": "Song Điển sử",
            "gender": "nam",
            "role": "Phản diện tạm thời",
            "addressing": "Xưng 'ta', gọi nhân vật chính là 'ngươi'.",
        },
    ]
    data = context_dict(chars)
    assert data == {"characters": chars}
    # gộp thẳng vào ngữ cảnh được (đây chính là ca lỗi ngoài đời)
    ctx = ProjectContext()
    changes = merge_update(ctx, data, "Tập 1")
    assert [c.source for c in ctx.characters] == ["情怒远", "宋典史"]
    assert changes

    # trộn: mục có note mà không có trường nhân vật là thuật ngữ
    mixed = [
        {"source": "Lạc Dương", "target": "Lạc Dương", "note": "địa danh"},
        {"source": "林凡", "target": "Lâm Phàm", "gender": "nữ"},
    ]
    assert context_dict(mixed) == {"characters": [mixed[1]], "glossary": [mixed[0]]}
    # mục lẻ tẻ cũng tự bọc đúng chỗ
    assert context_dict({"source": "灵石", "target": "linh thạch", "note": "tiền tệ"}) == {
        "glossary": [{"source": "灵石", "target": "linh thạch", "note": "tiền tệ"}]
    }
    # object đúng dạng bị bọc trong mảng vẫn bung ra
    assert context_dict([{"summary": "s", "characters": chars}]) == {
        "summary": "s",
        "characters": chars,
    }
    # không có gì mới -> coi như không thay đổi thay vì sập
    assert context_dict([]) == {}


def test_name_rule_tieng_viet_dung_am_han_viet():
    """Tên Trung phải đọc theo âm Hán Việt — không trộn pinyin kiểu 'Xiao Trường'."""
    rule = name_rule("vi")
    assert "Sino-Vietnamese" in rule
    assert "Tiêu Trường" in rule  # 萧长 đọc đúng
    assert "pinyin" in rule
    assert name_rule("en") == ""  # chỉ ép với mục tiêu tiếng Việt
    assert name_rule("") == ""
    # prompt dịch và prompt cập nhật ngữ cảnh đều phải gắn quy tắc này
    system, _ = _fake_translator([]).build_prompts(["x"], TranslateJob(target_lang="vi"))
    assert "Sino-Vietnamese" in system
    assert "Sino-Vietnamese" not in _fake_translator([]).build_prompts(["x"], TranslateJob(target_lang="en"))[0]
    prompt = build_update_prompt(ProjectContext(), [Segment(id=1, source="x", text="y")], "zh", "vi", "T1")
    assert "Sino-Vietnamese" in prompt
    assert "Sino-Vietnamese" not in build_update_prompt(
        ProjectContext(), [Segment(id=1, source="x", text="y")], "zh", "en", "T1"
    )


def _fake_translator(answers: list) -> "LLMTranslator":
    class Fake(LLMTranslator):
        def __init__(self) -> None:
            super().__init__(ProviderProfile(name="fake", kind="openai", model="fake-1"))
            self.answers = list(answers)
            self.prompts: list[str] = []

        def _call(self, system: str, prompt: str, key: str) -> str:
            self.prompts.append(prompt)
            return self.answers.pop(0)

    return Fake()


def test_phat_hien_ban_dich_chet_loop_va_dai_lo():
    src = "这一世我是大周小公主周冰仙消化完记忆孤地脸色一点老皇帝好像要将我许配给一个马夫不过我有绝世仙法"
    looped = "Ta là " + "tiểu công chúa tuần " * 20
    normal = "Ta là tiểu công chúa Đại Chu Băng Tiên, lão hoàng đế muốn gả ta cho mã phu."
    assert is_repetitive(looped)
    assert not is_repetitive(normal)
    assert is_too_long(src, "x" * (len(src) * 7))
    assert not is_too_long(src, "x" * (len(src) * 3))
    assert not is_too_long("ngắn", "x" * 500)  # gốc ngắn thì không tính là dài lố
    assert needs_repair(src, looped) and not needs_repair(src, normal)


def test_translate_tu_sua_cau_chet_loop():
    """Câu chết loop trong lô → tự hỏi lại 1 lượt, lấy bản gọn hơn; bản sạch thì không gọi thêm."""
    src = "这一世我是大周小公主周冰仙消化完记忆孤地脸色一点老皇帝好像要将我许配给一个马夫不过我有绝世仙法"
    looped = "Ta là " + "tiểu công chúa tuần " * 20
    fixed = "Ta là tiểu công chúa Đại Chu Băng Tiên, lão hoàng đế muốn gả ta cho mã phu, nhưng ta có tiên pháp vô song."
    job = TranslateJob(source_lang="zh", target_lang="vi")

    fake = _fake_translator([f"1. {looped}", f"1. {fixed}"])
    assert fake.translate([src], job) == [fixed]
    assert len(fake.prompts) == 2 and "Compress the wording" in fake.prompts[1]

    good = "Ta là tiểu công chúa Đại Chu Băng Tiên, được gả cho mã phu."
    clean = _fake_translator([f"1. {good}"])
    assert clean.translate([src], job) == [good]
    assert len(clean.prompts) == 1  # bản sạch -> không tốn thêm lượt gọi


def test_plan_clips_fit_and_gap():
    doc = VideoDoc(duration=20)
    segs = [
        Segment(id=1, start=0, end=1, tts_file="a.wav", tts_duration=3.0),
        Segment(id=2, start=2, end=3, tts_file="b.wav", tts_duration=0.5),
    ]
    clips = plan_clips(doc, segs)
    assert abs(clips[0].tempo - 1.5) < 1e-6  # khe = tới câu sau (2s)
    assert clips[1].tempo == 1.0
    doc.audio.use_gap = False
    doc.audio.max_speed = 2.0
    assert abs(plan_clips(doc, segs)[0].tempo - 2.0) < 1e-6


def test_store_and_settings(home, tmp_path):
    legacy = home / ".video_translation_studio"
    legacy.mkdir()
    (legacy / "config.json").write_text(json.dumps({"gemini_api_keys": ["k1", "k2"], "openai_api_key": "o"}))
    store = SettingsStore()
    kinds = [p.kind for p in store.settings.translate_profiles]
    assert "gemini" in kinds and "openai" in kinds
    assert store.settings.find_translate(store.settings.default_translate_profile).kind == "gemini"

    pstore, project = ProjectStore.create(tmp_path / "projects", "Phim A")
    doc = pstore.add_video(project, str(tmp_path / "ep1.mp4"))
    pstore.save_segments(doc.id, [Segment(id=1, start=0, end=1, source="x")])
    reopened = ProjectStore.open(pstore.root)
    assert reopened.load_project().videos[0].id == doc.id
    assert reopened.load_segments(doc.id)[0].source == "x"
    assert isinstance(reopened.load_project(), Project)


def test_extract_7z_with_bcj2(tmp_path):
    import subprocess

    from reviewtrans.core.pipeline.resources import _seven_zip_exe, extract_7z

    seven = _seven_zip_exe()
    if not seven:
        pytest.skip("cần 7-Zip để tạo file thử BCJ2")
    src = tmp_path / "src"
    src.mkdir()
    (src / "libmpv-2.dll").write_bytes(b"MZ" + bytes(range(256)) * 64)
    archive = tmp_path / "pkg.7z"
    subprocess.run(
        [str(seven), "a", "-y", str(archive), str(src / "libmpv-2.dll"),
         "-m0=BCJ2", "-m1=LZMA:d25", "-m2=LZMA:d19", "-m3=LZMA:d19", "-mb0:1", "-mb0s1:2", "-mb0s2:3"],
        check=True, capture_output=True,
    )
    out = tmp_path / "out"
    out.mkdir()
    extract_7z(archive, out)
    assert (out / "libmpv-2.dll").read_bytes() == (src / "libmpv-2.dll").read_bytes()


def test_openai_speech_uses_server_voices_and_models(tmp_path, monkeypatch):
    from reviewtrans.core.config import ProviderProfile
    from reviewtrans.core.providers import tts as tts_mod

    class Resp:
        def __init__(self, status, data=None, content=b"", ctype="application/json"):
            self.status_code, self._data, self.content = status, data, content
            self.headers = {"content-type": ctype}
            self.text = str(data)

        def json(self):
            return self._data

    calls = []

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/voices"):
            return Resp(200, {"object": "list", "data": [{"id": "Trúc Ly", "name": "Trúc Ly", "description": "Nữ"}]})
        if url.endswith("/models"):
            return Resp(200, {"data": [{"id": "vieneu-v3-turbo"}]})
        return Resp(404, {})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return Resp(200, content=b"RIFF....WAVE", ctype="audio/wav")

    monkeypatch.setattr(tts_mod.requests, "get", fake_get)
    monkeypatch.setattr(tts_mod.requests, "post", fake_post)
    tts_mod.OpenAISpeechTTS._remote_cache.clear()
    provider = tts_mod.create_tts(ProviderProfile(kind="openai_speech", base_url="https://tts.example/v1", options={"format": "pcm"}))
    assert provider.list_voices() == [("Trúc Ly", "Trúc Ly — Nữ")]
    out = provider.synthesize("xin chào", "", 1.0, tmp_path / "a")
    assert calls[0]["voice"] == "Trúc Ly" and calls[0]["model"] == "vieneu-v3-turbo"
    assert calls[0]["response_format"] == "wav" and out.suffix == ".wav"
    tts_mod.OpenAISpeechTTS._remote_cache.clear()


def test_edge_tts_rejects_text_voice_cannot_read(tmp_path):
    """Text toàn tiếng Trung + giọng vi → Edge trả audio rỗng (NoAudioReceived):
    phải báo ngay bằng lỗi rõ, không gửi request và không retry vô ích."""
    from reviewtrans.core.providers import ProviderError
    from reviewtrans.core.providers.tts import EdgeTTS

    provider = EdgeTTS(ProviderProfile(kind="edge", options={"voice": "vi-VN-NamMinhNeural"}))
    with pytest.raises(ProviderError, match="không phát âm được"):
        provider.synthesize("功法", "vi-VN-NamMinhNeural", 1.0, tmp_path / "seg")
    with pytest.raises(ProviderError, match="không phát âm được"):
        provider.synthesize("留云手", "", 1.0, tmp_path / "seg2")  # voice lấy từ profile
    assert not (tmp_path / "seg.mp3").exists()  # fail trước khi gửi request


# ------------------------------------------------------------------ Blaze TTS


class _Resp:
    """Response giả cho requests của provider Blaze."""

    def __init__(self, status, data=None, content=b"", ctype="application/json"):
        self.status_code, self._data, self.content = status, data, content
        self.headers = {"content-type": ctype}
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data

    def iter_content(self, chunk=8192):
        for i in range(0, len(self.content), chunk):
            yield self.content[i:i + chunk]


class _FakeBlaze:
    """Chặn mọi request của BlazeTTS, trả lời theo kịch bản cho trước."""

    def __init__(self, *, speakers=None, token_broken=()):
        self.calls = []          # (method, path, token, payload)
        self.speakers = speakers if speakers is not None else [
            {"id": "HN-Nu-1-TM", "name": "BTV Hương Giang", "language": "vi",
             "gender": "Nữ", "province": "Hà Nội", "type": "TM"},
            {"id": "US-Nam-1-BL", "name": "Male Announcer", "language": "en", "gender": "Male"},
        ]
        self.token_broken = set(token_broken)

    def request(self, method, url, json=None, headers=None, params=None, stream=False, timeout=None):
        token = str((headers or {}).get("Authorization", "")).removeprefix("Bearer ")
        self.calls.append((method, url, token, json))
        if token in self.token_broken:
            return _Resp(401, {"msg": "bad token"})
        path = url.split("?")[0]
        if path.endswith("/tts/options"):
            return _Resp(200, {"speakers": self.speakers})
        if path.endswith("/download"):
            return _Resp(200, content=b"ID3-fake-audio-bytes", ctype="audio/mpeg")
        if path.endswith("/info"):
            return _Resp(200, {"id": "job-1", "status": "completed"})
        if path.endswith("/v1/tts"):
            return _Resp(200, {"id": "job-1", "status": "processing"})
        return _Resp(404, {"msg": "nope"})


def _blaze(tmp_path, monkeypatch, keys=("tokA", "tokB"), **options):
    from reviewtrans.core.providers.tts import blaze as bmod

    pool_file = tmp_path / "pool.json"
    profile = ProviderProfile(kind="blaze", name="Blaze", api_keys=list(keys),
                              model="v2.0_pro", options=options)
    provider = bmod.BlazeTTS(profile, pool=bmod.BlazeKeyPool(list(keys), pool_file))
    return provider, pool_file


def test_blaze_pool_rotates_and_retries_next_token(tmp_path, home, monkeypatch):
    """401 trên token đầu → đánh dấu chết, thử token sau, vẫn ra file âm thanh."""
    provider, _ = _blaze(tmp_path, monkeypatch)
    fake = _FakeBlaze(token_broken={"tokA"})
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.requests.request", fake.request)
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.normalize", lambda p: p)
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.time.sleep", lambda _s: None)

    out = provider.synthesize("xin chào", "HN-Nu-1-TM", 1.0, tmp_path / "seg")
    assert out == tmp_path / "seg.mp3" and out.read_bytes().startswith(b"ID3")
    assert provider.pool.snapshot()[0]["dead"] is True  # tokA chết
    assert any(call[2] == "tokB" for call in fake.calls)
    payload = next(c[3] for c in fake.calls if c[1].endswith("/v1/tts"))
    assert payload["speaker_id"] == "HN-Nu-1-TM" and payload["query"] == "xin chào"
    assert payload["audio_format"] == "mp3" and payload["audio_quality"] == 64


def test_blaze_pool_cools_down_on_429_and_persists(tmp_path, home, monkeypatch):
    """429 → token nghỉ; bộ đếm ghi xuống JSON nên mở lại app vẫn nhớ."""
    provider, pool_file = _blaze(tmp_path, monkeypatch)
    fake = _FakeBlaze()
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.requests.request", fake.request)
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.normalize", lambda p: p)
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.time.sleep", lambda _s: None)

    state = provider.pool.acquire()
    provider.pool.report(state, ok=False, status=429)
    assert state.window_state(time.time()) == "cooling"
    assert json.loads(pool_file.read_text(encoding="utf-8"))["tokens"]

    # hết hạn mức cửa sổ 10 phút thì thành "full", không cấp token nào nữa
    from reviewtrans.core.providers import ProviderError

    now = time.time()
    for other in provider.pool._tokens.values():
        other.used_10m = [now] * 100
    assert provider.pool.summary()["full"] == 1  # token kia vẫn đang nghỉ do 429
    assert provider.pool.summary()["cooling"] == 1
    with pytest.raises(ProviderError):
        provider.pool.acquire()

    # xoá lịch sử → cả hai token sẵn sàng trở lại
    provider.pool.clear_history()
    assert provider.pool.summary()["ready"] == 2
    assert provider.pool.reset_dead() == 0


def test_blaze_voices_cached_once_and_filtered_by_language(tmp_path, home, monkeypatch):
    """Danh sách giọng gọi mạng đúng một lần, lọc theo field ``language`` của API."""
    provider, _ = _blaze(tmp_path, monkeypatch)
    fake = _FakeBlaze()
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.requests.request", fake.request)
    provider._save_voices([])  # xoá cache để chắc chắn phải gọi mạng

    vi = provider.list_voices("vi")
    assert [v for v, _ in vi] == ["HN-Nu-1-TM"]
    assert "BTV Hương Giang" in vi[0][1] and "Nữ · Hà Nội · TM" in vi[0][1]

    all_rows = provider.list_voices("")
    assert [v for v, _ in all_rows] == ["HN-Nu-1-TM", "US-Nam-1-BL"]
    assert provider.list_voices("ja") == all_rows  # không có giọng ja → trả đầy đủ
    assert len([c for c in fake.calls if c[1].endswith("/tts/options")]) == 1  # cache đọc từ đĩa


def test_blaze_preview_reuses_cache_for_label_and_voice_id(tmp_path, home, monkeypatch):
    """Nhãn đầy đủ và mã giọng phải trỏ cùng một file mẫu, bấm lại không tốn quota."""
    provider, _ = _blaze(tmp_path, monkeypatch)
    fake = _FakeBlaze()
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.requests.request", fake.request)
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.normalize", lambda p: p)
    monkeypatch.setattr("reviewtrans.core.providers.tts.blaze.time.sleep", lambda _s: None)

    by_id = provider.preview_path("HN-Nu-1-TM", "v2.0_pro", "thử giọng")
    by_label = provider.preview_path("BTV Hương Giang — HN-Nu-1-TM · Nữ", "v2.0_pro", "thử giọng")
    assert by_id == by_label and by_id.name.startswith("HN-Nu-1-TM__")

    first = provider.preview("HN-Nu-1-TM", "thử giọng")
    calls_after_first = len(fake.calls)
    second = provider.preview("BTV Hương Giang — HN-Nu-1-TM", "thử giọng")
    assert second == first and len(fake.calls) == calls_after_first  # không gọi lại


def test_blaze_registered_as_tts_kind(home):
    from reviewtrans.core.config import TTS_KINDS
    from reviewtrans.core.providers.tts import BlazeTTS, create_tts

    assert "blaze" in TTS_KINDS
    assert isinstance(create_tts(ProviderProfile(kind="blaze", api_keys=["t"])), BlazeTTS)


def test_resolve_priority_video_project_global():
    from reviewtrans.core.config import AppSettings, ProviderProfile
    from reviewtrans.core.resolve import resolve

    g = ProviderProfile(id="g", name="G", kind="google")
    o = ProviderProfile(id="o", name="O", kind="openai", model="m-profile")
    a = ProviderProfile(id="a", name="A", kind="anthropic")
    settings = AppSettings(translate_profiles=[g, o, a], default_translate_profile="o")
    project = Project()
    doc = VideoDoc()
    r = resolve(settings, "translate", project, doc)
    assert (r.profile.id, r.profile_source, r.value) == ("o", "global", "")
    project.translate_model = "m-project"  # model ở project áp cho provider mặc định
    r = resolve(settings, "translate", project, doc)
    assert (r.profile.id, r.value, r.value_source) == ("o", "m-project", "project")
    project.translate_profile = "g"
    assert resolve(settings, "translate", project, doc).profile.id == "g"
    doc.translate_profile = "a"  # video chọn provider khác → model của project không áp sang
    r = resolve(settings, "translate", project, doc)
    assert (r.profile.id, r.profile_source, r.value) == ("a", "video", "")
    doc.translate_model = "claude-x"
    assert resolve(settings, "translate", project, doc).value == "claude-x"
    doc.translate_profile = "missing"  # profile đã bị xoá → rơi về cấp trên
    assert resolve(settings, "translate", project, doc).profile.id == "g"


def test_google_endpoint_follows_base_url():
    from reviewtrans.core.config import ProviderProfile
    from reviewtrans.core.providers.translate.machine import GoogleTranslator

    def endpoint(profile, default, path):
        return GoogleTranslator(profile)._endpoint(default, path)

    plain = ProviderProfile(id="a", name="G", kind="google")
    assert endpoint(plain, "https://translation.googleapis.com", "/language/translate/v2") == \
        "https://translation.googleapis.com/language/translate/v2"
    # điền Base URL (host/mirror) → app tự ghép path endpoint vào sau
    proxy = ProviderProfile(id="b", name="G", kind="google", base_url="https://proxy.example/api/")
    assert endpoint(proxy, "https://translate.googleapis.com", "/translate_a/single") == \
        "https://proxy.example/api/translate_a/single"
    # Base URL đã trỏ thẳng tới endpoint → dùng nguyên, không lặp path
    direct = ProviderProfile(id="c", name="G", kind="google", base_url="https://proxy.example/translate_a/single")
    assert endpoint(direct, "https://translate.googleapis.com", "/translate_a/single") == \
        "https://proxy.example/translate_a/single"


def test_riva_code_mapping_and_variants():
    from reviewtrans.core.langs import riva_code

    assert riva_code("zh") == "zh-CN" and riva_code("zh-tw") == "zh-TW"
    assert riva_code("vi") == "vi" and riva_code("auto") == "" and riva_code("xx") == ""

    from reviewtrans.core.config import ProviderProfile
    from reviewtrans.core.providers.translate.base import TranslateJob
    from reviewtrans.core.providers.translate.riva import RivaTranslator, _RIVA_VARIANTS

    translator = RivaTranslator(ProviderProfile(name="N", kind="riva"))
    # es → thử es-ES trước, hết khả dĩ mới báo lỗi ngôn ngữ
    assert _RIVA_VARIANTS["es"] == ["es-ES", "es-US"]

    class FakeNmt:
        def __init__(self, results, missing_error=""):
            self.results, self.calls, self.missing_error = results, [], missing_error

        def translate(self, texts, model, source_language, target_language):
            self.calls.append((source_language, target_language, list(texts)))
            if (source_language, target_language) not in self.results:
                raise Exception(self.missing_error or "not found")
            return self.results[(source_language, target_language)]

    fake = FakeNmt({("es-ES", "vi"): type("R", (), {"translations": [type("T", (), {"text": "xin chào"})()]})()})
    translator._client = fake
    job = TranslateJob(source_lang="es", target_lang="vi")
    assert translator._translate_many(["hola"], job) == ["xin chào"]
    assert fake.calls == [("es-ES", "vi", ["hola"])]
    # cặp không có → thử cả biến thể rồi ném lỗi
    fake2 = FakeNmt({}, missing_error="Error: Could not find a valid model for the request source:target language pair: es:vi")
    translator._client = fake2
    with pytest.raises(Exception, match="không dịch được"):
        translator._translate_many(["hola"], job)
    assert fake2.calls == [("es-ES", "vi", ["hola"]), ("es-US", "vi", ["hola"])]


def test_retry_delay_backs_off_harder_on_rate_limit():
    from reviewtrans.core.providers import retry_delay

    assert retry_delay(0, RuntimeError("HTTP 429: Too Many Requests")) >= 5.0
    assert retry_delay(1, RuntimeError("quota exceeded")) >= 10.0
    assert retry_delay(0, RuntimeError("connection reset by peer")) < 5.0  # lỗi thường → thử lại nhanh
    assert all(retry_delay(attempt, RuntimeError("429")) <= 60.0 for attempt in range(8))


def test_updates_version_compare():
    from reviewtrans.core.updates import is_newer

    assert is_newer("2.3.0", "2.2.0")
    assert is_newer("v2.10.0", "2.9.9")
    assert not is_newer("2.2.0", "2.2.0")
    assert not is_newer("2.1.9", "2.2.0")


def test_cover_fit_scan(qapp):
    from PyQt6 import QtCore, QtGui

    from reviewtrans.core.cover_fit import _scan

    image = QtGui.QImage(640, 360, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor(20, 30, 60))
    painter = QtGui.QPainter(image)
    painter.fillRect(QtCore.QRect(200, 290, 240, 26), QtGui.QColor(255, 255, 255))
    painter.end()
    rect = _scan(image, (0.0, 0.5, 1.0, 0.5), min_width=0.05)
    assert rect is not None
    x, y, w, h = rect
    assert abs(x - 200 / 640) < 0.05 and abs(y - 290 / 360) < 0.05
    assert abs(w - 240 / 640) < 0.08 and abs(h - 26 / 360) < 0.05
    # nền trống → không dò ra chữ
    empty = QtGui.QImage(640, 360, QtGui.QImage.Format.Format_RGB32)
    empty.fill(QtGui.QColor(20, 30, 60))
    assert _scan(empty, (0.0, 0.5, 1.0, 0.5), min_width=0.05) is None
