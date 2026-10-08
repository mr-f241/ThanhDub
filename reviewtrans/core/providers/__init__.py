from __future__ import annotations

import threading


class ProviderError(RuntimeError):
    pass


class FatalProviderError(ProviderError):
    """Lỗi cấu hình (thiếu key, sai engine, unsupported ngôn ngữ) — thử lại cũng vậy, báo ngay."""


class NoAudioError(ProviderError):
    """Edge trả về audio rỗng — thường do bị Microsoft limit tạm, phải nghỉ ~20s mới qua."""


def retry_delay(attempt: int, exc: Exception) -> float:
    """Đợi giữa hai lần thử lại: 429/quota thì chờ lâu hẳn, lỗi thường thì thử lại nhanh."""
    text = str(exc).lower()
    rate_limited = any(
        token in text
        for token in ("429", "too many requests", "rate limit", "quota", "overloaded", "resource has been exhausted")
    )
    base = 5.0 if rate_limited else 1.5
    return min(60.0, base * (2 ** attempt))


class KeyRing:
    """Xoay vòng nhiều API key (tránh giới hạn quota)."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = [k.strip() for k in keys if k and k.strip()]
        self._index = 0
        self._lock = threading.Lock()

    def __bool__(self) -> bool:
        return bool(self.keys)

    def __len__(self) -> int:
        return len(self.keys)

    def current(self) -> str:
        with self._lock:
            return self.keys[self._index % len(self.keys)] if self.keys else ""

    def next(self) -> str:
        with self._lock:
            if not self.keys:
                return ""
            key = self.keys[self._index % len(self.keys)]
            self._index += 1
            return key
