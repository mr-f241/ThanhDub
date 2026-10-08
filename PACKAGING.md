# Đóng gói ThanhDub

Kết quả build nằm trong `release/`:

| File | Dùng khi |
|---|---|
| `ThanhDub-<phiên bản>-setup.exe` | Cài đặt bình thường (không cần quyền admin, cài vào `%LOCALAPPDATA%\Programs\ThanhDub`, có shortcut và gỡ cài đặt) |
| `ThanhDub-<phiên bản>-portable.zip` | Giải nén là chạy; mọi dữ liệu (cài đặt, project, model) lưu trong `data\` cạnh `ThanhDub.exe` |

Trên Linux/macOS gói tương ứng là `ThanhDub-<phiên bản>-linux-x86_64.tar.xz` (xem [Build trên Linux](#build-trên-linux--macos)).

Cả hai được tạo từ cùng một bản PyInstaller dạng thư mục (`dist\ThanhDub\`), không dùng dạng 1 file exe
(khởi động chậm vì phải giải nén vài trăm MB mỗi lần, dễ bị antivirus báo nhầm).

## Build trên máy

```bat
build.cmd
build.cmd --version 2.1.0
build.cmd --no-libmpv --skip-installer
```

Cần Python 3.10+ trong PATH. Muốn có installer thì cài [Inno Setup 6](https://jrsoftware.org/isdl.php);
thiếu Inno Setup thì script vẫn tạo zip portable và bỏ qua installer.

`build.cmd` tự tạo venv `.venv_build`, cài thư viện rồi gọi `scripts\build.py`, lần lượt:

1. **tools**: gom ffmpeg, ffprobe, whisper.cpp và libmpv vào `build\tools\`. Ưu tiên lấy từ `bin\` của repo
   hoặc thư mục công cụ của app (`%USERPROFILE%\.video_translation_studio\bin`), không có mới tải về.
   Bản whisper.cpp **Vulkan** (nếu có) được đặt vào `build\tools\whisper-vulkan\`, trong app là `bin\whisper-vulkan\`.
2. **app**: PyInstaller theo `build.spec` → `dist\ThanhDub\`.
3. **check**: chạy `ThanhDub.exe --self-check` trong môi trường sạch (PATH tối thiểu, thư mục người dùng tạm)
   để chắc bản đóng gói đủ thư viện và công cụ.
4. **zip**: nén bản portable (thêm `portable.txt`).
5. **installer**: Inno Setup theo `installer\ThanhDub.iss`.

Có thể chạy riêng từng bước: `python scripts\build.py tools|app|check|zip|installer`.

### whisper.cpp bản Vulkan (GPU AMD / Intel / NVIDIA)

whisper.cpp không phát hành bản Vulkan cho Windows nên ThanhDub tự build:

```bat
python scripts\build.py whisper-vulkan
```

Cần git, CMake, Visual Studio (C++) và [Vulkan SDK](https://vulkan.lunarg.com/sdk/home) (biến `VULKAN_SDK`).
Kết quả là `build\whisper-vulkan-x64.zip` (phiên bản whisper.cpp: `WHISPER_CPP_REF` trong `scripts\build.py`,
liên kết tĩnh nên không cần VC++ Redistributable). Bước **tools** tự lấy file zip này; hoặc chỉ định
`--whisper-vulkan <zip|thư mục>`. Không có thì bước tools thử tải từ release của repo, không được thì bỏ qua
(app vẫn nhận dạng bằng CPU).

Bản CUDA (NVIDIA) không đóng gói kèm vì nặng 270–680 MB; người dùng tải ở trang Tài nguyên trong app.

## Build trên Linux / macOS

Dùng cùng một `build.spec`, nhưng qua `scripts/build_linux.py`:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/build_linux.py all              # tools → app → check → package
.venv/bin/python scripts/build_linux.py install          # cài bản vừa build vào ~/.local
```

Kết quả build nằm trong `release/`:

| File | Dùng khi |
|---|---|
| `ThanhDub-<phiên bản>-linux-x86_64.tar.xz` | Giải nén rồi `./run.sh`, hoặc `./install.sh` để có menu ứng dụng và lệnh `thanhdub` |

Các bước giống hệt bản Windows, khác ở chỗ:

1. **tools**: ffmpeg/ffprobe lấy từ `~/.video_translation_studio/bin`, từ `bin/` của repo, hoặc tải bản static
   cho Linux. whisper.cpp **không có gói binary chính thức cho Linux** nên script lấy `whisper-cli` của gói
   distro (`sudo apt install whisper.cpp`) rồi chép kèm thư viện `libwhisper`/`libggml` (giữ symlink).
   Không có thì bỏ qua — app vẫn chạy, chỉ thiếu nhận dạng offline.
2. **app**: PyInstaller theo `build.spec` → `dist/ThanhDub/` (file `ThanhDub` + `_internal/`).
3. **check**: chạy `ThanhDub --self-check` với `PATH` tối thiểu và `HOME` tạm để chắc bản đóng gói tự đủ.
4. **package**: nén `tar.xz`, kèm `run.sh`, `reviewtrans.desktop`, `install.sh`, `README.txt`.

Tuỳ chọn:

| Tham số | Tác dụng |
|---|---|
| `--no-ffmpeg` | Không đóng gói ffmpeg (nhẹ hơn ~340 MB) — app dùng `ffmpeg` trong `PATH` |
| `--no-whisper` | Không đóng gói whisper.cpp |
| `--with-moonshine` | Đóng gói kèm `torch` + `transformers` để Moonshine chạy được (build nặng ~1 GB) |
| `--skip-tar` | Build xong không nén gói |

Mặc định **không** đóng gói `torch`/`transformers`: app sẽ báo chạy từ mã nguồn nếu muốn dùng Moonshine
(giống bản Windows).

### Công cụ hệ thống

| Thứ | Bắt buộc? | Cách cài |
|---|---|---|
| glibc ≥ 2.31 (Ubuntu 20.04+) | có | — |
| libGL, libEGL, libxkbcommon, fontconfig | có | `sudo apt install libgl1 libegl1 libxkbcommon0 fontconfig` |
| libmpv2 | không | `sudo apt install libmpv2` — có thì dùng player libmpv, không thì tự dùng Qt Multimedia |

`find_tool()` cũng dò `PATH` của hệ thống cho `ffmpeg`, `ffprobe`, `whisper-cli`, nên bản build với
`--no-ffmpeg` vẫn chạy được nếu máy đã có ffmpeg.

## Build và phát hành tự động trên GitHub

Workflow `.github/workflows/build.yml` (Windows runner):

| Sự kiện | Việc được làm |
|---|---|
| Mở / cập nhật pull request | Chỉ chạy test |
| **Merge (push) vào `main`** | Build whisper.cpp Vulkan (cache theo phiên bản) → test → đóng gói → **tự tạo release** |
| Push tag `vX.Y.Z` | Đóng gói → tạo/cập nhật release cho tag đó |
| Bấm **Run workflow** (tab Actions) | Đóng gói, file nằm ở mục **Artifacts** của lần chạy (giữ 14 ngày) |

Quy tắc tự tạo release khi merge vào `main`:

- `APP_VERSION` trong `reviewtrans/__init__.py` **chưa có release** → tạo release chính thức `vX.Y.Z`,
  đánh dấu **Latest**, kèm ghi chú thay đổi tự sinh từ các PR.
- **Đã có** release của phiên bản đó → cập nhật release thử nghiệm **`nightly`** (luôn là bản build mới nhất của
  `main`, thay file mỗi lần merge, không tạo thêm release rác).

Mỗi release kèm thêm `whisper-vulkan-x64.zip` để app (bản chạy từ mã nguồn hoặc bản cũ) tải về ở trang Tài nguyên.
Job build Vulkan được phép lỗi: khi đó bản cài đặt vẫn ra, chỉ thiếu whisper Vulkan.

**Ra bản chính thức mới:** tăng `APP_VERSION` (ví dụ `2.0.0` → `2.1.0`) trong một PR rồi merge.

Tải bản mới nhất: https://github.com/mr-f241/ThanhDub/releases/latest

> File cài đặt nằm ở mục **Releases** (cột phải trang repo), không phải **Packages**: Packages của GitHub dành cho
> gói npm/Docker/Maven… nên không dùng cho file `.exe`/`.zip`.

## Chế độ portable

App chạy ở chế độ portable khi có file `portable.txt` cạnh `ThanhDub.exe` (bản zip có sẵn file này).
Xoá file đó để app dùng thư mục người dùng như bản cài đặt.
