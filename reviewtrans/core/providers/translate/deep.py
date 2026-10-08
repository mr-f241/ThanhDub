"""Deep Translator — bọc thư viện `deep-translator`, gộp nhiều engine vào một kind.

Chọn engine ở ô Tuỳ chọn (key `engine`): google, mymemory, libre, deepl, microsoft,
yandex, papago, baidu. Engine cần key thì điền key vào ô API key
(baidu/papago cần hai giá trị, viết `appid:appkey` / `client_id:secret`).
"""
from __future__ import annotations

import re
import threading
import time

import requests

from ...langs import LANGUAGES, google_code
from ...proc import StopRequested
from .. import FatalProviderError, ProviderError, retry_delay
from .base import TranslateJob
from .machine import MachineTranslator

# engine -> (nhãn, cần key?, nhận nguồn "auto"?, endpoint free cần chặn nhịp?)
ENGINES: dict[str, tuple[str, bool, bool, bool]] = {
    "google": ("Google (web, miễn phí, không cần key)", False, True, True),
    "mymemory": ("MyMemory (miễn phí, không cần key)", False, False, True),
    "libre": ("LibreTranslate (cần key, hoặc trỏ Base URL vào mirror riêng)", True, True, False),
    "deepl": ("DeepL (cần key — không có tiếng Việt)", True, False, False),
    "microsoft": ("Microsoft Azure Translator (cần key)", True, True, False),
    "yandex": ("Yandex (cần key)", True, True, False),
    "papago": ("Papago Naver (cần `client_id:secret`)", True, True, False),
    "baidu": ("Baidu (cần `appid:appkey`)", True, True, False),
}

_CLASSES = {
    "google": "GoogleTranslator",
    "mymemory": "MyMemoryTranslator",
    "libre": "LibreTranslator",
    "deepl": "DeeplTranslator",
    "microsoft": "MicrosoftTranslator",
    "yandex": "YandexTranslator",
    "papago": "PapagoTranslator",
    "baidu": "BaiduTranslator",
}

FREE_MIN_INTERVAL = 0.4  # endpoint web: chặn gọi ồ ạt → 429
# Google free chặn IP tạm rất hay — nhảy sang engine free khác ngay, không đợi retry
FREE_FALLBACK: dict[str, str] = {"google": "mymemory", "mymemory": "libre", "libre": "mymemory"}

_maps_cache: dict | None = None


def _lib():
    try:
        import deep_translator
    except ImportError as exc:
        raise ProviderError("Chưa cài deep-translator (pip install deep-translator)") from exc
    return deep_translator


def _engine_maps() -> dict:
    """Bảng mã ngôn ngữ tĩnh của từng engine (lấy từ constants, import một lần)."""
    global _maps_cache
    if _maps_cache is None:
        _lib()
        from deep_translator import constants as dc

        _maps_cache = {
            "google": dc.GOOGLE_LANGUAGES_TO_CODES,
            "mymemory": dc.MY_MEMORY_LANGUAGES_TO_CODES,
            "libre": dc.LIBRE_LANGUAGES_TO_CODES,
            "deepl": dc.DEEPL_LANGUAGE_TO_CODE,
            "papago": dc.PAPAGO_LANGUAGE_TO_CODE,
            "baidu": dc.BAIDU_LANGUAGE_TO_CODE,
        }
    return _maps_cache


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _google_name(google_value: str) -> str:
    """'zh-CN' → 'chinese (simplified)' để đối chiếu với bảng của engine."""
    for name, value in _engine_maps()["google"].items():
        if value.lower() == google_value.lower():
            return name
    return google_value


def engine_lang(engine: str, code: str) -> str:
    """Mã ngôn ngữ nội bộ → mã mà engine chấp nhận (deep-translator kiểm khá chặt)."""
    if not code or code == "auto":
        return "auto"
    g = google_code(code)
    mapping = _engine_maps().get(engine)
    if not mapping:
        return g
    by_value = {str(v).lower(): v for v in mapping.values()}
    if g.lower() in by_value:
        return by_value[g.lower()]
    keys = {str(k).lower(): k for k in mapping}
    if g.lower() in keys:  # bảng kiểu {mã: tên} (Papago) → mã đã đúng
        return g
    want = _norm(_google_name(g))
    for key in keys:
        if _norm(key) == want:
            return mapping[keys[key]]
    for key in keys:  # 'Chinese' khớp 'chinese (simplified)'
        norm = _norm(key)
        if len(norm) > 2 and norm in want:
            return mapping[keys[key]]
    raise FatalProviderError(f"Engine {engine} không hỗ trợ ngôn ngữ {code} ({g})")


def _from_google(value: str) -> str:
    """Mã google ('zh-CN') → mã nội bộ ('zh'); không thấy thì trả về nguyên."""
    value = value.lower()
    for code, entry in LANGUAGES.items():
        if entry[2].lower() == value:
            return code
    return value


class DeepTranslator(MachineTranslator):
    """Máy dịch qua deep-translator: không ngữ cảnh, có glossary áp vào nguồn."""

    max_batch = 20

    _throttle_lock = threading.Lock()
    _throttle_last = 0.0

    def __init__(self, profile, model_override: str = "") -> None:
        super().__init__(profile, model_override)
        self.engine = (str(profile.option("engine", "")) or "google").strip().lower()
        if self.engine not in ENGINES:
            raise FatalProviderError(
                f"Engine không hợp lệ: {self.engine!r} — chọn một trong: "
                + ", ".join(sorted(ENGINES))
            )
        self._detected: str | None = None
        self._instances: dict[tuple[str, str], object] = {}

    # ------------------------------------------------------------------ helper

    def _throttle(self) -> None:
        if not ENGINES[self.engine][3]:
            return
        with DeepTranslator._throttle_lock:
            wait = FREE_MIN_INTERVAL - (time.time() - DeepTranslator._throttle_last)
            if wait > 0:
                time.sleep(wait)
            DeepTranslator._throttle_last = time.time()

    def _split_key(self, need: int) -> list[str]:
        raw = self.keys.next()
        if not raw:
            raise FatalProviderError(
                f"Engine {self.engine} cần API key — điền vào ô API key (mỗi dòng một key)"
            )
        if need == 1:
            return [raw]
        parts = raw.split(":", 1)
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise FatalProviderError(
                f"Engine {self.engine} cần hai giá trị, viết dạng `giá-trị-1:giá-trị-2` trong một dòng"
            )
        return [p.strip() for p in parts]

    def _build(self, source: str, target: str) -> object:
        lib = _lib()
        cls = getattr(lib, _CLASSES[self.engine])
        engine = self.engine
        if engine == "google":
            return cls(source=source, target=target)
        if engine == "mymemory":
            email = str(self.profile.option("email", "") or "").strip()
            return cls(source=source, target=target, email=email or None)
        if engine == "libre":
            (key,) = self._split_key(1)
            url = (self.profile.base_url or "").strip()
            return cls(
                source=source, target=target, api_key=key,
                custom_url=url or None, use_free_api=not url,
            )
        if engine == "deepl":
            (key,) = self._split_key(1)
            # key Free ends với ':fx', key Pro thì không
            return cls(source=source, target=target, api_key=key, use_free_api=key.endswith(":fx"))
        if engine == "microsoft":
            (key,) = self._split_key(1)
            region = (self.profile.region or "").strip()
            return cls(source=source, target=target, api_key=key, region=region or None)
        if engine == "yandex":
            (key,) = self._split_key(1)
            return cls(source=source, target=target, api_key=key)
        if engine == "papago":
            client_id, secret = self._split_key(2)
            return cls(source=source, target=target, client_id=client_id, secret_key=secret)
        appid, appkey = self._split_key(2)
        return cls(source=source, target=target, appid=appid, appkey=appkey)

    def _source(self, job: TranslateJob, sample: str) -> str:
        """Nguồn 'auto': engine nào không tự nhận thì để Google nhận giúp một lần."""
        code = job.source_lang or "auto"
        if code != "auto":
            return engine_lang(self.engine, code)
        if ENGINES[self.engine][2]:
            return "auto"
        if self._detected is None:
            lib = _lib()
            try:
                value = lib.single_detection(sample[:400], api="google")
            except Exception as exc:  # noqa: BLE001 - lỗi mạng/HTML của thư viện
                raise ProviderError(f"Không nhận diện được ngôn ngữ nguồn: {exc}") from exc
            self._detected = _from_google(str(value))
        return engine_lang(self.engine, self._detected)

    def _classify(self, exc: Exception) -> Exception:
        """Lỗi cấu hình/sai ngôn ngữ → báo ngay; còn lại → để `_retry` thử lại."""
        try:
            from deep_translator import exceptions as dte
        except ImportError:
            return ProviderError(f"{self.engine}: {type(exc).__name__}: {exc}")
        fatal = (
            dte.ApiKeyException, dte.AuthorizationException, dte.LanguageNotSupportedException,
            dte.InvalidSourceOrTargetLanguage, dte.NotValidPayload, dte.NotValidLength,
        )
        if isinstance(exc, fatal):
            return FatalProviderError(f"{self.engine}: {exc}")
        if isinstance(exc, dte.TooManyRequests):
            return ProviderError(f"{self.engine} 429: bị giới hạn tần suất — thử lại sau")
        if isinstance(exc, ProviderError):
            return exc
        return ProviderError(f"{self.engine}: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ dịch

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        out: list[str] = []
        source = self._source(job, texts[0] if texts else "")
        target = engine_lang(self.engine, job.target_lang)
        for text in texts:
            job.check_stop()
            instance = self._instances.get((source, target))
            if instance is None:
                instance = self._build(source, target)
                self._instances[(source, target)] = instance
            self._throttle()
            try:
                value = instance.translate(text)
            except StopRequested:
                raise
            except Exception as exc:  # noqa: BLE001 - mỗi engine một họ lỗi
                raise self._classify(exc) from exc
            out.append(str(value or "").strip())
        return out

    def _retry(self, func, job: TranslateJob):
        """Như của MachineTranslator nhưng lỗi Fatal thì bỏ ngay, không đợi thử lại."""
        last: Exception | None = None
        for attempt in range(4):
            job.check_stop()
            try:
                return func()
            except FatalProviderError:
                raise
            except (requests.RequestException, ProviderError, ValueError, KeyError, IndexError) as exc:
                last = exc
                nxt = FREE_FALLBACK.get(self.engine) if ("429" in str(exc) or "giới hạn tần suất" in str(exc)) else None
                if nxt:
                    job.emit(f"  {self.engine} bị chặn — chuyển sang engine {nxt} ngay")
                    self.engine = nxt
                    self._instances.clear()
                    continue  # thử lại ngay, không ngủ
                delay = retry_delay(attempt, exc)
                job.emit(f"  lỗi {type(exc).__name__}: {str(exc)[:200]} — thử lại sau {delay:.0f}s ({attempt + 1}/4)")
                time.sleep(delay)
        raise ProviderError(f"{self.profile.name}: {last}")
