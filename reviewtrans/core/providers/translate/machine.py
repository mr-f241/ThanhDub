"""Máy dịch không dùng ngữ cảnh: Google Translate và Microsoft Translator."""
from __future__ import annotations

import threading
import time

import requests

from ...context import apply_glossary_to_source
from ...langs import google_code, microsoft_code
from .. import ProviderError, retry_delay
from .base import TranslateJob, Translator

MAX_CHARS = 4500

# endpoint miễn phí bị giới hạn nghiêm ngặt hơn nhiều soo với API key
GOOGLE_API_BASE = "https://translation.googleapis.com"
GOOGLE_FREE_BASE = "https://translate.googleapis.com"
GOOGLE_API_PATH = "/language/translate/v2"
GOOGLE_FREE_PATH = "/translate_a/single"
FREE_MAX_CHARS = 1800  # mỗi lần gọi endpoint web: cắt nhỏ để ít bị 429
FREE_MAX_ITEMS = 25
FREE_MIN_INTERVAL = 0.4  # giãn cách tối thiểu giữa hai lần gọi endpoint web


def _chunks(lines: list[str], max_chars: int, max_items: int) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, line in enumerate(lines):
        length = len(line) + 1
        if current and (size + length > max_chars or len(current) >= max_items):
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        groups.append(current)
    return groups


class MachineTranslator(Translator):
    uses_context = False

    def translate(self, lines: list[str], job: TranslateJob) -> list[str]:
        prepared = [apply_glossary_to_source(line, job.glossary) for line in lines]
        results = [""] * len(lines)
        for group in _chunks(prepared, MAX_CHARS, self.max_batch):
            job.check_stop()
            texts = [prepared[i] for i in group]
            translated = self._retry(lambda: self._translate_many(texts, job), job)
            for i, text in zip(group, translated):
                results[i] = text
        return results

    def _retry(self, func, job: TranslateJob):
        last: Exception | None = None
        for attempt in range(4):
            job.check_stop()
            try:
                return func()
            except (requests.RequestException, ProviderError, ValueError, KeyError, IndexError) as exc:
                last = exc
                delay = retry_delay(attempt, exc)
                job.emit(f"  lỗi {type(exc).__name__}: {str(exc)[:200]} — thử lại sau {delay:.0f}s ({attempt + 1}/4)")
                time.sleep(delay)
        raise ProviderError(f"{self.profile.name}: {last}")

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        raise NotImplementedError

    def _endpoint(self, default_base: str, path: str) -> str:
        """Ghép Base URL của profile với path endpoint (Base URL đã chứa sẵn path thì dùng nguyên)."""
        root = (self.profile.base_url or default_base).rstrip("/")
        return root if root.endswith(path) else f"{root}{path}"


class GoogleTranslator(MachineTranslator):
    """Có API key → Cloud Translation v2 (chính xác từng dòng). Không có → endpoint web miễn phí.

    Base URL để trống thì dùng endpoint của Google; điền Base URL thì đi qua proxy/mirror tự chọn.
    """
    max_batch = 100

    _free_lock = threading.Lock()
    _free_last = 0.0

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        key = self.keys.next()
        target = google_code(job.target_lang)
        source = google_code(job.source_lang)
        if key:
            payload = {"q": texts, "target": target, "format": "text"}
            if source and source != "auto":
                payload["source"] = source
            response = requests.post(
                self._endpoint(GOOGLE_API_BASE, GOOGLE_API_PATH),
                params={"key": key},
                json=payload,
                timeout=60,
            )
            if response.status_code != 200:
                raise ProviderError(f"Google {response.status_code}: {response.text[:300]}")
            return [item["translatedText"] for item in response.json()["data"]["translations"]]
        out = [""] * len(texts)
        # gọi theo lát nhỏ (FREE_MAX_*) thay vì gộp 4500 ký tự → giảm hẳn 429
        for group in _chunks(texts, FREE_MAX_CHARS, FREE_MAX_ITEMS):
            joined = "\n".join(texts[i].replace("\n", " ") for i in group)
            parts = self._free(joined, source or "auto", target).split("\n")
            if len(parts) == len(group):
                for i, value in zip(group, parts):
                    out[i] = value.strip()
                continue
            if len(group) == 1:  # Google tách một dòng thành nhiều đoạn → ghép lại
                out[group[0]] = " ".join(part.strip() for part in parts if part.strip())
                continue
            for i in group:  # lệch dòng → dịch từng dòng
                out[i] = self._free(texts[i], source or "auto", target).strip()
        return out

    def _free(self, text: str, source: str, target: str) -> str:
        with GoogleTranslator._free_lock:  # chặn gọi ồ ạt → Google trả 429 ngay
            wait = FREE_MIN_INTERVAL - (time.time() - GoogleTranslator._free_last)
            if wait > 0:
                time.sleep(wait)
            GoogleTranslator._free_last = time.time()
        response = requests.post(
            self._endpoint(GOOGLE_FREE_BASE, GOOGLE_FREE_PATH),
            params={"client": "gtx", "sl": source, "tl": target, "dt": "t"},
            data={"q": text},
            timeout=60,
        )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            hint = f", chờ {retry_after}s" if retry_after else ""
            raise ProviderError(f"Google 429: bị giới hạn tần suất{hint} — thử lại sau")
        if response.status_code != 200:
            raise ProviderError(f"Google trả về {response.status_code}")
        data = response.json()
        return "".join(part[0] for part in data[0] if part and part[0])


class MicrosoftTranslator(MachineTranslator):
    """Có key → Azure Translator. Không có → token miễn phí của Microsoft Edge."""

    max_batch = 100
    _edge_token: str = ""
    _edge_expiry: float = 0.0
    _edge_lock = threading.Lock()

    def _auth_headers(self) -> dict:
        key = self.keys.next()
        if key:
            headers = {"Ocp-Apim-Subscription-Key": key}
            if self.profile.region:
                headers["Ocp-Apim-Subscription-Region"] = self.profile.region
            return headers
        with MicrosoftTranslator._edge_lock:
            if time.time() > MicrosoftTranslator._edge_expiry:
                response = requests.get("https://edge.microsoft.com/translate/auth", timeout=30)
                response.raise_for_status()
                MicrosoftTranslator._edge_token = response.text.strip()
                MicrosoftTranslator._edge_expiry = time.time() + 8 * 60
            return {"Authorization": f"Bearer {MicrosoftTranslator._edge_token}"}

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        base = (self.profile.base_url or "https://api.cognitive.microsofttranslator.com").rstrip("/")
        params = {"api-version": "3.0", "to": microsoft_code(job.target_lang)}
        source = microsoft_code(job.source_lang)
        if source:
            params["from"] = source
        response = requests.post(
            f"{base}/translate",
            params=params,
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json=[{"Text": t} for t in texts],
            timeout=60,
        )
        if response.status_code == 401:
            MicrosoftTranslator._edge_expiry = 0
        if response.status_code != 200:
            raise ProviderError(f"Microsoft {response.status_code}: {response.text[:300]}")
        return [item["translations"][0]["text"] for item in response.json()]
