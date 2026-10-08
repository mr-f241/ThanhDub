# PyInstaller spec cho ThanhDub — thường được gọi qua scripts/build.py (Windows)
# hoặc scripts/build_linux.py (Linux/macOS). Cùng một spec dùng được cho cả hai hệ điều hành.
# Công cụ ngoài (ffmpeg, ffprobe, whisper, libmpv) lấy từ thư mục REVIEWTRANS_TOOLS (mặc định build/tools).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

app_dir = Path(SPECPATH)
IS_WINDOWS = sys.platform.startswith("win")
tools_dir = Path(os.environ.get("REVIEWTRANS_TOOLS", app_dir / "build" / "tools"))
# version_info.txt là resource của Windows (VS_VERSIONINFO) — Linux/macOS không dùng.
version_file = (os.environ.get("REVIEWTRANS_VERSION_FILE") or None) if IS_WINDOWS else None
# PyInstaller nhận .ico trên Windows, .png trên Linux/macOS.
app_icon = app_dir / ("icon.ico" if IS_WINDOWS else "icon.png")

datas = [(str(app_dir / "icon.ico"), "."), (str(app_dir / "icon.png"), ".")]
# Trên Linux/macOS công cụ là file thực thi + .so: phải đưa vào `binaries` để giữ quyền +x.
tool_binaries = []
if tools_dir.is_dir():
    if IS_WINDOWS:
        # để nguyên file .exe/.dll (datas) — không cho PyInstaller phân tích/sửa
        datas += [(str(path), "bin") for path in sorted(tools_dir.iterdir())
                  if path.is_file() and path.suffix.lower() in (".exe", ".dll")]
        # bản whisper GPU nằm trong thư mục con riêng (bin/whisper-vulkan, bin/whisper-cuda) vì DLL ggml khác nhau
        for sub in sorted(p for p in tools_dir.iterdir() if p.is_dir() and p.name.startswith("whisper-")):
            datas += [(str(path), f"bin/{sub.name}") for path in sorted(sub.iterdir())
                      if path.is_file() and path.suffix.lower() in (".exe", ".dll")]
    else:
        def _add_tool(path: Path, target: str) -> None:
            # file không đuôi = executable (ffmpeg, ffprobe, whisper-cli); .so/.dylib = thư viện đi kèm
            if path.is_file() and (path.suffix == "" or path.suffix.lower() in (".so", ".dylib")):
                (tool_binaries if path.suffix == "" else datas).append((str(path), target))
            elif path.is_file() and ".so." in path.name:
                datas.append((str(path), target))

        for path in sorted(tools_dir.iterdir()):
            _add_tool(path, "bin")
        for sub in sorted(p for p in tools_dir.iterdir() if p.is_dir() and p.name.startswith("whisper-")):
            for path in sorted(sub.iterdir()):
                _add_tool(path, f"bin/{sub.name}")

hiddenimports = (
    collect_submodules("reviewtrans")
    + collect_submodules("google.genai")
    + collect_submodules("riva.client")
    + ["mpv", "edge_tts", "py7zr"]
)

# Moonshine (asr_moonshine) import torch/transformers, nhưng cả bộ nặng hơn 1 GB. Mặc định
# không đóng gói: app báo chạy từ mã nguồn để dùng Moonshine (xem resources.moonshine_runtime).
# Đặt REVIEWTRANS_INCLUDE_TORCH=1 nếu muốn bản đóng gói có sẵn Moonshine.
excludes = ["tkinter", "pytest", "pyflakes", "PyInstaller"]
if not os.environ.get("REVIEWTRANS_INCLUDE_TORCH"):
    excludes += ["torch", "torchaudio", "torchvision", "tensordict", "transformers", "triton", "tensorboard"]

a = Analysis(
    ["main.py"],
    pathex=[str(app_dir)],
    binaries=tool_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ThanhDub",
    icon=str(app_icon) if app_icon.is_file() else None,
    version=version_file,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX làm tăng cảnh báo nhầm của antivirus
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ThanhDub",
)