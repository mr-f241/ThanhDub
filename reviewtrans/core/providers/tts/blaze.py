"""Provider TTS Blaze (api.blaze.vn): pool token xoay vòng + kho giọng nghe thử.

Mỗi token có hai hạn mức riêng (100 request / 10 phút, 600 request / giờ) và 20
request đồng thời. Token hết hạn mức tự nghỉ, token trả 401 bị đánh dấu chết,
429 thì nghỉ đúng số giây còn lại của cửa sổ. Bộ đếm lưu xuống JSON nên mở lại
app vẫn nhớ token nào vừa dùng bao nhiêu.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from ...loudness import normalize
from ...paths import user_data_dir
from ...proc import StopRequested
from .. import FatalProviderError, ProviderError

PUBLIC_API = "https://api.blaze.vn"
GATEWAY = "https://gateway.blaze.vn"

TTS_MODELS = ("v2.0_pro", "v2.0_flash", "v1.5_pro", "v1.5_flash")
AUDIO_FORMATS = ("mp3", "wav", "opus")
NORMALIZATIONS = ("no", "basic", "advanced")
LANGUAGES = ("vi", "en")
DEFAULT_MODEL = "v2.0_pro"
DEFAULT_SPEAKER = "HN-Nam-1-BL"

# (request / 10 phút, request / giờ, request đồng thời)
LIMIT_PER_10M = 100
LIMIT_PER_HOUR = 600
LIMIT_CONCURRENT = 20

WINDOW_10M = 600.0
WINDOW_HOUR = 3600.0
COOLDOWN_10M = 600.0

HTTP_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 180

PREVIEW_TEXT = (
    "Xin chào! Đây là giọng đọc thử. "
    "Chọn giọng này nếu bạn thấy âm thanh vừa ý."
)


def with_ext(base: Path, ext: str) -> Path:
    """Gắn đuôi ``ext`` vào ``base``.

    Không dùng ``Path.with_suffix`` vì tên file của ta chứa dấu chấm sẵn (mã model
    ``v2.0_pro``, hash ``.mp3``…) nên ``with_suffix`` cắt nhầm mất phần đuôi.
    """
    stem = str(base)[: -len(base.suffix)] if base.suffix in AUDIO_FORMATS else str(base)
    return Path(f"{stem}.{ext}")


def without_ext(path: Path) -> Path:
    """Bỏ đuôi âm thanh, theo đúng danh sách :data:`AUDIO_FORMATS`."""
    text = str(path)
    for ext in AUDIO_FORMATS:
        if text.endswith(f".{ext}"):
            return Path(text[: -len(ext) - 1])
    return path


# --------------------------------------------------------------------------- pool token


@dataclass(frozen=True)
class VoiceRow:
    """Một giọng trong danh mục Blaze. ``language`` để lọc theo ngôn ngữ đích."""

    voice_id: str
    name: str
    language: str = ""
    gender: str = ""
    province: str = ""
    kind: str = ""

    @property
    def label(self) -> str:
        tags = [t for t in (self.gender, self.province, self.kind) if t]
        detail = " · ".join(tags)
        return f"{self.name} — {self.voice_id}" + (f" · {detail}" if detail else "")

    @property
    def pair(self) -> tuple[str, str]:
        return (self.voice_id, self.label)

    def to_dict(self) -> dict[str, str]:
        return {
            "voice_id": self.voice_id, "name": self.name, "language": self.language,
            "gender": self.gender, "province": self.province, "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceRow":
        return cls(
            voice_id=str(data.get("voice_id", "")),
            name=str(data.get("name", "")),
            language=str(data.get("language", "")),
            gender=str(data.get("gender", "")),
            province=str(data.get("province", "")),
            kind=str(data.get("kind", "")),
        )


@dataclass
class TokenState:
    token: str
    enabled: bool = True
    dead: bool = False
    fail_count: int = 0
    last_error: str = ""
    cooldown_until: float = 0.0
    used_10m: list[float] = field(default_factory=list)  # mốc thời gian request gần đây
    used_hour: list[float] = field(default_factory=list)
    inflight: int = 0
    calls: int = 0  # tổng số request đã cấp (để hiển thị)

    @property
    def short(self) -> str:
        return f"{self.token[:6]}…{self.token[-4:]}" if len(self.token) > 12 else self.token

    def _trim(self, now: float) -> None:
        self.used_10m = [t for t in self.used_10m if now - t < WINDOW_10M]
        self.used_hour = [t for t in self.used_hour if now - t < WINDOW_HOUR]

    def window_state(self, now: float) -> str:
        if self.dead:
            return "dead"
        if not self.enabled:
            return "off"
        if self.cooldown_until > now:
            return "cooling"
        self._trim(now)
        if len(self.used_10m) >= LIMIT_PER_10M or len(self.used_hour) >= LIMIT_PER_HOUR:
            return "full"
        return "ready"

    def to_dict(self) -> dict[str, Any]:
        now = time.time()
        self._trim(now)
        return {
            "token": self.token,
            "short": self.short,
            "enabled": self.enabled,
            "dead": self.dead,
            "fail_count": self.fail_count,
            "last_error": self.last_error,
            "used_10m": len(self.used_10m),
            "used_hour": len(self.used_hour),
            "inflight": self.inflight,
            "calls": self.calls,
            "cooldown": round(max(0.0, self.cooldown_until - now), 1),
            "state": self.window_state(now),
        }


# lỗi thuộc về TÀI KHOẢN Blaze (không phải một token hỏng) — xoay token khác không cứu được
_ACCOUNT_ERRORS = ("banned", "suspended", "disabled", "vi phạm", "khóa tài khoản", "khong duoc phep")


def error_detail(response: Any) -> str:
    """Đọc message server trả kèm 401/403.

    Blaze trả ``{"detail": {"message": "Your account has been banned", ...}}`` — trước đây
    phần này bị vứt đi nên người dùng chỉ thấy "token bị từ chối (401)" rồi đinh ninh token hỏng.
    """
    try:
        data = response.json()
    except ValueError:
        return str(getattr(response, "text", "") or "").strip()[:160]
    if not isinstance(data, dict):
        return "" if data is None else str(data)[:160]
    detail: Any = data.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("msg")
    elif isinstance(detail, list) and detail:
        first = detail[0]
        detail = first.get("msg") if isinstance(first, dict) else first
    if not detail:
        detail = data.get("message") or data.get("error") or data.get("msg")
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail)[:160] if detail else ""


def _is_account_error(detail: str) -> bool:
    text = (detail or "").lower()
    return any(word in text for word in _ACCOUNT_ERRORS)


class BlazeKeyPool:
    """Xoay vòng token theo quota cửa sổ trượt, lưu bộ đếm xuống đĩa."""

    def __init__(self, keys: list[str], state_file: Path | None = None) -> None:
        self.path = state_file or (user_data_dir() / "blaze_pool.json")
        self._lock = threading.Lock()
        self._rr = itertools.count()
        self._tokens: dict[str, TokenState] = {}
        for key in keys:
            token = key.strip()
            if token:
                self._tokens[token] = TokenState(token)
        self._load()

    # ---------- trạng thái ----------
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        now = time.time()
        for row in data.get("tokens", []) if isinstance(data, dict) else []:
            token = str(row.get("token", ""))
            state = self._tokens.get(token)
            if state is None:
                continue  # token không còn trong profile → bỏ qua
            state.enabled = bool(row.get("enabled", True))
            state.dead = bool(row.get("dead", False))
            state.fail_count = int(row.get("fail_count", 0))
            state.last_error = str(row.get("last_error", ""))
            state.calls = int(row.get("calls", 0))
            state.used_10m = [float(t) for t in row.get("used_10m", []) if now - float(t) < WINDOW_10M]
            state.used_hour = [float(t) for t in row.get("used_hour", []) if now - float(t) < WINDOW_HOUR]
            cooldown = float(row.get("cooldown_until", 0.0))
            state.cooldown_until = cooldown if cooldown > now else 0.0

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"tokens": [s.to_dict() | {"used_10m": s.used_10m, "used_hour": s.used_hour,
                                                 "cooldown_until": s.cooldown_until}
                                  for s in self._tokens.values()]}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # mất bộ đếm cũngng không chặn việc đọc TTS

    def keys(self) -> list[str]:
        return list(self._tokens)

    def current(self) -> str:
        """Token sẵn sàng để gọi API không cần tính quota (đọc danh sách giọng, xem trạng thái)."""
        now = time.time()
        ready = [s.token for s in self._tokens.values() if s.window_state(now) == "ready"]
        return ready[0] if ready else (next(iter(self._tokens), ""))

    def summary(self) -> dict[str, Any]:
        now = time.time()
        rows = [s.window_state(now) for s in self._tokens.values()]
        return {
            "total": len(rows),
            "ready": rows.count("ready"),
            "cooling": rows.count("cooling"),
            "full": rows.count("full"),
            "dead": rows.count("dead"),
            "off": rows.count("off"),
            "inflight": sum(s.inflight for s in self._tokens.values()),
            "limit_10m": LIMIT_PER_10M,
            "limit_hour": LIMIT_PER_HOUR,
        }

    def snapshot(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._tokens.values()]

    def reset_dead(self) -> int:
        """Bật lại token bị đánh dấu chết. Trả về số token được bật."""
        count = 0
        with self._lock:
            for state in self._tokens.values():
                if state.dead:
                    state.dead = False
                    state.fail_count = 0
                    state.last_error = ""
                    count += 1
            self._save()
        return count

    def clear_history(self) -> None:
        with self._lock:
            for state in self._tokens.values():
                state.used_10m.clear()
                state.used_hour.clear()
                state.cooldown_until = 0.0
            self._save()

    # ---------- cấp token ----------
    def candidates(self) -> list[TokenState]:
        now = time.time()
        ready = [s for s in self._tokens.values() if s.window_state(now) == "ready"]
        return sorted(ready, key=lambda s: (len(s.used_hour), len(s.used_10m)))

    def acquire(self) -> TokenState:
        pool = self.candidates()
        if not pool:
            raise ProviderError(self._no_token_reason())
        with self._lock:
            # ưu tiên token ít dùng nhất; khi bằng nhau thì xoay vòng theo thứ tự cấp
            lightest = min(len(s.used_hour) for s in pool)
            tier = [s for s in pool if len(s.used_hour) == lightest]
            state = tier[next(self._rr) % len(tier)]
            if state.inflight >= LIMIT_CONCURRENT:
                # token nhẹ nhất đã kín → lấy token khác còn chỗ, bận ít nhất
                spare = [s for s in pool if s.inflight < LIMIT_CONCURRENT]
                if spare:
                    state = min(spare, key=lambda s: s.inflight)
            now = time.time()
            state.used_10m.append(now)
            state.used_hour.append(now)
            state.inflight += 1
            state.calls += 1
        return state

    def report(self, state: TokenState, *, ok: bool, status: int | None = None, error: str = "") -> None:
        with self._lock:
            state.inflight = max(0, state.inflight - 1)
            if ok:
                state.cooldown_until = 0.0
            elif status == 429:
                state.cooldown_until = time.time() + COOLDOWN_10M
                state.last_error = "hết quota"
            elif status in (401, 403):
                state.dead = True
                state.last_error = f"token bị từ chối ({status})" + (f": {error[:140]}" if error else "")
            elif status is not None:
                state.fail_count += 1
                state.last_error = error[:120]
            self._save()

    def _no_token_reason(self) -> str:
        now = time.time()
        if not self._tokens:
            return "chưa có token Blaze nào — dán token vào ô API key (mỗi dòng một token)"
        states = [s.window_state(now) for s in self._tokens.values()]
        if all(s == "dead" for s in states):
            # nêu đúng lý do server trả về (vd. "Your account has been banned") thay vì hardcode 401
            details = sorted({s.last_error.strip() for s in self._tokens.values() if s.last_error and s.last_error.strip()})
            why = "; ".join(details[:2]) + (" …" if len(details) > 2 else "") if details else "401"
            return f"mọi token đều bị từ chối — {why}. Lấy token mới hoặc kiểm tra tài khoản Blaze."
        if all(s in ("dead", "off") for s in states):
            return "mọi token đều bị tắt hoặc đã chết"
        cooling = [s for s in self._tokens.values() if s.window_state(now) == "cooling"]
        if cooling:
            wait = min(s.cooldown_until for s in cooling) - now
            return f"mọi token đang nghỉ vì hết quota, sẵn sàng sau {max(0, wait):.0f}s"
        if any(s == "full" for s in states):
            return "mọi token đã dùng hết quota cửa sổ, thử lại sau ít phút"
        return "mọi token đều bận, thử lại sau"


# --------------------------------------------------------------------------- provider


class BlazeTTS:
    """TTS Blaze — dùng chung giao diện với các provider khác của pipeline."""

    default_voice = DEFAULT_SPEAKER

    def __init__(self, profile, *, pool: BlazeKeyPool | None = None) -> None:
        self.profile = profile
        self.pool = pool or BlazeKeyPool(list(profile.api_keys))

    # ---------- cấu hình ----------
    @property
    def base(self) -> str:
        return (self.profile.base_url or PUBLIC_API).rstrip("/")

    @property
    def gateway(self) -> str:
        return str(self.profile.option("gateway", GATEWAY)).rstrip("/")

    @property
    def voice(self) -> str:
        return str(self.profile.option("voice", "") or self.default_voice)

    def _option(self, key: str, default: str = "") -> str:
        return str(self.profile.option(key, default) or default)

    def _model(self) -> str:
        model = self.profile.model or self._option("model", DEFAULT_MODEL)
        return model if model in TTS_MODELS else DEFAULT_MODEL

    def _format(self) -> str:
        fmt = self._option("format", "mp3").lower()
        return fmt if fmt in AUDIO_FORMATS else "mp3"

    def _quality(self) -> int:
        try:
            return max(1, min(100, int(self._option("quality", "64"))))
        except (TypeError, ValueError):
            return 64

    def _language(self) -> str:
        language = self._option("language", "vi").lower()
        return language if language in LANGUAGES else "vi"

    # ---------- gọi API ----------
    def _request(
        self,
        method: str,
        path: str,
        state: TokenState,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        stream: bool = False,
        timeout: int = HTTP_TIMEOUT,
        base: str = "",
    ) -> requests.Response:
        url = f"{base or self.base}{path}"
        headers = {"Authorization": f"Bearer {state.token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = requests.request(
                method, url, json=json_body, params=params, headers=headers,
                stream=stream, timeout=timeout,
            )
        except requests.RequestException as exc:
            self.pool.report(state, ok=False, error=str(exc))
            raise ProviderError(f"Lỗi mạng: {exc}") from exc
        if response.status_code in (401, 403):
            status = response.status_code
            detail = error_detail(response)
            self.pool.report(state, ok=False, status=status, error=detail)
            if status == 403 and _is_account_error(detail):
                # 403 do TÀI KHOẢN (vd. "Your account has been banned"): mọi token đều thuộc
                # cùng một tài khoản → xoay vòng vô ích, đốt hết pool rồi người dùng chỉ thấy
                # "401" mơ hồ. Báo ngay đúng nguyên nhân.
                raise FatalProviderError(f"Blaze từ chối tài khoản (403): {detail}")
            raise _RetryToken(f"token bị từ chối ({status})" + (f": {detail}" if detail else ""))
        if response.status_code == 429:
            self.pool.report(state, ok=False, status=429, error="hết quota")
            raise _RetryToken("đã vượt quota")
        return response

    def _call(self, work, *, attempts: int = 3):
        """Gọi ``work(state)`` với token khác nhau mỗi lần thử lại (401/429/5xx)."""
        last: Exception | None = None
        for _ in range(max(1, attempts)):
            state = self.pool.acquire()
            try:
                result = work(state)
            except _RetryToken as exc:
                last = exc
                continue
            except Exception as exc:  # noqa: BLE001
                self.pool.report(state, ok=False, status=getattr(exc, "status", None), error=str(exc))
                raise
            self.pool.report(state, ok=True, status=getattr(result, "status_code", 200))
            return result
        raise ProviderError(str(last) if last else "không token nào dùng được")

    # ---------- TTS ----------
    def _create_job(self, state: TokenState, payload: dict) -> dict:
        response = self._request("POST", "/v1/tts", state, json_body=payload)
        if response.status_code >= 400:
            raise ProviderError(f"Blaze từ chối ({response.status_code}): {response.text[:220]}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("Blaze trả về dữ liệu không đọc được") from exc

    def _job_info(self, state: TokenState, tts_id: str) -> dict:
        response = self._request("GET", f"/v1/tts/{tts_id}/info", state)
        if response.status_code >= 400:
            raise ProviderError(f"Không đọc được trạng thái job ({response.status_code})")
        return response.json()

    def synthesize(
        self,
        text: str,
        voice: str,
        speed: float,
        out_base: Path,
        stop_event: threading.Event | None = None,
    ) -> Path:
        body = text.strip()
        if not body:
            raise ProviderError("Nội dung cần đọc rỗng")
        speaker = (voice or self.voice or DEFAULT_SPEAKER).strip()
        payload = {
            "query": body,
            "speaker_id": speaker,
            "language": self._language(),
            "model": self._model(),
            "audio_format": self._format(),
            "audio_quality": self._quality(),
            "audio_speed": f"{max(0.5, min(2.0, float(speed))):.2f}",
            "normalization": self._option("normalization", "basic") if
                self._option("normalization", "basic") in NORMALIZATIONS else "basic",
        }
        target = with_ext(out_base, self._format())
        timeout_s = float(self._option("timeout", "120") or 120)
        deadline = time.time() + timeout_s

        def work(state: TokenState) -> Path:
            job = self._create_job(state, payload)
            tts_id = str(job.get("id") or "")
            if not tts_id:
                raise ProviderError("Blaze không trả về mã job")
            info = job
            status = str(job.get("status") or "processing")
            while status not in ("completed", "failed", "error"):
                if stop_event is not None and stop_event.is_set():
                    raise StopRequested()
                if time.time() > deadline:
                    raise ProviderError(f"Blaze chưa xong sau {timeout_s:.0f}s (đang {status})")
                time.sleep(1.2)
                info = self._job_info(state, tts_id)
                status = str(info.get("status") or "")
            if status != "completed":
                raise ProviderError(f"Blaze báo lỗi khi tạo âm thanh ({status})")
            return self._download(state, tts_id, target)

        result = self._call(work)
        if self._option("boost", "on") != "off":
            normalize(result)
        return result

    def _download(self, state: TokenState, tts_id: str, target: Path) -> Path:
        response = self._request(
            "GET", f"/v1/tts/{tts_id}/download", state, stream=True, timeout=DOWNLOAD_TIMEOUT
        )
        if response.status_code >= 400:
            raise ProviderError(f"Tải audio thất bại ({response.status_code})")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(target, "wb") as handle:
            for chunk in response.iter_content(65536):
                if chunk:
                    handle.write(chunk)
                    written += len(chunk)
        if written == 0:
            raise ProviderError("Blaze trả về file rỗng")
        return target

    # ---------- danh sách giọng ----------
    def _voice_cache(self) -> Path:
        return user_data_dir() / "blaze_voices.json"

    def _voices_from_disk(self, max_age: float = 12 * 3600) -> list[VoiceRow] | None:
        path = self._voice_cache()
        if not path.is_file():
            return None
        try:
            if time.time() - path.stat().st_mtime > max_age:
                return None
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        out = [VoiceRow.from_dict(r) for r in rows if isinstance(r, dict)]
        return out or None

    def _save_voices(self, rows: list[VoiceRow]) -> None:
        try:
            path = self._voice_cache()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([r.to_dict() for r in rows], ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def list_voices(self, language: str = "") -> list[tuple[str, str]]:
        catalog = self._catalog()
        return [item.pair for item in self._filter(catalog, language)]

    def _extra_voices(self) -> list[tuple[str, str]]:
        raw = self.profile.option("voices", "")
        return [(v.strip(), v.strip()) for v in str(raw).split(",") if v.strip()]

    def _catalog(self) -> list[VoiceRow]:
        """Danh sách giọng đầy đủ (kèm ngôn ngữ để lọc), cache 12 giờ trên đĩa."""
        cached = self._voices_from_disk()
        if cached is not None:
            return cached
        rows = self._fetch_voices() + [VoiceRow(v, l) for v, l in self._extra_voices()]
        self._save_voices(rows)
        return rows

    @staticmethod
    def _filter(rows: list[VoiceRow], language: str) -> list[VoiceRow]:
        wanted = (language or "").strip().lower()
        if not wanted:
            return rows
        matched = [r for r in rows if wanted in r.language.lower() or wanted in r.voice_id.lower()]
        return matched or rows  # không có giọng đúng ngôn ngữ thì vẫn cho danh sách đầy đủ

    def _fetch_voices(self) -> list[VoiceRow]:
        def work(state: TokenState) -> list[VoiceRow]:
            response = self._request("GET", "/tts/options", state, base=self.gateway)
            if response.status_code >= 400:
                raise ProviderError(f"Không lấy được danh sách giọng ({response.status_code})")
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError("Danh sách giọng không đọc được") from exc
            return self._parse_voices(body)

        try:
            return self._call(work)
        except (ProviderError, ValueError):
            return []

    @staticmethod
    def _parse_voices(body: Any) -> list[VoiceRow]:
        speakers = body.get("speakers", []) if isinstance(body, dict) else body
        out: list[VoiceRow] = []
        seen: set[str] = set()
        for row in speakers if isinstance(speakers, list) else []:
            if not isinstance(row, dict):
                continue
            voice_id = str(row.get("id") or row.get("speaker_id") or row.get("voice_id") or "").strip()
            if not voice_id or voice_id in seen:
                continue
            seen.add(voice_id)
            out.append(VoiceRow(
                voice_id=voice_id,
                name=str(row.get("name") or voice_id),
                language=str(row.get("language") or ""),
                gender=str(row.get("gender") or ""),
                province=str(row.get("province") or ""),
                kind=str(row.get("type") or row.get("kind") or ""),
            ))
        return out

    # ---------- nghe thử ----------
    def samples_dir(self) -> Path:
        path = user_data_dir() / "blaze_samples"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def preview_path(self, voice_id: str, model: str = "", text: str = "") -> Path:
        voice = self._voice_id(voice_id, self.voice)
        body = text.strip()
        # hash ổn định giữa các lần chạy app, nên mẫu cũ vẫn được dùng lại
        digest = hashlib.md5(body.encode("utf-8")).hexdigest()[:8] if body else "default"
        return self.samples_dir() / f"{voice}__{model or self._model()}__{digest}.mp3"

    def preview(self, voice_id: str, text: str = "", model: str = "") -> Path:
        """Trả về file âm thanh để nghe thử giọng (có cache, không tốn quota lần hai)."""
        voice = self._voice_id(voice_id, self.voice)
        target = self.preview_path(voice, model, text)
        if target.exists() and target.stat().st_size > 0:
            return target
        return self.synthesize(text.strip() or PREVIEW_TEXT, voice, 1.0, without_ext(target))

    @staticmethod
    def _voice_id(raw: str, fallback: str = "") -> str:
        """Chấp nhận cả mã giọng lẫn nhãn đầy đủ kiểu “Tên — Mã · chi tiết”.

        Nhãn do :attr:`VoiceRow.label` dựng ra có tối đa ba phần: tên hiển thị,
        mã giọng, rồi chi tiết. API chỉ nhận mã giọng, nên mã phải thắng tên.
        """
        parts: list[str] = []
        for candidate in (raw, fallback):  # thử nhãn trước, mã giọng sau
            body = (candidate or "").split("·")[0]  # cắt đuôi chi tiết “Nu · HN”
            chunk = [p.strip() for p in body.split("—") if p.strip()]
            if not chunk:
                continue
            parts = chunk if len(chunk) == 1 else [chunk[1]]  # nhãn đầy đủ → mã giọng
            break
        return parts[0] if parts else ""

    # ---------- kiểm tra ----------
    def test(self, out_base: Path) -> Path:
        return self.synthesize("Xin chào, đây là giọng đọc thử.", self.voice, 1.0, out_base)


class _RetryToken(Exception):
    """Token vừa dùng không ổn (401/429) — thử token khác."""