"""Đo và chuẩn hoá độ lớn audio bằng ffmpeg.

Một số API TTS trả về file ở khoảng -21 LUFS nên nghe như nói thầm. Ở đây ta đo
loudness rồi nâng về mức nghe dễ chịu, có limiter để không vỡ tiếng. Không có
ffmpeg thì bỏ qua, không làm hỏng gì.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .paths import find_tool
from .proc import subprocess_kwargs

TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0

_MEASURE_KEYS = (("input_i", "i"), ("input_tp", "tp"), ("input_lra", "lra"))
_num = re.compile(r"-?\d+(?:\.\d+)?")

_cache: dict[tuple[str, int], dict[str, float]] = {}


def ffmpeg_path() -> Path | None:
    return find_tool("ffmpeg")


def measure(path: str | Path) -> dict[str, float]:
    """Đo integrated loudness (LUFS) và true peak (dBFS). Không đo được trả về 0."""
    target = Path(path)
    key = (str(target.resolve()), target.stat().st_size if target.exists() else 0)
    if key in _cache:
        return dict(_cache[key])
    out = {"i": 0.0, "tp": 0.0, "lra": 0.0}
    ffmpeg = ffmpeg_path()
    if not ffmpeg or not target.exists() or target.stat().st_size == 0:
        return out
    try:
        result = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-nostats", "-i", str(target), "-af",
                f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=180, **subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return out
    blob = result.stderr or ""
    start, end = blob.rfind("{"), blob.rfind("}")
    if start < 0 or end <= start:
        return out
    try:
        data = json.loads(blob[start:end + 1])
    except json.JSONDecodeError:
        return out
    for raw_key, short in _MEASURE_KEYS:
        raw = data.get(raw_key)
        if raw not in (None, "-inf", "inf"):
            found = _num.search(str(raw))
            if found:
                out[short] = float(found.group())
    _cache[key] = dict(out)
    return dict(out)


def needed_gain_db(path: str | Path) -> float:
    """Bao nhiêu dB cần thêm để lên TARGET_LUFS. 0 nghĩa là không đo được hoặc đã đủ to."""
    measured = measure(path)["i"]
    if measured == 0.0:
        return 0.0
    return max(0.0, round(TARGET_LUFS - measured, 1))


def normalize(
    path: str | Path,
    *,
    target_lufs: float = TARGET_LUFS,
    target_tp: float = TARGET_TP,
    min_gain_db: float = 1.0,
) -> float:
    """Nâng độ lớn của file tại chỗ, giữ nhịp điệu. Trả về gain đã cộng (0 = không can thiệp)."""
    target = Path(path)
    ffmpeg = ffmpeg_path()
    if not ffmpeg or not target.exists() or target.stat().st_size == 0:
        return 0.0
    before = measure(target)
    if before["i"] == 0.0:
        return 0.0
    gain = round(target_lufs - before["i"], 1)
    if gain < min_gain_db:
        return 0.0

    tmp = target.with_name(target.stem + ".gain.tmp" + target.suffix)
    command = [
        str(ffmpeg), "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(target),
        "-af", f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={TARGET_LRA}", "-ar", "44100",
    ]
    command += ["-c:a", "pcm_s16le"] if target.suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-b:a", "192k"]
    command.append(str(tmp))
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, **subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        tmp.unlink(missing_ok=True)
        return 0.0
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return 0.0
    tmp.replace(target)
    for cached in [k for k in _cache if k[0] == str(target.resolve())]:
        _cache.pop(cached, None)
    return gain


def describe(path: str | Path) -> str:
    measured = measure(path)
    if measured["i"] == 0.0:
        return "không đo được"
    return f"{measured['i']:.1f} LUFS · peak {measured['tp']:.1f} dB"