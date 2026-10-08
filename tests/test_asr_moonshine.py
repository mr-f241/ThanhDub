"""Test các hàm thuần của Moonshine (không cần torch, không cần model)."""
import pytest

from reviewtrans.core.pipeline.asr_moonshine import (
    MAX_CHUNK_SECONDS,
    MIN_CHUNK_SECONDS,
    TOKEN_BUDGET,
    _finalize,
    _generate,
    _prepare_inputs,
    chunk_seconds_for,
    clean_text,
    parse_timestamped,
    plan_chunks,
    repetition_ratio,
    spread_times,
    split_lines,
)
from reviewtrans.core.pipeline.resources import (
    KNOWN_MOONSHINE_MODELS,
    moonshine_language,
    suggest_moonshine_model,
)


def _flat(seconds: float, hop: float = 0.02, quiet: tuple[float, float] | None = None) -> list[float]:
    """Năng lượng mô phỏng: 0.5 (có tiếng) hoặc 0 (im lặng trong khoảng `quiet`)."""
    frames = int(seconds / hop)
    out = []
    for index in range(frames):
        t = index * hop
        silent = quiet is not None and quiet[0] <= t < quiet[1]
        out.append(0.0 if silent else 0.5)
    return out


def test_plan_chunks_stays_within_limit():
    boundaries = plan_chunks(_flat(60.0))
    assert boundaries[0] == 0.0
    assert all(later - earlier <= MAX_CHUNK_SECONDS + 1e-6 for earlier, later in zip(boundaries, boundaries[1:]))
    assert boundaries[-1] < 60.0
    assert len(boundaries) >= 2


def test_plan_chunks_prefers_silence_and_keeps_progress():
    # im lặng ở giây 24.5–25.5 nằm trong 3 giây cuối đoạn 26s → cắt ở đó
    boundaries = plan_chunks(_flat(60.0, quiet=(24.5, 25.5)))
    assert boundaries[1] > 24.0 and boundaries[1] < 26.0
    # âm thanh im lặng hoàn toàn vẫn tiến được (không lặp vô hạn)
    silent = plan_chunks(_flat(60.0, quiet=(0.0, 60.0)))
    assert len(silent) == 3 and silent[0] == 0.0
    assert all(later > earlier for earlier, later in zip(silent, silent[1:]))
    assert all(later - earlier <= MAX_CHUNK_SECONDS + 1e-6 for earlier, later in zip(silent, silent[1:]))
    assert silent[-1] < 60.0
    # quá ngắn thì chỉ có một mốc; rỗng thì không có gì
    assert plan_chunks(_flat(5.0)) == [0.0]
    assert plan_chunks([]) == []


def test_split_lines_keeps_words_and_length():
    assert split_lines("Ngươi đến rồi à") == ["Ngươi đến rồi à"]
    long = (
        "Đây là câu thứ nhất rất dài để buộc phải tách. Đây là câu thứ hai cũng khá dài, "
        "có cả dấu phẩy để cắt nếu cần. Cuối cùng là câu thứ ba."
    )
    lines = split_lines(long, max_chars=40)
    assert len(lines) > 1
    assert " ".join(lines).split() == long.split()
    assert all(len(line) <= 40 for line in lines[:-1])
    assert split_lines("   ") == []


def test_spread_times_splits_by_length():
    out = spread_times(["aaaa", "bb"], 0.0, 6.0)
    assert out == [(0.0, 4.0, "aaaa"), (4.0, 6.0, "bb")]
    assert spread_times([], 0.0, 10.0) == []


def test_parse_timestamped():
    text = "[00:01.00 - 00:03.50] Xin chào   [00:04.00 - 00:06.00] Bạn khỏe không"
    assert parse_timestamped(text) == [(1.0, 3.5, "Xin chào"), (4.0, 6.0, "Bạn khỏe không")]
    shifted = parse_timestamped(text, offset=10.0)
    assert shifted[0][:2] == (11.0, 13.5) and shifted[1][2] == "Bạn khỏe không"
    # không có timestamp → trả về một đoạn với mốc bắt đầu, end <= start (bước sau sẽ chia lại)
    assert parse_timestamped("Không có mốc giờ") == [(0.0, 0.0, "Không có mốc giờ")]


def test_finalize_drops_empty_and_fixes_overlap():
    segments = _finalize([(1.0, 2.0, "  a  "), (1.5, 3.0, "b"), (3.0, 3.1, "   "), (4.0, 5.0, "c")])
    assert [s.source for s in segments] == ["a", "b", "c"]
    assert [s.id for s in segments] == [1, 2, 3]
    assert segments[0].end <= segments[1].start


def test_chunk_seconds_for_respects_decoder_token_budget():
    # Moonshine chỉ có 194 vị trí → ~170 token. Tốc độ token đo được phải quy ra giây.
    assert chunk_seconds_for(19.0) == pytest.approx(TOKEN_BUDGET / 19.0, rel=0.01)
    # tiếng Anh token thưa nên vẫn ăn cả đoạn dài nhất cho phép
    assert chunk_seconds_for(4.0) == MAX_CHUNK_SECONDS
    # chặn hai đầu: quá nhanh không cắt vỡ, quá chậm cũng không cắt nhỏ vô ích
    assert chunk_seconds_for(1000.0) >= MIN_CHUNK_SECONDS
    assert chunk_seconds_for(0.0) == MAX_CHUNK_SECONDS


def test_plan_chunks_resumes_from_start_frame():
    """Chia lại giữa audio (khi đoạn tràn ngân sách token) không mất phần đã nghe."""
    rms = _flat(60.0)
    tail = plan_chunks(rms, max_seconds=10.0, start_frame=int(20.0 / 0.02))
    assert tail[0] == pytest.approx(20.0, abs=0.02)
    assert all(later > earlier for earlier, later in zip(tail, tail[1:]))
    assert all(later - earlier <= 10.0 + 1e-6 for earlier, later in zip(tail, tail[1:]))
    assert plan_chunks(rms, max_seconds=10.0, start_frame=len(rms)) == []


def test_repetition_ratio_flags_looping_hallucination():
    clean = "The quick brown fox jumps over the lazy dog near the riverbank today"
    assert repetition_ratio(clean) == 0.0
    assert repetition_ratio("ngắn quá") == 0.0
    # đúng cái lỗi tao thấy: bản tiếng Anh đọc tiếng Trung, model lặp một cụm
    loop = " ".join(["price of the"] * 20)
    assert repetition_ratio(loop) > 0.30


def test_clean_text_strips_special_tokens():
    # transformers không có MoonshineProcessor nên output là `<s> ... </s>` trần
    assert clean_text("<s> Xin chào bạn </s>") == "Xin chào bạn"
    assert clean_text("<|transcribe|> A <|endoftext|>") == "A"
    assert clean_text("  ") == ""


def test_clean_text_glues_spaced_cjk():
    # moonshine-base-zh hay sinh token `▁` trước từng Hán tự → rã từng chữ
    assert clean_text("却 不 想 出 门 就 碰 到 了") == "却不想出门就碰到了"
    assert clean_text("你 默 默 存 了 个 当") == "你默默存了个当"
    # khoảng trắng cạnh chữ Latin / dấu câu thì phải giữ
    assert clean_text("hello world") == "hello world"
    assert clean_text("xin chào bạn") == "xin chào bạn"
    assert clean_text("。 下 一个") == "。下一个"


def test_prepare_inputs_falls_back_across_processor_shapes():
    """`MoonshineProcessor` không tồn tại; cả 2 venv đều map sang Wav2Vec2 → `input_values`."""

    class OnlySamples:
        def __call__(self, samples=None, sampling_rate=None, return_tensors=None, padding=None):
            self.got = samples
            return {"input_values": [1, 2, 3]}

    class AttrOnly:
        def __call__(self, **kwargs):
            return type("Out", (), {"input_features": [9, 9]})()

    samples_only = OnlySamples()
    assert _prepare_inputs(samples_only, [0.1, 0.2]) == [1, 2, 3]
    assert samples_only.got == [0.1, 0.2]
    assert _prepare_inputs(AttrOnly(), [0.1]) == [9, 9]


def test_generate_drops_unsupported_return_timestamps():
    """Model này không nhận `return_timestamps` — phải gọi lại không có nó."""

    class Model:
        def __init__(self):
            self.calls = []

        def generate(self, features, **kwargs):
            self.calls.append(kwargs)
            if "return_timestamps" in kwargs:
                raise ValueError("The following `model_kwargs` are not used by the model")
            return "ok"

    model = Model()
    assert _generate(model, [1]) == "ok"
    assert "return_timestamps" in model.calls[0]
    assert "return_timestamps" not in model.calls[1]
    assert model.calls[1]["do_sample"] is False


def test_moonshine_model_names():
    assert moonshine_language("moonshine-ai/moonshine-tiny-zh") == "zh"
    assert moonshine_language("moonshine-tiny") == "en"
    assert suggest_moonshine_model("vi") == "moonshine-ai/moonshine-base-vi"
    assert suggest_moonshine_model("en") == "moonshine-ai/moonshine-base"
    assert suggest_moonshine_model("fr") == "moonshine-ai/moonshine-base"  # chưa có bản riêng
    assert suggest_moonshine_model("", size="tiny") == "moonshine-ai/moonshine-tiny"
    assert "moonshine-ai/moonshine-base-vi" in KNOWN_MOONSHINE_MODELS
    assert "moonshine-ai/moonshine-tiny" in KNOWN_MOONSHINE_MODELS
