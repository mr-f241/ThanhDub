from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ...context import extract_json, name_rule
from ...langs import english_name
from ...proc import StopRequested
from .. import KeyRing, ProviderError, retry_delay
from ...config import ProviderProfile


@dataclass
class TranslateJob:
    source_lang: str = "auto"
    target_lang: str = "vi"
    context_text: str = ""
    glossary: list[tuple[str, str]] = field(default_factory=list)
    previous: list[tuple[str, str]] = field(default_factory=list)  # (gốc, đã dịch) ngay trước batch
    log: Callable[[str], None] | None = None
    stop_event: threading.Event | None = None

    def check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()

    def emit(self, message: str) -> None:
        if self.log:
            self.log(message)


class Translator:
    uses_context = False
    default_model = ""
    max_batch = 100

    def __init__(self, profile: ProviderProfile, model_override: str = "") -> None:
        self.profile = profile
        self.model = model_override or profile.model or self.default_model
        self.keys = KeyRing(profile.api_keys)

    def translate(self, lines: list[str], job: TranslateJob) -> list[str]:
        raise NotImplementedError

    def complete(self, system: str, prompt: str) -> str:
        raise ProviderError(f"{self.profile.name} không hỗ trợ sinh văn bản tự do (cần LLM).")

    def list_models(self) -> list[str]:
        """Danh sách model mà API cho phép (máy dịch không có model → rỗng)."""
        return []

    def test(self) -> str:
        result = self.translate(["你好，世界"], TranslateJob(source_lang="zh", target_lang="vi"))
        return result[0]


RULES = (
    "You are a professional subtitle translator for movie-recap / review videos that will be dubbed.\n"
    "GOAL: a line that is faithful to the source and still sounds like something a real narrator "
    "would say out loud — engaging, punchy, never flat or robotic.\n"
    "RULES:\n"
    "1. MEANING FIRST. Carry over every piece of information in the source: actions, results, "
    "emotions, twists, jokes and wordplay. Do not summarise, drop or quietly 'fix' plot details, and "
    "never translate word by word from a dictionary — read the whole line and render what it means.\n"
    "2. SPOKEN LANGUAGE. Write natural spoken {target}, not translationese: reorder, add or drop "
    "function words whenever that is how {target} speakers actually say it, as long as meaning is kept.\n"
    "3. LENGTH. The line is read aloud by TTS against the original timing: if the source line is "
    "short, the target must be short. Never pad, explain, or add notes, romanisation or quotes. "
    "If the source line is LONG, do not render it clause by clause — tighten it: cut filler and "
    "repetition, merge clauses, use compact spoken wording. Drop words, never facts, and keep the "
    "target about as long as the source so it still fits the original audio.\n"
    "4. ITEMS. Translate each item independently and keep the same number of items with the same ids. "
    "Never merge, split, skip, reorder or add items. Output only the requested format.\n"
    "5. NAMES & TERMS. Keep names, titles, sects, skills and forms of address exactly as given in the "
    "CONTEXT below, even when a general dictionary suggests something else.\n"
    "6. VOICE. Preserve the relationship and register of the speakers (pronouns, honorifics, insults, "
    "flattery) and the beat of the joke or reveal — do not flatten it into a neutral report.\n\n"
    "STYLE EXAMPLE (source Chinese, target Vietnamese)\n"
    "source: 你是在责怪老夫不敢冷坑一声，楚天翼甩袖离开孔浩然缓了一会才太恐慌。\n"
    "output: Ngươi đang trách lão phu không dám lên tiếng sao? Sở Thiên Dực phất tay rời đi, "
    "Khổng Hạo Nhiên hồi lâu mới hết hoảng loạn."
)


MIN_SOURCE_CHARS = 30
# tỉ lệ "dịch dài lố": trên dữ liệu thật tỉ lệ p99 (gốc >= 30 ký tự) là 5.79 → ngưỡng 6.0
MAX_TARGET_RATIO = 6.0


def is_too_long(source: str, target: str) -> bool:
    """Gốc dài mà bản dịch dài bất thường → không vừa thời lượng audio gốc."""
    source = source.strip()
    return len(source) >= MIN_SOURCE_CHARS and len(target) > MAX_TARGET_RATIO * len(source)


def is_repetitive(text: str, times: int = 4) -> bool:
    """Bản dịch chết loop (một cụm lặp lại >= `times` lần) — bệnh kinh điển của dịch lô lớn."""
    text = text.strip()
    for size in (8, 12, 6, 10):
        if len(text) < size * times:
            continue
        for start in range(0, len(text) - size * times + 1):
            chunk = text[start:start + size]
            if chunk.strip() and text.count(chunk) >= times:
                return True
    return False


def needs_repair(source: str, target: str) -> bool:
    return is_repetitive(target) or is_too_long(source, target)


class LLMTranslator(Translator):
    uses_context = True
    max_batch = 80

    def _call(self, system: str, prompt: str, key: str) -> str:
        raise NotImplementedError

    def complete(self, system: str, prompt: str) -> str:
        return self._with_retry(lambda key: self._call(system, prompt, key), attempts=max(3, len(self.keys)))

    def _with_retry(self, func, attempts: int = 3, job: TranslateJob | None = None):
        last_error: Exception | None = None
        for attempt in range(attempts):
            if job:
                job.check_stop()
            key = self.keys.next()
            try:
                return func(key)
            except StopRequested:
                raise
            except Exception as exc:  # noqa: BLE001 - SDK khác nhau ném lỗi khác nhau
                last_error = exc
                if job:
                    job.emit(f"  lỗi {type(exc).__name__}: {str(exc)[:300]} — thử lại ({attempt + 1}/{attempts})")
                if _is_fatal(exc) and len(self.keys) <= 1:
                    break
                # 429 thì chờ 5/10/20s (server free tier cần nghỉ lâu), lỗi thường vẫn thử lại nhanh
                time.sleep(retry_delay(attempt, exc))
        raise ProviderError(f"{self.profile.name}: {last_error}") from last_error

    def build_prompts(self, lines: list[str], job: TranslateJob) -> tuple[str, str]:
        target = english_name(job.target_lang)
        system = RULES.format(target=target)
        names = name_rule(job.target_lang)
        if names:
            system += "\n" + names
        if job.context_text:
            system += "\n\nPROJECT CONTEXT:\n" + job.context_text
        items = [{"id": i + 1, "text": text} for i, text in enumerate(lines)]
        prompt = f"Translate from {english_name(job.source_lang)} to {target}.\n"
        if job.previous:
            prompt += "Previous lines for continuity (already translated, do NOT output them):\n"
            prompt += "\n".join(f"{src} => {dst}" for src, dst in job.previous) + "\n\n"
        # dạng đánh số từng dòng: model nhỏ hỏng JSON (khoảng 4/5 lần) nhưng sinh danh sách đánh số ổn định
        prompt += "Items:\n" + "\n".join(f"{item['id']}. {item['text']}" for item in items) + "\n\n"
        prompt += (
            f"Return EXACTLY {len(items)} lines in the same order, each line starting with its id "
            "followed by a dot and a space:\n"
            "1. <translation>\n2. <translation>\n…\n"
            "No extra text, no blank lines, no explanations."
        )
        return system, prompt

    def translate(self, lines: list[str], job: TranslateJob) -> list[str]:
        if not lines:
            return []
        return self._repair(lines, self._translate_batch(lines, job), job)

    def _translate_batch(self, lines: list[str], job: TranslateJob) -> list[str]:
        system, prompt = self.build_prompts(lines, job)
        # parse fail một lần là chia nhỏ lô ngay: retry cùng prompt với model nhỏ hầu như không đổi gì
        raw = self._with_retry(lambda key: self._call(system, prompt, key), attempts=max(3, len(self.keys)), job=job)
        result = parse_items(raw, len(lines))
        if result is not None:
            return result
        if len(lines) <= 3:
            out = []
            for line in lines:
                single = self._with_retry(
                    lambda key, l=line: self._call(*self.build_prompts([l], job), key), job=job
                )
                parsed = parse_items(single, 1)
                out.append(parsed[0] if parsed else single.strip())
            return out
        middle = len(lines) // 2
        job.emit(f"  chia nhỏ batch {len(lines)} → {middle} + {len(lines) - middle}")
        return self.translate(lines[:middle], job) + self.translate(lines[middle:], job)

    def _repair(self, lines: list[str], results: list[str], job: TranslateJob) -> list[str]:
        """Câu chết loop / dịch dài lố → hỏi lại đúng các câu đó một lượt, giữ bản gọn hơn."""
        out = list(results)
        hits = [i for i, (src, dst) in enumerate(zip(lines, out)) if needs_repair(src, dst)]
        if not hits:
            return out
        job.emit(f"  {len(hits)} câu bị lặp vòng hoặc dài lố — dịch lại cho gọn")
        system, prompt = self.build_prompts([lines[i] for i in hits], job)
        prompt += (
            "\nNOTE: the previous attempt at these lines either looped or came out far too long. "
            "Compress the wording — cut filler, repetition and redundant clauses, keep every fact, "
            "and make each result about as long as its own source line."
        )
        try:
            raw = self._with_retry(
                lambda key: self._call(system, prompt, key), attempts=max(2, len(self.keys)), job=job
            )
        except ProviderError:
            return out  # dịch vụ không trả được -> giữ nguyên bản cũ
        parsed = parse_items(raw, len(hits))
        if parsed is None:
            return out
        for index, text in zip(hits, parsed):
            if not needs_repair(lines[index], text) or len(text) < len(out[index]):
                out[index] = text
        return out


def parse_items(raw: str, expected: int) -> list[str] | None:
    try:
        data = extract_json(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        for key in ("items", "translations", "result", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            if all(str(k).isdigit() for k in data.keys()):
                data = [{"id": int(k), "text": v} for k, v in data.items()]
    if isinstance(data, list):
        mapping: dict[int, str] = {}
        for position, item in enumerate(data, start=1):
            if isinstance(item, dict):
                ident = item.get("id", position)
                text = item.get("text", item.get("t", item.get("translation", "")))
            else:
                ident, text = position, item
            try:
                mapping[int(ident)] = str(text).strip()
            except (TypeError, ValueError):
                continue
        if all(i in mapping for i in range(1, expected + 1)):
            return [mapping[i] for i in range(1, expected + 1)]
        return None
    numbered = _parse_numbered(raw, expected)
    if numbered is not None:
        return numbered
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if len(lines) == expected:
        return lines
    return None


def _parse_numbered(raw: str, expected: int) -> list[str] | None:
    """Đọc kết quả dạng '1. …' / '1) …' — dạng mà model nhỏ sinh ổn định hơn JSON."""
    body = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
    body = re.sub(r"\s*```\s*$", "", body)
    lines = [line for line in body.splitlines() if line.strip()]
    mapping: dict[int, str] = {}
    matched: dict[int, str] = {}  # giữ cả số thứ tự theo vị trí dòng
    for position, line in enumerate(lines):
        match = re.match(r"^\s*(\d+)\s*[.)]\s+(.*)$", line)
        if match:
            mapping[int(match.group(1))] = match.group(2).strip()
            matched[position] = match.group(2).strip()
    if mapping and all(i in mapping for i in range(1, expected + 1)):
        return [mapping[i] for i in range(1, expected + 1)]
    # model sót mất số của một dòng đầu → số dòng vẫn đúng nên xếp theo vị trí,
    # tuyệt đối không trả nguyên dòng còn số thứ tự
    if matched and len(lines) == expected:
        return [
            matched.get(position, re.sub(r"^\.\s*", "", line).strip())
            for position, line in enumerate(lines)
        ]
    return None


def _is_fatal(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (400, 401, 403, 404):
        return "rate" not in text and "quota" not in text
    return False
