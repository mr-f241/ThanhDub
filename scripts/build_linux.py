"""Build ThanhDub cho Linux/macOS: gom công cụ → PyInstaller (onedir) → tự kiểm tra
→ tar.xz kèm launcher + file .desktop + script cài đặt.

Dùng chung cho máy cá nhân và GitHub Actions.

    python scripts/build_linux.py all [--version 2.1.0] [--no-ffmpeg] [--no-whisper] [--skip-tar]
    python scripts/build_linux.py tools          # chỉ gom công cụ vào build/tools
    python scripts/build_linux.py app|check|package
    python scripts/build_linux.py install        # cài bản vừa build vào ~/.local

Khác với scripts/build.py (Windows):
  - Công cụ tải về lưu ở thư mục `bin` của người dùng (~/.video_translation_studio/bin) hoặc
    dùng bản hệ thống; script chép vào build/tools (giữ symlink, giữ quyền +x).
  - whisper.cpp không có gói binary chính thức cho Linux nên lấy từ gói của distro
    (apt install whisper.cpp) hoặc từ thư mục người dùng; không có thì bỏ qua.
  - libmpv không đóng gói kèm: app dùng libmpv của hệ thống (gói `libmpv2`), hoặc rơi về
    Qt Multimedia.
  - Không có installer .exe; đóng gói dạng tar.xz cho người dùng tự giải nén / chạy install.sh.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

IS_MACOS = platform.system().lower().startswith("darwin")
TOOLS = ROOT / "build" / "tools"
DIST = ROOT / "dist" / "ThanhDub"
RELEASE = ROOT / "release"
EXE = DIST / "ThanhDub"
APP_NAME = "ThanhDub"
FFMPEG_NAMES = ("ffmpeg", "ffprobe")
WHISPER_EXES = ("whisper-cli", "whisper", "main")


def log(message: str) -> None:
    print(f"[build-linux] {message}", flush=True)


def source_dirs() -> list[Path]:
    """Nơi tìm công cụ đã có: thư mục tools của app, thư mục người dùng, PATH hệ thống."""
    from reviewtrans.core.paths import user_bin_dir

    dirs = [TOOLS, ROOT / "bin", user_bin_dir()]
    for extra in os.environ.get("REVIEWTRANS_EXTRA_BIN", "").split(os.pathsep):
        if extra:
            dirs.append(Path(extra))
    return dirs


def _find(name: str) -> Path | None:
    for folder in source_dirs():
        for candidate in (folder / name, folder / f"{name}.exe"):
            if candidate.is_file() or candidate.is_symlink():
                return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _copy_tool(src: Path, dst: Path) -> None:
    """Chép công cụ giữ nguyên quyền thực thi và symlink (whisper.cpp cần libwhisper.so.1 đúng tên)."""
    if src.is_symlink():
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        os.symlink(os.readlink(src), dst)  # symlink tương đối vẫn đúng khi nằm cùng thư mục
        return
    shutil.copy2(src, dst)
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _progress(label: str):
    state = {"last": -10.0}

    def report(pct: float, _msg: str = "") -> None:
        if pct - state["last"] >= 10 or pct >= 100:
            state["last"] = pct
            log(f"  {label}: {pct:.0f}%")

    return report


# ------------------------------------------------------------------ công cụ


def fetch_tools(with_ffmpeg: bool = True, with_whisper: bool = True) -> None:
    """Chuẩn bị build/tools: FFmpeg (bắt buộc) và whisper.cpp (tuỳ chọn trên Linux)."""
    from reviewtrans.core.pipeline import resources

    TOOLS.mkdir(parents=True, exist_ok=True)

    if with_ffmpeg:
        if all((TOOLS / n).is_file() for n in FFMPEG_NAMES):
            log("FFmpeg: đã có trong build/tools")
        else:
            staged = [_find(n) for n in FFMPEG_NAMES]
            if all(staged):
                for path in staged:
                    _copy_tool(path, TOOLS / path.name)
                log(f"FFmpeg: dùng bản có sẵn ({staged[0].parent})")
            else:
                log("FFmpeg: đang tải bản static cho Linux…")
                resources.download_ffmpeg(_progress("FFmpeg"), bin_dir=TOOLS)
    else:
        for name in FFMPEG_NAMES:
            (TOOLS / name).unlink(missing_ok=True)

    if with_whisper:
        stage_whisper()
    else:
        for name in WHISPER_EXES:
            (TOOLS / name).unlink(missing_ok=True)

    files = sorted(p for p in TOOLS.rglob("*") if p.is_file() or p.is_symlink())
    total = sum(p.stat().st_size for p in files if p.is_file())
    log(f"Công cụ trong {TOOLS}: {', '.join(p.name for p in files[:12])}"
        f"{'…' if len(files) > 12 else ''} ({total / 1e6:.0f} MB)")


def stage_whisper() -> None:
    """Đưa whisper-cli + thư viện .so đi kèm vào build/tools.

    whisper.cpp không phát hành binary Linux chính thức, nên lấy từ gói distro
    (apt install whisper.cpp → /usr/bin/whisper-cli) hoặc thư mục người dùng.
    """
    have = next((TOOLS / n for n in WHISPER_EXES if (TOOLS / n).is_file() or (TOOLS / n).is_symlink()), None)
    if have is not None:
        log(f"whisper.cpp: đã có ({have.name})")
        return
    exe = next((p for p in (_find(n) for n in WHISPER_EXES) if p), None)
    if exe is None:
        log("whisper.cpp: không có sẵn trên máy — bỏ qua (app sẽ dùng Moonshine hoặc nhận dạng online).")
        log("  Cài bằng: sudo apt install whisper.cpp   rồi chạy lại 'tools'.")
        return
    _copy_tool(exe, TOOLS / exe.name)
    libs = 0
    for lib in sorted(exe.parent.iterdir()):
        if lib.is_file() and (lib.name.startswith("libwhisper") or lib.name.startswith("libggml")):
            _copy_tool(lib, TOOLS / lib.name)
            libs += 1
    log(f"whisper.cpp: lấy từ {exe.parent} ({exe.name} + {libs} thư viện)")


# ------------------------------------------------------------------ các bước


def build_app(version: str, with_moonshine: bool = False) -> None:
    from build import write_version_files  # scripts/build.py (dùng chung hàm ghi _build_info)

    if not all((TOOLS / n).is_file() for n in FFMPEG_NAMES):
        log("Thiếu build/tools/ffmpeg — chạy bước 'tools' trước (hoặc --no-ffmpeg để bỏ).")
    write_version_files(version)
    env = dict(os.environ, REVIEWTRANS_TOOLS=str(TOOLS))
    env.pop("REVIEWTRANS_VERSION_FILE", None)  # resource .exe chỉ dùng cho Windows
    if with_moonshine:
        log("Moonshine: đóng gói kèm torch + transformers (bản build nặng hơn ~1 GB)")
        env["REVIEWTRANS_INCLUDE_TORCH"] = "1"
    else:
        env.pop("REVIEWTRANS_INCLUDE_TORCH", None)
    log(f"PyInstaller (phiên bản {version})…")
    started = time.time()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"), str(ROOT / "build.spec")],
        cwd=ROOT, env=env, check=True,
    )
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    log(f"Xong sau {time.time() - started:.0f}s → {DIST} ({size / 1e6:.0f} MB)")


def self_check(require: list[str]) -> None:
    if not EXE.is_file():
        raise SystemExit(f"Không thấy {EXE}")
    report = ROOT / "build" / "selfcheck-linux.json"
    report.unlink(missing_ok=True)
    log("Tự kiểm tra bản đóng gói…")
    # chạy trong thư mục tạm, không dùng PATH/thư mục công cụ của máy build để chắc bản đóng gói tự đủ
    env = {k: v for k, v in os.environ.items() if k != "REVIEWTRANS_EXTRA_BIN"}
    env["PATH"] = "/usr/bin:/bin"
    fake_home = Path(tempfile.mkdtemp(prefix="rt_home_"))
    env["HOME"] = str(fake_home)
    try:
        code = subprocess.run([str(EXE), "--self-check", str(report), *require], env=env, timeout=300).returncode
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    for name, state in data.get("modules", {}).items():
        if state != "ok":
            log(f"  ✕ {name}: {state}")
    log(f"  libmpv: {data.get('libmpv')}")
    for tool, path in data.get("tools", {}).items():
        run = data.get("runs", {}).get(tool)
        log(f"  {tool}: {path or 'không có'}" + (f" (chạy thử: mã {run})" if run is not None else ""))
    if code != 0 or not data.get("ok"):
        raise SystemExit("Tự kiểm tra thất bại — xem build/selfcheck-linux.json")
    log("  ✓ đủ thư viện và công cụ")


# ------------------------------------------------------------------ đóng gói


def launcher_script(version: str) -> str:
    return f"""#!/bin/sh
# ThanhDub {version} — launcher cho Linux.
# App tự thêm thư mục công cụ vào LD_LIBRARY_PATH; dòng dưới để whisper-cli/libmpv nạp
# được ngay cả khi app được gọi từ script khác.
# Đọc theo đường dẫn thật vì ~/.local/bin/thanhdub là symlink trỏ tới file này.
SELF=$(readlink -f "$0" 2>/dev/null) || SELF=$0
DIR=$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)
export LD_LIBRARY_PATH="$DIR/_internal/bin:$LD_LIBRARY_PATH"
exec "$DIR/ThanhDub" "$@"
"""


def desktop_file() -> str:
    """Mẫu .desktop — @PREFIX@ được install.sh thay bằng đường dẫn cài thật.

    Exec trỏ thẳng vào run.sh vì spec desktop entry không cho dùng `sh -c` với dấu nháy lẫn `$`.
    """
    return """[Desktop Entry]
Type=Application
Name=ThanhDub
GenericName=Dịch và lồng tiếng video
Comment=Dịch, lồng tiếng và chèn phụ đề tự động cho video review
Exec=@PREFIX@/run.sh %F
Path=@PREFIX@
Icon=thanhdub
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
StartupNotify=true
"""


def install_script() -> str:
    return """#!/bin/sh
# Cài ThanhDub vào ~/.local (không cần quyền root).
#   ./install.sh            → cài vào ~/.local/opt/thanhdub, tạo lệnh `thanhdub` và menu ứng dụng
#   ./install.sh --uninstall
set -e
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
OPT="$HOME/.local/opt/thanhdub"

if [ "$1" = "--uninstall" ]; then
  rm -rf "$OPT" "$HOME/.local/bin/thanhdub" "$PREFIX/applications/thanhdub.desktop" \
         "$PREFIX/icons/hicolor/256x256/apps/thanhdub.png"
  rmdir "$PREFIX/icons/hicolor/256x256/apps" "$PREFIX/icons/hicolor/256x256" "$PREFIX/icons/hicolor" 2>/dev/null || true
  echo "Đã gỡ ThanhDub."
  exit 0
fi

echo "→ cài vào $OPT"
rm -rf "$OPT"
mkdir -p "$OPT"
cp -a "$SRC/." "$OPT/"

# biểu tượng: PyInstaller đặt tài nguyên vào _internal/
ICON="$OPT/icon.png"
[ -f "$ICON" ] || ICON="$OPT/_internal/icon.png"

mkdir -p "$HOME/.local/bin" "$PREFIX/applications" "$PREFIX/icons/hicolor/256x256/apps"
ln -sf "$OPT/run.sh" "$HOME/.local/bin/thanhdub"
# .desktop cần đường dẫn tuyệt đối nên thay placeholder @PREFIX@
sed "s|@PREFIX@|$OPT|g" "$OPT/thanhdub.desktop" > "$PREFIX/applications/thanhdub.desktop"
chmod +x "$PREFIX/applications/thanhdub.desktop"
cp "$ICON" "$PREFIX/icons/hicolor/256x256/apps/thanhdub.png"
chmod +x "$OPT/run.sh"

echo "→ làm mới menu ứng dụng"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$PREFIX/icons/hicolor" || true

cat <<'MSG'

Xong. Mở app bằng:
  • menu ứng dụng: tìm "ThanhDub"
  • hoặc lệnh:    thanhdub

Thư viện hệ thống dùng cho player (không bắt buộc, thiếu thì app dùng Qt Multimedia):
  Debian/Ubuntu   sudo apt install libmpv2
  Fedora          sudo dnf install libmpv
  Arch            sudo pacman -S mpv
MSG
"""


def readme(version: str) -> str:
    return f"""ThanhDub {version} — bản đóng gói cho Linux
{'=' * 60}

Cách chạy nhanh
  ./run.sh

Cài vào menu ứng dụng và lệnh `thanhdub`
  ./install.sh
  ./install.sh --uninstall

Cấu trúc
  ThanhDub            file thực thi (chạy được nhờ bootloader của PyInstaller)
  _internal/             thư viện Python, Qt, provider
  _internal/bin/         công cụ đóng gói kèm (ffmpeg, ffprobe, whisper-cli)
  run.sh                 launcher (thêm bin/ vào LD_LIBRARY_PATH)
  thanhdub.desktop    entry menu ứng dụng (install.sh thay đường dẫn)
  install.sh             cài vào ~/.local
  _internal/icon.png     biểu tượng

Phụ thuộc hệ thống
  Bắt buộc: glibc 2.31+ (Ubuntu 20.04+), libGL, libEGL, libxkbcommon, fontconfig
  Nên có:   libmpv2 — player mượt. Thiếu thì app tự dùng Qt Multimedia.

    Debian/Ubuntu  sudo apt install libmpv2 libgl1 libegl1 libxkbcommon0 fontconfig
    Fedora         sudo dnf install mpv-libs mesa-libGL mesa-libEGL libxkbcommon fontconfig
    Arch           sudo pacman -S mpv mesa libxkbcommon fontconfig

Cài thêm FFmpeg (nếu bản này không đóng gói kèm) hoặc whisper.cpp
    sudo apt install ffmpeg whisper.cpp

Dữ liệu người dùng
  Mặc định lưu ở ~/.video_translation_studio (project, model, cấu hình).
  Muốn portable (mọi thứ nằm trong thư mục app) thì tạo file `portable.txt`
  cạnh file thực thi rồi chạy lại.
"""


def make_package(version: str) -> Path:
    if not EXE.is_file():
        raise SystemExit(f"Không thấy {EXE} — chạy bước 'app' trước.")
    RELEASE.mkdir(exist_ok=True)
    suffix = "macos" if IS_MACOS else "linux"
    target = RELEASE / f"{APP_NAME}-{version}-{suffix}-x86_64.tar.xz"
    target.unlink(missing_ok=True)
    log(f"Đóng gói {target.name}…")
    started = time.time()
    try:
        write_extras(version)
        with tarfile.open(target, "w:xz", preset=6) as tar:
            tar.add(DIST, arcname=APP_NAME, filter=_tar_filter)
    finally:
        remove_extras()
    log(f"  → {target} ({target.stat().st_size / 1e6:.0f} MB, nén trong {time.time() - started:.0f}s)")
    return target


def write_extras(version: str) -> None:
    """Viết launcher, .desktop, install.sh và README vào dist/ThanhDub."""
    extras = {
        "run.sh": launcher_script(version),
        "thanhdub.desktop": desktop_file(),
        "install.sh": install_script(),
        "README.txt": readme(version),
    }
    for name, text in extras.items():
        path = DIST / name
        path.write_text(text, encoding="utf-8")
        if name.endswith(".sh"):
            path.chmod(0o755)


def remove_extras() -> None:
    for name in ("run.sh", "thanhdub.desktop", "install.sh", "README.txt"):
        (DIST / name).unlink(missing_ok=True)


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Bỏ rác. Symlink và quyền thực thi được tar giữ nguyên theo metadata sẵn có trong dist."""
    parts = Path(info.name).parts
    if "__pycache__" in parts or parts[-1:] in ((".DS_Store", "Thumbs.db")):
        return None
    return info


# ------------------------------------------------------------------ cài đặt


def install() -> None:
    """Cài bản vừa build vào ~/.local bằng install.sh (không cần root)."""
    if not EXE.is_file():
        raise SystemExit(f"Không thấy {EXE} — chạy bước 'app' trước.")
    from build import current_version

    try:
        write_extras(current_version())
        subprocess.run(["sh", str(DIST / "install.sh")], check=True)
    finally:
        remove_extras()


# ------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", nargs="?", default="all",
                        choices=["all", "tools", "app", "check", "package", "install"])
    parser.add_argument("--version", default="", help="mặc định: APP_VERSION trong reviewtrans/__init__.py")
    parser.add_argument("--no-ffmpeg", action="store_true", help="không đóng gói FFmpeg (dùng bản hệ thống)")
    parser.add_argument("--no-whisper", action="store_true", help="không đóng gói whisper.cpp")
    parser.add_argument("--with-moonshine", action="store_true",
                        help="đóng gói kèm torch + transformers cho Moonshine (build nặng ~1 GB)")
    parser.add_argument("--skip-tar", action="store_true", help="build xong không nén tarball")
    args = parser.parse_args()

    from build import current_version

    version = args.version.strip().lstrip("v") or current_version()
    # chỉ yêu cầu công cụ thật sự được đóng gói kèm (whisper có thể không có trên máy build)
    require = ["ffmpeg", "ffprobe"] if not args.no_ffmpeg else []
    if any((TOOLS / name).is_file() or (TOOLS / name).is_symlink() for name in WHISPER_EXES):
        require.append("whisper")

    if args.step in ("all", "tools"):
        fetch_tools(with_ffmpeg=not args.no_ffmpeg, with_whisper=not args.no_whisper)
    if args.step in ("all", "app"):
        build_app(version, with_moonshine=args.with_moonshine)
    if args.step in ("all", "check"):
        self_check(require)
    if args.step in ("all", "package") and not args.skip_tar:
        make_package(version)
    if args.step == "install":
        install()
    if args.step == "all":
        log(f"Hoàn tất. Kết quả trong {RELEASE}")
        log(f"Chạy thử: {DIST}/run.sh   (hoặc: python scripts/build_linux.py install)")


if __name__ == "__main__":
    main()