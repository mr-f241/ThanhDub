"""Dò vùng chữ cứng (phụ đề gốc) trong một khung hình để căn vùng che vừa khớp.

Cách làm: chụp 1 khung bằng ffmpeg, tìm các cụm pixel sáng (chữ trắng/vàng) trong
vùng tìm kiếm (mặc định nửa dưới khung), rồi trả về hình chữ nhật bao quanh —
theo toạ độ chuẩn hoá 0..1 giống hệt Layer.x/y/w/h.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6 import QtGui

from .paths import find_tool
from .proc import run


# chữ trắng: sáng đều; chữ vàng: r,g cao, b thấp
def _is_text_pixel(r: int, g: int, b: int) -> bool:
    bright = (r + g + b) / 3.0
    if bright >= 200 and min(r, g, b) >= 130:
        return True  # trắng / xám sáng
    return r >= 200 and g >= 160 and b <= 150 and (r + g) >= 420  # vàng


def detect_text_region(
    video_path: str,
    at_seconds: float,
    search: tuple[float, float, float, float] = (0.0, 0.45, 1.0, 0.55),
    min_width: float = 0.05,
) -> tuple[float, float, float, float] | None:
    """Dò chữ trong `search` (x, y, w, h chuẩn hoá). Trả về (x, y, w, h) hoặc None."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg or not video_path or not Path(video_path).exists():
        return None
    with tempfile.TemporaryDirectory(prefix="thanhdub_fit_") as tmp:
        png = Path(tmp) / "frame.png"
        command = [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at_seconds):.3f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", "scale=640:-2", str(png),
        ]
        code = run(command, log=None)
        if code != 0 or not png.exists():
            return None
        image = QtGui.QImage(str(png))
        if image.isNull():
            return None
        return _scan(image, search, min_width)


def _scan(image: QtGui.QImage, search: tuple[float, float, float, float], min_width: float) -> tuple[float, float, float, float] | None:
    width, height = image.width(), image.height()
    if not width or not height:
        return None
    sx, sy, sw, sh = search
    x0, y0 = int(sx * width), int(sy * height)
    x1, y1 = min(width, x0 + int(sw * width)), min(height, y0 + int(sh * height))
    if x1 - x0 < 8 or y1 - y0 < 4:
        return None
    rows: list[int] = [0] * (y1 - y0)
    cols: list[int] = [0] * (x1 - x0)
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = image.pixel(x, y)
            r, g, b = (pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF
            if _is_text_pixel(r, g, b):
                rows[y - y0] += 1
                cols[x - x0] += 1
    row_top = _bounds(rows, min(3, max(1, (y1 - y0) // 60)))
    col_top = _bounds(cols, max(3, (x1 - x0) // 200))
    if row_top is None or col_top is None:
        return None
    ry0, ry1 = row_top
    cx0, cx1 = col_top
    if (cx1 - cx0) / width < min_width:
        return None  # nhiễu lấm tấm, không phải chữ
    return (
        (x0 + cx0) / width,
        (y0 + ry0) / height,
        (cx1 - cx0) / width,
        (ry1 - ry0) / height,
    )


def _bounds(counts: list[int], threshold: int) -> tuple[int, int] | None:
    """Khoảng chứa các vị trí có đếm vượt ngưỡng, cắt đuôi thưa."""
    active = [i for i, count in enumerate(counts) if count >= threshold]
    if not active:
        return None
    first, last = active[0], active[-1]
    peak = max(counts)
    while last > first and counts[last] < peak * 0.08:
        last -= 1
    while first < last and counts[first] < peak * 0.08:
        first += 1
    return first, last + 1
