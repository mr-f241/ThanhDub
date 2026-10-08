"""Nhận dạng giọng nói bằng Moonshine (moonshine-ai) thay cho whisper.cpp.

Moonshine nhỏ và nhanh nhưng chỉ nhận tối đa 30 giây mỗi lần, nên âm thanh được cắt thành các đoạn
≤ `MAX_CHUNK_SECONDS`, cắt ở chỗ im lặng gần mốc để không cắt giữa tiếng. Mỗi đoạn cho ra text
(kèm timestamp nếu phiên bản transformers hỗ trợ), sau đó tách thành câu ngắn và chia lại thời lượng
theo độ dài để phụ đề không bị một đoạn 25 giây.
"""
from __future__ import annotations

import threading
import wave
from array import array
from pathlib import Path
from typing import Sequence

from ..config import whisper_thread_count
from ..models import Segment
from ..proc import ToolMissing
from . import RunContext
from .resources import (
    MOONSHINE_LANGUAGES,
    ensure_moonshine_model,
    moonshine_model_dir,
    moonshine_runtime,
    moonshine_short_name,
)

SAMPLE_RATE = 16000
MAX_CHUNK_SECONDS = 26.0  # Moonshine nhận tối đa 30s, chừa chút cho chỗ cắt
HOP_SECONDS = 0.02  # bước tính năng lượng (20 ms)
MIN_SEGMENT = 0.25  # câu ngắn hơn thì bỏ
MAX_CHARS = 42  # số ký tự mỗi dòng phụ đề
MAX_REPETITION_RATIO = 0.30  # trên tỉ lệ này coi như model đang lặp, không phải lời nói

# `max_position_embeddings` của Moonshine chỉ 194 → decoder cứng, generate dừng ở đó và
# phần sau bị rã thành từng ký tự rời rạc. Tốc độ token phụ thuộc ngôn ngữ (tiếng Trung
# ~19 token/giây, tiếng Anh chỉ ~4) nên tao cắt đoạn theo **ngân sách token** chứ không
# theo giây cố định, đo được rồi thu hẹp dần để chắc không bao giờ tràn.
TOKEN_BUDGET = 170  # chừa 24 token cho decoder_start/bos/eos
DEFAULT_TOKENS_PER_SECOND = 12.0  # ước lượng ban đầu trước khi đo được
MIN_CHUNK_SECONDS = 3.0

# Ký tự Hán/Kana + dấu câu CJK. Khoảng trắng xen giữa hai ký tự trong nhóm này luôn là
# nhiễu do tokenizer (token `▁`), không phải ranh giới từ thật.
CJK = "\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uff65"

_engine_lock = threading.Lock()
_engines: dict[str, tuple[object, object]] = {}


# ------------------------------------------------------------------ cắt đoạn


def _rms_threshold(rms: Sequence[float]) -> float:
    peak = max(rms, default=0.0)
    return max(1e-4, 0.08 * peak)


def plan_chunks(
    rms: Sequence[float],
    hop: float = HOP_SECONDS,
    max_seconds: float = MAX_CHUNK_SECONDS,
    tail: float = 3.0,
    start_frame: int = 0,
) -> list[float]:
    """Các mốc bắt đầu (giây) của các đoạn âm thanh, mỗi đoạn ≤ `max_seconds`.

    Ưu tiên cắt ở khoảng lặng dài nhất nằm trong `tail` giây cuối đoạn; không có khoảng lặng
    nào thì cắt thẳng ở mốc. Luôn tiến được để tránh lặp vô hạn trên âm thanh im lặng.

    `start_frame` cho phép tính lại kế hoạch từ giữa audio khi đã đo được tốc độ token
    và cần thu nhỏ đoạn (xem `chunk_seconds_for`).
    """
    frames = len(rms)
    if start_frame >= frames:
        return []
    total_seconds = (frames - start_frame) * hop
    if total_seconds <= max_seconds:
        return [start_frame * hop]
    threshold = _rms_threshold(rms)
    window = int(max_seconds / hop)
    search = int(tail / hop)
    floor = int(4.0 / hop)
    boundaries: list[float] = []
    start = start_frame
    while (frames - start) * hop > max_seconds + 1e-6:
        limit = min(start + window, frames - 1)
        best = -1
        best_run = 0
        run_start = -1
        for index in range(max(start + floor, limit - search), limit + 1):
            if rms[index] <= threshold:
                run_start = index if run_start < 0 else run_start
                if index - run_start + 1 > best_run:
                    best_run, best = index - run_start + 1, run_start
            else:
                run_start = -1
        cut = best + best_run // 2 if best >= 0 and best_run >= 5 else limit
        cut = max(cut, start + 1)
        boundaries.append(start * hop)
        start = cut
    boundaries.append(start * hop)
    return boundaries


def chunk_seconds_for(tokens_per_second: float, budget: int = TOKEN_BUDGET) -> float:
    """Độ dài đoạn (giây) sao cho số token sinh ra vừa đủ dưới ngân sách của decoder.

    Moonshine có `max_position_embeddings = 194` và **không** hỗ trợ sliding window, nên
    generate dừng cứng ở đó. Tốc độ token khác nhau theo ngôn ngữ (tiếng Trung ~19 token/giây,
    tiếng Anh ~4), nên phải đo thay vì đoán: đoán sai là đoạn bị cắt và rã thành từng chữ.
    """
    rate = max(1.0, tokens_per_second)
    return min(MAX_CHUNK_SECONDS, max(MIN_CHUNK_SECONDS, budget / rate))


# ------------------------------------------------------------------ âm thanh


def read_wav(path: Path) -> tuple["list[float]", float]:
    """Đọc wav PCM 16 bit → (mẫu float32 dạng list, số giây). Dùng list để không phụ thuộc numpy."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if width != 2:
        raise ValueError(f"Cần wav 16 bit, file này {width * 8} bit")
    data = array("h")
    data.frombytes(raw[: len(raw) - (len(raw) % 2)])
    scale = 1.0 / 32768.0
    samples = [value * scale for value in data]
    if channels > 1:
        samples = [
            sum(samples[index:index + channels]) / channels
            for index in range(0, len(samples) - channels + 1, channels)
        ]
    return samples, (len(samples) / rate if rate else 0.0)


def frame_rms(samples: Sequence[float], hop: float = HOP_SECONDS) -> list[float]:
    step = max(1, int(hop * SAMPLE_RATE))
    frames: list[float] = []
    for start in range(0, len(samples), step):
        window = samples[start:start + step]
        if not window:
            break
        frames.append((sum(value * value for value in window) / len(window)) ** 0.5)
    return frames


# ------------------------------------------------------------------ sinh text


def split_lines(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Tách text dài thành các dòng ngắn: ưu tiên câu, rồi dấu phẩy, cuối cùng theo từ."""
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    sentence = ""
    for char in text:
        sentence += char
        if char in ".!?。！？" and len(sentence.strip()) >= 8:
            pieces.append(sentence.strip())
            sentence = ""
    if sentence.strip():
        pieces.append(sentence.strip())
    lines: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            lines.append(piece)
            continue
        chunk = ""
        for word in piece.split(" "):
            if chunk and len(chunk) + len(word) + 1 > max_chars:
                lines.append(chunk.strip())
                chunk = word
            else:
                chunk = f"{chunk} {word}".strip()
        if chunk.strip():
            lines.append(chunk.strip())
    return [line for line in lines if line]


def spread_times(lines: Sequence[str], start: float, end: float) -> list[tuple[float, float, str]]:
    """Chia khoảng thời gian cho các dòng theo tỉ lệ độ dài (Moonshine không trả timestamp từng câu)."""
    if not lines:
        return []
    weights = [max(1, len(line)) for line in lines]
    total = sum(weights)
    span = max(end - start, 0.3 * len(lines))
    out: list[tuple[float, float, str]] = []
    cursor = start
    for line, weight in zip(lines, weights):
        length = span * weight / total
        out.append((cursor, cursor + length, line))
        cursor += length
    return out


def clean_text(text: str) -> str:
    """Bỏ token đặc biệt `<s>`/`<|transcribe|>`, gộp khoảng trắng, và dán chữ CJK lại.

    Bản `moonshine-base-zh` đôi khi sinh token `▁` (mã 29871) trước từng Hán tự, biến
    `却不想出门` thành `却 不 想 出 门`. Khoảng trắng xen giữa hai chữ Hán luôn vô nghĩa nên
    tao bỏ; khoảng trắng cạnh chữ Latin hoặc dấu câu thì giữ nguyên.
    """
    import re

    stripped = re.sub(r"<[^<>]{0,40}>", " ", text)
    glued = re.sub(f"(?<=[{CJK}])[ \t]+(?=[{CJK}])", "", stripped)
    return " ".join(glued.split())


def parse_timestamped(text: str, offset: float = 0.0) -> list[tuple[float, float, str]]:
    """Đọc kiểu output `[mm:ss.mmm - mm:ss.mmm] câu` của transformers, bỏ qua phần không khớp.

    Checkpoint Moonshine của tao **không** có token timestamp nên output thường là text trần
    (`<s> … </s>`); trường hợp đó hàm trả về một mảng `(offset, offset, text)` để caller rải
    thời lượng theo độ dài.
    """
    import re

    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\s*-\s*(\d+):(\d+(?:\.\d+)?)\]\s*")
    out: list[tuple[float, float, str]] = []
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            _flush(text[cursor:match.start()], offset, out)
        start = int(match.group(1)) * 60 + float(match.group(2)) + offset
        end = int(match.group(3)) * 60 + float(match.group(4)) + offset
        body = text[match.end():]
        nxt = pattern.search(body)
        chunk = body[: nxt.start()] if nxt else body
        cleaned = clean_text(chunk)
        if cleaned:
            out.append((start, end, cleaned))
        cursor = match.end() + (nxt.start() if nxt else len(body))
    if cursor < len(text):
        _flush(text[cursor:], offset, out)
    return out


def _flush(chunk: str, offset: float, out: list[tuple[float, float, str]]) -> None:
    cleaned = clean_text(chunk)
    if cleaned:
        out.append((offset, offset, cleaned))


# ------------------------------------------------------------------ engine


def engine_available() -> tuple[bool, str]:
    ok, missing = moonshine_runtime()
    if ok:
        return True, ""
    return False, f"Moonshine cần torch + transformers ({missing}). Vào Tài nguyên để cài."


def load_engine(model: str) -> tuple[object, object]:
    """Nạp (processor, model) theo model, cache lại để video sau không phải đọc disk."""
    path = str(moonshine_model_dir(model))
    with _engine_lock:
        cached = _engines.get(path)
    if cached is not None:
        return cached
    ok, why = engine_available()
    if not ok:
        raise ToolMissing(why)
    if not (Path(path) / "config.json").is_file():
        raise ToolMissing(f"Chưa có model Moonshine {moonshine_short_name(model)}. Vào Tài nguyên để tải.")
    import torch
    from transformers import AutoProcessor, MoonshineForConditionalGeneration

    torch.set_grad_enabled(False)
    # transformers map moonshine → Wav2Vec2Processor (không có MoonshineProcessor
    # riêng) nên phải gọi qua AutoProcessor mới đúng trên mọi phiên bản.
    processor = AutoProcessor.from_pretrained(path)
    model_object = MoonshineForConditionalGeneration.from_pretrained(path, dtype=torch.float32)
    model_object.eval()
    engine = (processor, model_object)
    with _engine_lock:
        _engines[path] = engine
    return engine


def forget_engine(model: str = "") -> None:
    """Xoá model đang nạp khỏi RAM (dùng sau khi đổi model trong Cài đặt)."""
    with _engine_lock:
        if model:
            _engines.pop(str(moonshine_model_dir(model)), None)
        else:
            _engines.clear()


def set_threads(count: int) -> None:
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, count))


def _prepare_inputs(processor, chunk: Sequence[float]):
    """Đưa đoạn sóng âm vào tensor mà model chấp nhận.

    transformers không có `MoonshineProcessor`; cả `moonshine` lẫn bản streaming đều được map
    sang `Wav2Vec2Processor`/`Wav2Vec2FeatureExtractor`, và `MoonshineForConditionalGeneration`
    ăn **sóng âm thô** (`input_values`, conv1d stride 64) chứ không phải mel. Nên tao gọi
    processor bằng `audio=` (tên tham số của feature extractor dùng chung) rồi tự chọn key.
    """
    audio = list(chunk)
    try:
        inputs = processor(
            audio=audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=False
        )
    except TypeError:  # bản cũ chỉ nhận samples=
        inputs = processor(
            samples=audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=False
        )
    for key in ("input_features", "input_values"):
        value = getattr(inputs, key, None)
        if value is None and isinstance(inputs, dict):
            value = inputs.get(key)
        if value is not None:
            return value
    return getattr(inputs, "input_values", inputs)


def _generate(model, features) -> object:
    """Gọi `generate`, bỏ `return_timestamps` nếu bản transformers không nhận."""
    import torch

    kwargs = dict(max_new_tokens=440, num_beams=1, do_sample=False)
    with torch.inference_mode():
        try:
            return model.generate(features, return_timestamps=True, **kwargs)
        except (TypeError, ValueError):
            return model.generate(features, **kwargs)


def _transcribe_chunk(processor, model, chunk: Sequence[float]) -> str:
    """Trả về text thô của model (có timestamp nếu bản transformers hỗ trợ)."""
    generated = _generate(model, _prepare_inputs(processor, chunk))
    try:
        return processor.batch_decode(
            generated, skip_special_tokens=False, decode_with_timestamps=True
        )[0]
    except TypeError:  # transformers cũ không có decode_with_timestamps
        return processor.batch_decode(generated, skip_special_tokens=True)[0]


def repetition_ratio(text: str, window: int = 3) -> float:
    """Tỉ lệ phần trùng lặp trong text (0 = sạch, gần 1 = lặp một cụm).

    Moonshine khi nghe ngoài ngôn ngữ nó được huấn luyện (vd bản tiếng Anh đọc tiếng
    Trung) sẽ lặp lại một cụm ngữ vô nghĩa tới hết độ dài cho phép thay vì dừng. Đo bằng
    cách so tỉ lệ cụm ngữ 3 từ **không trùng nhau**: lời nói thật gần như không lặp, còn
    lặp chu kỳ thì chỉ còn vài cụm duy nhất dù dài bao nhiêu.
    """
    words = text.split()
    if len(words) < 2 * window:
        return 0.0
    grams = [" ".join(words[i:i + window]) for i in range(len(words) - window + 1)]
    return 1.0 - len(set(grams)) / len(grams)


def _count_tokens(processor, text: str) -> int:
    """Số token của text sau khi bỏ ký hiệu đặc biệt (dùng để canh ngân sách decoder)."""
    body = clean_text(text)
    if not body:
        return 0
    try:
        return len(processor.tokenizer(body)["input_ids"])
    except Exception:  # noqa: BLE001 - tokenizer khác nhau có thể ném lỗi khác nhau
        return len(body.split())


# ------------------------------------------------------------------ chạy


def run_moonshine(ctx: RunContext, audio: Path, model: str) -> list[Segment]:
    """Nhận dạng file wav 16 kHz mono, trả về các câu có timestamp (giây)."""
    ok, why = engine_available()
    if not ok:
        raise ToolMissing(why)
    directory = ensure_moonshine_model(model, lambda pct, msg: ctx.progress(pct * 0.05, msg), ctx.stop_event)
    ctx.check_stop()
    ctx.progress(5, "Nạp model Moonshine…")
    set_threads(whisper_thread_count(ctx.settings.whisper_threads))
    processor, model_object = load_engine(model)
    ctx.log(f"Moonshine: {moonshine_short_name(model)} ({directory})")

    samples, duration = read_wav(audio)
    if not samples:
        return []
    rms = frame_rms(samples)
    # Tốc độ token ban đầu là ước lượng; sau đoạn đầu sẽ hiệu chỉnh lại và tính lại kế hoạch
    # cắt từ chỗ đang đứng, nên không phải chạy lại từ đầu.
    rate = DEFAULT_TOKENS_PER_SECOND
    max_seconds = chunk_seconds_for(rate)
    boundaries = plan_chunks(rms, max_seconds=max_seconds)
    if not boundaries:
        return []
    ctx.log(f"Moonshine: {len(boundaries)} đoạn âm thanh, mỗi đoạn ≤ {max_seconds:.1f}s")

    collected: list[tuple[float, float, str]] = []
    repeated = 0
    truncated = 0
    done = 0
    index = 0
    while index < len(boundaries):
        ctx.check_stop()
        start = boundaries[index]
        end = boundaries[index + 1] if index + 1 < len(boundaries) else duration
        ctx.progress(5 + done * 90.0 / max(1, len(boundaries)), f"Moonshine · đoạn {done + 1}")
        chunk = samples[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        raw = _transcribe_chunk(processor, model_object, chunk)
        span = max(0.5, end - start)
        tokens = _count_tokens(processor, raw)

        # Hiệu chỉnh tốc độ token từ đoạn vừa rồi, có chặn trên/dưới để một đoạn rác không
        # kéo tốc độ điện rồi cắt vỡ đoạn sau.
        if tokens >= 4:
            rate = min(40.0, max(2.0, max(rate * 0.5, min(rate * 2.0, tokens / span))))

        if tokens > TOKEN_BUDGET:
            # Đoạn này tràn ngân sách token (decoder cắt cụt, phần sau rã thành từng chữ).
            # Chia lại chính đoạn này cho ngắn hơn rồi chạy lại — không bỏ mất âm đã nghe.
            truncated += 1
            max_seconds = chunk_seconds_for(rate)
            start_frame = int(round(start / HOP_SECONDS))
            replanned = plan_chunks(rms, max_seconds=max_seconds, start_frame=start_frame)
            if len(replanned) < 2 and max_seconds > MIN_CHUNK_SECONDS:
                # Không thu nhỏ được nữa → dừng cốt, chấp nhận cắt cụt đoạn này.
                max_seconds = MIN_CHUNK_SECONDS
            elif len(replanned) < 2:
                pass
            else:
                replanned[0] = start  # giữ đúng mốc đang xử lý, tránh lệch số thực
                boundaries[index:] = replanned
                ctx.log(f"Moonshine · đoạn {done + 1} dài {span:.1f}s sinh {tokens} token, chia nhỏ còn ≤ {max_seconds:.1f}s.")
                continue

        done += 1
        ratio = repetition_ratio(clean_text(raw))
        if ratio >= MAX_REPETITION_RATIO:
            # Model lặp: thường là ngôn ngữ nguồn không khớp model đang chạy. Bỏ hẳn đoạn
            # này thay vì đổ cụm rác vào timeline.
            repeated += 1
            ctx.log(
                f"Moonshine · đoạn {done} ({start:.0f}s): nghe không ra tiếng, "
                f"model lặp lại {ratio:.0%} — bỏ qua đoạn này."
            )
            index += 1
            continue
        timed = parse_timestamped(raw, offset=start)
        for seg_start, seg_end, text in timed:
            if seg_end <= seg_start:  # không có timestamp → chia theo độ dài
                for sub_start, sub_end, line in spread_times(split_lines(text), start, end):
                    collected.append((sub_start, sub_end, line))
                continue
            for line in split_lines(text):
                collected.append((seg_start, seg_end, line))
        ctx.check_stop()
        index += 1

    segments = _finalize(collected)
    if truncated:
        ctx.log(
            f"Moonshine: {truncated} đoạn bị cắt cụt vì hết ngân sách token, đã tự chia nhỏ "
            f"và chạy lại (giờ mỗi đoạn ≤ {max_seconds:.1f}s)."
        )
    if repeated:
        ctx.log(
            f"Moonshine: {repeated}/{done} đoạn nghe không ra tiếng nên bị bỏ. "
            f"Nếu hết video, hãy đổi sang model đúng ngôn ngữ nguồn "
            f"(Moonshine chỉ có bản riêng cho: {', '.join(MOONSHINE_LANGUAGES)})."
        )
    if not segments:
        return segments
    ctx.log(f"Moonshine nhận dạng được {len(segments)} câu.")
    return segments


def _finalize(pieces: Sequence[tuple[float, float, str]]) -> list[Segment]:
    """Sắp xếp, bỏ câu rỗng/quá ngắn, giới hạn end không vượt sang câu sau."""
    out: list[Segment] = []
    for start, end, text in sorted(pieces, key=lambda item: item[0]):
        text = text.strip()
        if not text:
            continue
        end = max(end, start + MIN_SEGMENT)
        if out and start < out[-1].end:
            out[-1].end = min(out[-1].end, start) if out[-1].end > start else start
            if not out[-1].source.strip():  # cùng thời điểm mà không có chữ → bỏ, đừng giữ câu rỗng
                out.pop()
        out.append(Segment(id=len(out) + 1, start=start, end=end, source=text))
    return [Segment(id=index + 1, start=seg.start, end=seg.end, source=seg.source)
            for index, seg in enumerate(out)]
