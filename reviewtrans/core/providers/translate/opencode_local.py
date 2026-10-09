"""Dịch qua OpenCode đang mở trên máy — không cần API key.

Zen API (`opencode.ai/zen/v1`) chặn gọi ngoài: free tier chỉ dùng được từ
trong OpenCode (403 FreeTierError). Thay vào đó nói chuyện với server local
mà OpenCode desktop/CLI để lại trong ``service.json``:

1. ``POST /api/session`` — tạo một session riêng cho app.
2. ``POST /api/session/{id}/prompt`` — warm-up (session mới chưa dùng được
   ``generate`` cho tới khi có ít nhất một message).
3. ``POST /api/session/{id}/generate`` — sinh text **đồng bộ** cho từng lô
   dịch (không cộng dồn lịch sử vào session).

Session được cache theo (url, password) trong tiến trình: hết session hoặc
OpenCode restart thì tự tạo lại.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .. import ProviderError
from .base import LLMTranslator

# voice tag của AGENTS/skill người dùng hay bám vào đầu dòng — gỡ trước khi parse
_VOICE_TAG = re.compile(r"(?m)^\[T\]\s*")

_session_lock = threading.Lock()
_session_cache: dict[tuple[str, str], str] = {}


def service_paths() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/state/opencode/service.json",
        home / ".config/opencode/service.json",
        home / ".local/share/opencode/service.json",
    ]


def read_service() -> dict:
    """Đọc service.json của OpenCode (url + password + pid), file nào có thì lấy."""
    merged: dict = {}
    for path in service_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update({k: v for k, v in data.items() if v})
    return merged


def service_alive(service: dict) -> bool:
    """pid còn sống (hoặc không có pid mà có url) → coi như server đang mở."""
    pid = service.get("pid")
    if not pid:
        return bool(service.get("url"))
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


class OpenCodeLocalTranslator(LLMTranslator):
    """OpenCode local — model free của OpenCode qua chính server đang mở trên máy."""

    default_model = "big-pickle"
    max_batch = 40  # generate đồng bộ, lô nhỏ để không chờ quá lâu mỗi lượt

    # ---------------------------------------------------------------- kết nối
    def _server(self, key: str) -> tuple[str, str]:
        service = read_service()
        base = (self.profile.base_url or str(service.get("url") or "")).rstrip("/")
        password = key or str(service.get("password") or "")
        if not base:
            raise ProviderError(
                "Không thấy OpenCode đang chạy — mở app OpenCode (hoặc chạy `opencode serve`) "
                "trước khi dịch, hoặc điền Base URL thủ công."
            )
        if not password:
            raise ProviderError(
                "Thiếu mật khẩu local của OpenCode (service.json) — điền key thủ công vào ô bên trên."
            )
        return base, password

    def _request(self, method: str, url: str, password: str, timeout: float = 60, **kwargs):
        import requests

        try:
            resp = requests.request(method, url, auth=("opencode", password), timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"Không kết nối được OpenCode ({exc}) — chắc chắn app OpenCode đang mở.") from exc
        return resp

    def _ensure_session(self, base: str, password: str) -> str:
        cache_key = (base, password)
        with _session_lock:
            cached = _session_cache.get(cache_key)
            if cached:
                check = self._request("GET", f"{base}/api/session/{cached}", password, timeout=15)
                if check.status_code == 200:
                    return cached
                _session_cache.pop(cache_key, None)

            create = self._request(
                "POST", f"{base}/api/session", password,
                json={"title": "ThanhDub dịch"}, timeout=30,
            )
            if create.status_code not in (200, 201):
                raise ProviderError(f"OpenCode tạo session lỗi (HTTP {create.status_code}): {create.text[:200]}")
            payload = create.json()
            sid = payload.get("id") or (payload.get("data") or {}).get("id") or ""
            if not sid:
                raise ProviderError(f"OpenCode không trả về session id: {create.text[:200]}")

            # warm-up: generate trên session trống trả text rỗng → cần một message trước
            warm = self._request(
                "POST", f"{base}/api/session/{sid}/prompt", password,
                json={"text": "Ping. Reply with Pong."}, timeout=30,
            )
            if warm.status_code not in (200, 201):
                raise ProviderError(f"OpenCode warm-up lỗi (HTTP {warm.status_code}): {warm.text[:200]}")
            deadline = time.time() + 60
            ready = False
            while time.time() < deadline:
                msgs = self._request("GET", f"{base}/api/session/{sid}/message", password, timeout=30)
                items = msgs.json().get("data") or []
                if any(m.get("type") == "assistant" for m in items):
                    ready = True
                    break
                time.sleep(1)
            if not ready:
                raise ProviderError("OpenCode phản hồi quá chậm (warm-up hết 60s) — thử lại sau.")

            _session_cache[cache_key] = sid
            return sid

    def _model_provider(self, base: str, password: str, model: str) -> str:
        resp = self._request("GET", f"{base}/api/model", password, timeout=30)
        if resp.status_code == 200:
            for item in resp.json().get("data") or []:
                if item.get("id") == model:
                    return str(item.get("providerID") or "opencode")
        return "opencode"

    # ---------------------------------------------------------------- dịch
    def _call(self, system: str, prompt: str, key: str) -> str:
        base, password = self._server(key)
        sid = self._ensure_session(base, password)
        if self.model:
            provider = self._model_provider(base, password, self.model)
            chosen = self._request(
                "POST", f"{base}/api/session/{sid}/model", password,
                json={"model": {"id": self.model, "providerID": provider}}, timeout=30,
            )
            if chosen.status_code not in (200, 204):
                # session hỏng / model không tồn tại → bỏ cache, lần retry sẽ tạo session mới
                with _session_lock:
                    _session_cache.pop((base, password), None)
                raise ProviderError(
                    f"OpenCode không nhận model {self.model!r} (HTTP {chosen.status_code}): {chosen.text[:200]}"
                )
        resp = self._request(
            "POST", f"{base}/api/session/{sid}/generate", password,
            json={"prompt": f"{system}\n\n{prompt}"}, timeout=300,
        )
        if resp.status_code != 200:
            with _session_lock:
                _session_cache.pop((base, password), None)
            raise ProviderError(f"OpenCode generate lỗi (HTTP {resp.status_code}): {resp.text[:200]}")
        text = str(((resp.json().get("data") or {}).get("text")) or "").strip()
        if not text:
            with _session_lock:
                _session_cache.pop((base, password), None)
            raise ProviderError("OpenCode trả về nội dung rỗng — sẽ tạo session mới và thử lại.")
        return _VOICE_TAG.sub("", text)

    def list_models(self) -> list[str]:
        base, password = self._server(self.keys.current())
        resp = self._request("GET", f"{base}/api/model", password, timeout=30)
        if resp.status_code != 200:
            raise ProviderError(f"OpenCode không lấy được danh sách model (HTTP {resp.status_code})")
        data = resp.json().get("data") or []
        return sorted({str(m.get("id")) for m in data if m.get("id")})
