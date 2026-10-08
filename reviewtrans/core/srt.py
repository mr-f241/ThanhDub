from __future__ import annotations

import re
from typing import Iterable

_TIME_RE = re.compile(
    r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)


def parse_srt(content: str) -> list[tuple[float, float, str]]:
    """Trả về danh sách (start, end, text). Chịu được file thiếu số thứ tự, CRLF, BOM."""
    content = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    items: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line for line in block.split("\n") if line.strip() != ""]
        for idx, line in enumerate(lines):
            match = _TIME_RE.search(line)
            if not match:
                continue
            g = match.groups()
            start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
            end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
            text = " ".join(part.strip() for part in lines[idx + 1:]).strip()
            items.append((start, end, text))
            break
    return items


def match_translation(items: list[tuple[float, float, str]], targets: list[tuple[float, float]]) -> list[int | None]:
    """Ghép từng dòng SRT đã dịch vào câu (start, end) của video.

    Ưu tiên dòng trùng thời gian với câu; file có đúng bằng số câu thì lấp chỗ
    còn trống theo thứ tự (phần mềm dịch thường giữ nguyên số dòng và mốc giờ).
    Trả về list index của `items` cho từng câu, None = không ghép được.
    """
    picks: list[int | None] = []
    for start, end in targets:
        best, best_overlap = None, 0.0
        for index, (item_start, item_end, _text) in enumerate(items):
            overlap = min(end, item_end) - max(start, item_start)
            if overlap > best_overlap:
                best, best_overlap = index, overlap
        picks.append(best)
    if len(items) == len(targets):
        for index, picked in enumerate(picks):
            if picked is None:
                picks[index] = index
    return picks


def format_timestamp(seconds: float, sep: str = ",") -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def format_clock(seconds: float) -> str:
    """Hiển thị gọn cho UI: MM:SS.s hoặc H:MM:SS.s"""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:04.1f}"
    return f"{minutes:02d}:{secs:04.1f}"


def render_srt(items: Iterable[tuple[float, float, str]]) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(items, start=1):
        blocks.append(f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}")
    return "\n\n".join(blocks) + "\n"
