"""Dịch qua OpenCode đang mở trên máy — không cần API key.

Zen API (`opencode.ai/zen/v1`) chặn gọi ngoài: free tier chỉ dùng được từ
trong OpenCode (403 FreeTierError). Thay vào đó nói chuyện với server local
mà OpenCode desktop/CLI để lại trong ``service.json``:

1. ``POST /api/session`` — tạo một session riêng cho app.
2. ``POST /api/session/{id}/prompt`` — warm-up (session mới chưa dùng được
   ``generate`` cho tới khi có ít nhất một message).
3. ``POST /api/session/{id}/generate`` — sinh text **đồng bộ** cho từng lô
   dịch (không cộng dồn lịch sử vào session).

Session được cache theo (url, danh sách mật khẩu thử được) trong tiến trình: hết
session hoặc OpenCode restart thì tự tạo lại.
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
_session_cache: dict[tuple[str, tuple[str, ...]], str] = {}

# generate đồng bộ đôi khi trả text rỗng (session còn bận / model chỉ sinh reasoning + tool)
GEN_ATTEMPTS = 3
REPLY_POLL_TRIES = 15


def service_paths() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/state/opencode/service.json",
        home / ".config/opencode/service.json",
        home / ".local/share/opencode/service.json",
    ]


def read_service() -> dict:
    """Đọc service.json của OpenCode (url + password + pid), file nào có thì lấy.

    File có ``url`` (server đang chạy ghi ra) thắng file chỉ có password còn sót
    từ bản cũ — tránh nuốt mất địa chỉ server mới bằng bản lỗi thời.
    """
    files: list[dict] = []
    for path in service_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            files.append({k: v for k, v in data.items() if v})
    merged: dict = {}
    for data in sorted(files, key=lambda item: bool(item.get("url"))):
        merged.update(data)
    return merged


def _message_done(message: dict) -> bool:
    """Message assistant đã sinh xong chưa (opencode chỉ ghi ``completed`` khi stream hết)."""
    if message.get("type") == "idle":
        return True
    if message.get("type") != "assistant":
        return False
    stamp = message.get("time")
    return not isinstance(stamp, dict) or bool(stamp.get("completed"))


def _message_text(message: dict) -> str:
    """Lấy text thật của message — reasoning/tool không tính; bản cũ không có ``content`` thì lấy ``text``."""
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return str(message.get("text") or "").strip()


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


class _Auth:
    """Mật khẩu Basic của OpenCode local, kèm các ứng viên dự phòng.

    Ô "API key" của kind ``opencode`` vốn để ghi mật khẩu local, nhưng người
    dùng hay dán nhầm key ``nvapi-…``/``sk-…`` vào đó → server trả 401. Giữ
    đủ danh sách ứng viên để ``_request`` tự thử sang mật khẩu từ service.json
    thay vì chết nguyên buổi dịch.
    """

    __slots__ = ("passwords", "ok")

    def __init__(self, passwords: tuple[str, ...]) -> None:
        self.passwords = passwords
        self.ok = passwords[0]

    def header(self) -> tuple[str, str]:
        return ("opencode", self.ok)

    @property
    def cache_key(self) -> tuple[str, ...]:
        return self.passwords


class OpenCodeLocalTranslator(LLMTranslator):
    """OpenCode local — model free của OpenCode qua chính server đang mở trên máy."""

    default_model = "big-pickle"
    max_batch = 40  # generate đồng bộ, lô nhỏ để không chờ quá lâu mỗi lượt

    # ---------------------------------------------------------------- kết nối
    def _server(self, key: str) -> tuple[str, _Auth]:
        service = read_service()
        base = (self.profile.base_url or str(service.get("url") or "")).rstrip("/")
        local = str(service.get("password") or "")
        candidates = tuple(dict.fromkeys(p for p in (key, local) if p))
        if not base:
            raise ProviderError(
                "Không thấy OpenCode đang chạy — mở app OpenCode (hoặc chạy `opencode serve`) "
                "trước khi dịch, hoặc điền Base URL thủ công."
            )
        if not candidates:
            raise ProviderError(
                "Thiếu mật khẩu local của OpenCode (service.json) — điền key thủ công vào ô bên trên."
            )
        return base, _Auth(candidates)

    def _request(self, method: str, url: str, auth: _Auth, timeout: float = 60, **kwargs):
        import requests

        def send(password: str):
            try:
                return requests.request(method, url, auth=("opencode", password), timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                raise ProviderError(
                    f"Không kết nối được OpenCode ({exc}) — chắc chắn app OpenCode đang mở."
                ) from exc

        resp = send(auth.ok)
        if resp.status_code == 401:
            # mật khẩu hiện dùng sai (key dán nhầm / server vừa xoay mật khẩu) → thử ứng viên còn lại
            for alt in auth.passwords:
                if alt == auth.ok:
                    continue
                alt_resp = send(alt)
                if alt_resp.status_code != 401:
                    auth.ok = alt  # ứng viên này mới đúng — dùng cho các lần sau
                    return alt_resp
                resp = alt_resp
        return resp

    def _ensure_session(self, base: str, auth: _Auth) -> str:
        cache_key = (base, auth.cache_key)
        with _session_lock:
            cached = _session_cache.get(cache_key)
            if cached:
                check = self._request("GET", f"{base}/api/session/{cached}", auth, timeout=15)
                if check.status_code == 200:
                    return cached
                _session_cache.pop(cache_key, None)

            create = self._request(
                "POST", f"{base}/api/session", auth,
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
                "POST", f"{base}/api/session/{sid}/prompt", auth,
                json={"text": "Ping. Reply with Pong."}, timeout=30,
            )
            if warm.status_code not in (200, 201):
                raise ProviderError(f"OpenCode warm-up lỗi (HTTP {warm.status_code}): {warm.text[:200]}")
            deadline = time.time() + 60
            ready = False
            while time.time() < deadline:
                msgs = self._request("GET", f"{base}/api/session/{sid}/message", auth, timeout=30)
                items = msgs.json().get("data") or []
                # chỉ coi là xong khi message đã có ``completed``/``idle`` — còn đang stream
                # mà generate thì server trả text rỗng (lỗi thật đã gặp)
                if any(_message_done(m) for m in items):
                    ready = True
                    break
                time.sleep(1)
            if not ready:
                raise ProviderError("OpenCode phản hồi quá chậm (warm-up hết 60s) — thử lại sau.")

            _session_cache[cache_key] = sid
            return sid

    def _model_provider(self, base: str, auth: _Auth, model: str) -> str:
        resp = self._request("GET", f"{base}/api/model", auth, timeout=30)
        if resp.status_code == 200:
            for item in resp.json().get("data") or []:
                if item.get("id") == model:
                    return str(item.get("providerID") or "opencode")
        return "opencode"

    def _wait_reply(self, base: str, auth: _Auth, sid: str, since_ms: int) -> str:
        """Generate trả rỗng nhưng câu trả lời có thể tới trễ — chờ message của chính lượt đó.

        Danh sách message mới đứng đầu, nên gặp message cũ hơn ``since_ms`` là
        hết lượt của mình, không còn gì để chờ nữa.
        """
        for _ in range(REPLY_POLL_TRIES):
            resp = self._request("GET", f"{base}/api/session/{sid}/message", auth, timeout=30)
            if resp.status_code == 200:
                for message in resp.json().get("data") or []:
                    if message.get("type") != "assistant":
                        continue
                    created = (message.get("time") or {}).get("created") or 0
                    if created and created < since_ms:
                        break  # đã lùi về message cũ → chưa có câu trả lời mới
                    if not _message_done(message):
                        break  # vẫn đang sinh → vòng sau kiểm tra lại
                    return _message_text(message)
            time.sleep(1)
        return ""

    # ---------------------------------------------------------------- dịch
    def _call(self, system: str, prompt: str, key: str) -> str:
        base, auth = self._server(key)
        sid = self._ensure_session(base, auth)
        if self.model:
            provider = self._model_provider(base, auth, self.model)
            chosen = self._request(
                "POST", f"{base}/api/session/{sid}/model", auth,
                json={"model": {"id": self.model, "providerID": provider}}, timeout=30,
            )
            if chosen.status_code not in (200, 204):
                # session hỏng / model không tồn tại → bỏ cache, lần retry sẽ tạo session mới
                self._drop_session(base, auth)
                raise ProviderError(
                    f"OpenCode không nhận model {self.model!r} (HTTP {chosen.status_code}): {chosen.text[:200]}"
                )
        body = {"prompt": f"{system}\n\n{prompt}"}
        text = ""
        # rỗng không phải lỗi cuối: model free hay sinh reasoning/tool không ra chữ,
        # hay session còn bận → chờ câu tới trễ rồi generate lại trong đúng session này
        for attempt in range(GEN_ATTEMPTS):
            started_ms = int(time.time() * 1000)
            resp = self._request(
                "POST", f"{base}/api/session/{sid}/generate", auth,
                json=body, timeout=300,
            )
            if resp.status_code != 200:
                self._drop_session(base, auth)
                raise ProviderError(f"OpenCode generate lỗi (HTTP {resp.status_code}): {resp.text[:200]}")
            payload = resp.json().get("data") or {}
            text = str(payload.get("text") or "").strip()
            if not text:
                # provider gói lỗi vào HTTP 200 kèm ``message`` (free tier bị chặn…) —
                # bỏ qua thông điệp này thì sẽ retry vô ích rồi báo "rỗng" mù mờ
                message = str(payload.get("message") or "").strip()
                if message:
                    self._drop_session(base, auth)
                    raise ProviderError(f"OpenCode từ chối: {message[:300]}")
                text = self._wait_reply(base, auth, sid, started_ms)
            if text:
                break
            if attempt + 1 < GEN_ATTEMPTS:
                time.sleep(2)
        if not text:
            self._drop_session(base, auth)
            raise ProviderError("OpenCode trả về nội dung rỗng — sẽ tạo session mới và thử lại.")
        return _VOICE_TAG.sub("", text)

    @staticmethod
    def _drop_session(base: str, auth: _Auth) -> None:
        with _session_lock:
            _session_cache.pop((base, auth.cache_key), None)

    def list_models(self) -> list[str]:
        base, auth = self._server(self.keys.current())
        resp = self._request("GET", f"{base}/api/model", auth, timeout=30)
        if resp.status_code != 200:
            raise ProviderError(f"OpenCode không lấy được danh sách model (HTTP {resp.status_code})")
        data = resp.json().get("data") or []
        return sorted({str(m.get("id")) for m in data if m.get("id")})
