from __future__ import annotations

import ctypes.util
import os
import platform
import shutil
import sys
from functools import lru_cache
from pathlib import Path

USER_DIR_NAME = ".video_translation_studio"

IS_WINDOWS = platform.system().lower().startswith("win")
IS_MACOS = platform.system().lower().startswith("darwin")
IS_LINUX = platform.system().lower().startswith("linux")

# Tên thay thế cho từng công cụ (whisper.cpp đổi tên main.exe -> whisper-cli.exe).
TOOL_ALIASES = {
    "whisper": ["whisper-cli", "whisper", "main"],
}


def app_root() -> Path:
    """Thư mục gốc chứa tài nguyên đóng gói (bin/, icon...)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def resource_path(relative: str) -> Path:
    return app_root() / relative


def exe_dir() -> Path:
    """Thư mục chứa file exe (bản đóng gói) hoặc thư mục mã nguồn."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_portable() -> bool:
    """Bản zip portable có file portable.txt cạnh exe → lưu mọi dữ liệu ngay trong thư mục app."""
    return bool(os.environ.get("REVIEWTRANS_PORTABLE")) or (
        getattr(sys, "frozen", False) and (exe_dir() / "portable.txt").is_file()
    )


def user_data_dir() -> Path:
    path = exe_dir() / "data" if is_portable() else Path.home() / USER_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_bin_dir() -> Path:
    """Nơi lưu các công cụ tải về (ffmpeg, whisper, libmpv)."""
    path = user_data_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = user_data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fonts_dir() -> Path:
    path = user_data_dir() / "fonts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_projects_root() -> Path:
    if is_portable():
        return user_data_dir() / "projects"
    return Path.home() / "ThanhDub Projects"


def bin_dirs() -> list[Path]:
    dirs = [resource_path("bin"), resource_path("tools"), user_bin_dir()]
    # thư mục công cụ bổ sung (CI dùng build/tools); phân cách bằng os.pathsep
    dirs += [Path(p) for p in os.environ.get("REVIEWTRANS_EXTRA_BIN", "").split(os.pathsep) if p]
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / "bin")
    return dirs


def _executable_names(name: str) -> list[str]:
    names = []
    for alias in TOOL_ALIASES.get(name, [name]):
        if IS_WINDOWS:
            names.append(f"{alias}.exe")
        names.append(alias)
    return names


# Chỉ dò PATH cho tên không trùng tên công cụ khác: `whisper` (của OpenAI) có CLI khác hẳn
# whisper.cpp nên không được nhầm — còn ffmpeg/ffprobe/whisper-cli thì tên là duy nhất.
PATH_FALLBACK = ("ffmpeg", "ffprobe", "whisper-cli")


@lru_cache(maxsize=32)
def _which(name: str) -> Path | None:
    """Tìm trong PATH của hệ thống (Linux thường cài ffmpeg bằng gói distro)."""
    found = shutil.which(name)
    return Path(found) if found else None


def find_tool(name: str) -> Path | None:
    for directory in bin_dirs():
        for candidate in _executable_names(name):
            path = directory / candidate
            if path.is_file():
                return path
    if name in PATH_FALLBACK:
        return _which(name)
    return None


def find_libmpv() -> Path | None:
    names = ["libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"] if IS_WINDOWS else ["libmpv.so.2", "libmpv.so", "libmpv.dylib"]
    for directory in bin_dirs():
        for name in names:
            path = directory / name
            if path.is_file():
                return path
    return system_libmpv()


def system_libmpv() -> Path | None:
    """libmpv do hệ điều hành cài (Linux/macOS) — dùng khi không đóng gói kèm.

    Linux: dò ld.so cache/cache của loader, vd /usr/lib/x86_64-linux-gnu/libmpv.so.2 (gói `libmpv2`).
    macOS: Homebrew đặt ở /opt/homebrew/lib hoặc /usr/local/lib.
    """
    if IS_WINDOWS:
        return None
    if IS_MACOS:
        for folder in ("/opt/homebrew/lib", "/usr/local/lib", "/usr/lib"):
            path = Path(folder) / "libmpv.dylib"
            if path.is_file():
                return path
        return None
    soname = ctypes.util.find_library("mpv")  # vd "libmpv.so.2"
    if not soname:
        return None
    if os.path.isabs(soname) and os.path.isfile(soname):
        return Path(soname)
    folders = [Path("/lib"), Path("/usr/lib"), Path("/usr/lib64"), Path("/usr/local/lib"), Path("/lib64")]
    for folder in folders:
        # glibc đặt thư viện theo multiarch: /usr/lib/x86_64-linux-gnu/libmpv.so.2
        candidates = [folder] + ([p for p in folder.glob("*-linux-gnu") if p.is_dir()] if folder.is_dir() else [])
        for base in candidates:
            path = base / soname
            if path.is_file():
                return path
    return None


def register_dll_dirs() -> None:
    """Thêm các thư mục bin vào biến thư viện của hệ thốt để ctypes/python-mpv tìm được libmpv."""
    existing = os.environ.get("PATH", "")
    extra = [str(d) for d in bin_dirs() if d.is_dir() and str(d) not in existing]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + [existing])
    if IS_WINDOWS and hasattr(os, "add_dll_directory"):
        for directory in bin_dirs():
            if directory.is_dir():
                try:
                    os.add_dll_directory(str(directory))
                except OSError:
                    pass
    elif not IS_WINDOWS:
        # loader của Linux/macOS chỉ nhìn LD_LIBRARY_PATH (DYLD_LIBRARY_PATH trên macOS)
        var = "DYLD_LIBRARY_PATH" if IS_MACOS else "LD_LIBRARY_PATH"
        current = os.environ.get(var, "")
        lib_dirs = [str(d) for d in bin_dirs() if d.is_dir() and str(d) not in current.split(os.pathsep)]
        if lib_dirs:
            os.environ[var] = os.pathsep.join(lib_dirs + [current] if current else lib_dirs)
