"""Kiểm tra bản mới từ GitHub Releases của ThanhDub.

Chỉ đọc API công khai của GitHub, không gửi thông tin gì lên. Khi có bản mới,
hiện hộp thoại với ghi chú phát hành và nút mở trang tải.
"""
from __future__ import annotations

import re

import requests

from ..core.pipeline.resources import APP_REPO

API_URL = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
TIMEOUT = 10


def version_tuple(text: str) -> tuple[int, ...]:
    """'v2.3.1-beta' → (2, 3, 1); phần không phải số bị bỏ qua."""
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return version_tuple(latest) > version_tuple(current)


def fetch_latest(timeout: int = TIMEOUT) -> dict | None:
    """Thông tin bản mới nhất: {version, url, notes, asset} hoặc None nếu lỗi/không có."""
    try:
        response = requests.get(
            API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ThanhDub"},
            timeout=timeout,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("tag_name"):
        return None
    asset = next(
        (a for a in data.get("assets", []) if str(a.get("name", "")).endswith((".exe", ".zip"))),
        None,
    )
    return {
        "version": str(data["tag_name"]).lstrip("v"),
        "url": data.get("html_url") or f"https://github.com/{APP_REPO}/releases/latest",
        "notes": str(data.get("body") or "")[:2000],
        "asset": (asset or {}).get("browser_download_url", ""),
    }
