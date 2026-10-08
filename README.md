<p align="center"><img src="assets/logo.svg" width="128" alt="ThanhDub"></p>

# ThanhDub

Công cụ dịch, lồng tiếng và chèn phụ đề tự động cho video review phim, với giao diện kiểu phần mềm dựng video.

## Tính năng

- **Project nhiều video**: mỗi project (một bộ phim) chứa nhiều video xếp theo thứ tự tập, dùng chung
  **ngữ cảnh**: tóm tắt cốt truyện, nhân vật + cách xưng hô, thuật ngữ. Sau mỗi video, LLM tự bổ sung ngữ cảnh.
  Mục nào đã “khoá” sẽ không bị ghi đè.
- **Editor**: bảng câu thoại (sửa trực tiếp, tách/gộp/khoá câu), khung phát libmpv (tự chuyển sang Qt Multimedia
  nếu chưa có libmpv), inspector, timeline nhiều track (layer, phụ đề, lồng tiếng, âm gốc, nhạc nền, video).
- **Dịch**: Google Translate, Microsoft Translator (không cần key hoặc dùng key Azure/Google Cloud),
  **NVIDIA NIM / Riva NMT** (dịch máy chuyên dụng 36 ngôn ngữ, chỉ cần key nvapi),
  **Deep Translator** (thư viện `deep-translator`: google, mymemory, libre, deepl, microsoft, yandex, papago, baidu),
  API kiểu OpenAI (OpenAI, DeepSeek, OpenRouter, LM Studio, **NVIDIA NIM** `integrate.api.nvidia.com/v1`…),
  API kiểu Gemini, Anthropic Claude. Hỗ trợ nhiều key xoay vòng.
  Bị lỗi 429 thì tự chờ và thử lại; Google đi được qua proxy/mirror bằng ô **Base URL**.
- **Model free qua OpenCode Zen**: loại provider **“OpenCode Zen (model free)”** ở trang Providers (hoặc nút
  nhanh **Model free (Zen)**) thêm `big-pickle`, `mimo-v2.6-flash-free`, `space-bunny-free`…
  (Base URL `https://opencode.ai/zen/v1`, giá $0/1M token — chỉ cần key lấy tại opencode.ai/auth).
  Màn hình chính có dải **“Model dịch: …”** cho biết đang dùng model nào, thiếu key thì gõ đỏ,
  bấm **Đổi model** là qua Providers.
- **Nhận dạng**: whisper.cpp (mọi máy, chạy được CPU/GPU) hoặc **Moonshine** (nhỏ, nhanh, 7 ngôn ngữ).
  Không muốn chờ dịch thì **xuất SRT → dịch ở ngoài → nhập lại** vào Editor.
- **Lồng tiếng**: **Blaze TTS** (api.blaze.vn — dán nhiều token, mỗi dòng một token, app tự xoay vòng),
  Edge TTS, vBee, API kiểu OpenAI `/v1/audio/speech`, Custom API bản cũ. Có thể đặt giọng riêng
  cho từng nhân vật hoặc từng câu. Sửa câu nào chỉ tạo lại lồng tiếng câu đó.
  Bấm **Chọn giọng…** để mở kho giọng Blaze (212 giọng, cache 12 giờ) và **nghe thử trước khi dùng** —
  mẫu nghe thử được cache nên bấm lại không tốn quota.
- **Bật/tắt** phụ đề, lồng tiếng, âm gốc, nhạc nền, từng layer.
- **Style phụ đề**: font, cỡ, màu chữ, viền, bóng, hộp nền, vị trí, lề, số ký tự mỗi dòng. Lưu thành preset.
- **Layer**: ảnh (logo), chữ, vùng che (làm mờ / pixel hoá / tô màu) để che chữ cứng của video gốc.
  Kéo thả trên khung phát và trên timeline.
- **Hàng đợi**: chạy hàng loạt Nhận dạng → Dịch → Cập nhật ngữ cảnh → Lồng tiếng → Trộn âm → Xuất video.
- **Tăng tốc phần cứng**: xuất video bằng GPU (NVIDIA NVENC, AMD AMF, Intel Quick Sync, Media Foundation), tự dò và
  chạy thử trước khi dùng, lỗi thì tự chuyển về CPU. Nhận dạng giọng nói bằng GPU qua whisper.cpp CUDA hoặc Vulkan.
- Trang riêng cho **Providers**, **Preset**, **Tài nguyên** (tải ffmpeg, whisper.cpp, model Whisper, libmpv),
  **Công cụ** (ghép video), **Cài đặt**.

⬇️ **[Tải bản mới nhất (installer / portable)](https://github.com/mr-f241/ThanhDub/releases/latest)**

📖 **[Hướng dẫn sử dụng đầy đủ (có ảnh minh hoạ)](docs/huong-dan-su-dung.md)**

## Chạy từ mã nguồn

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Trên Linux/macOS dùng `.venv/bin/pip` và `.venv/bin/python main.py`.

Lần đầu mở, vào trang **Tài nguyên** để tải FFmpeg, whisper.cpp và libmpv (player). Cấu hình cũ
(`~/.video_translation_studio/config.json`) được tự chuyển thành các provider.

## Bản cho Linux

Tải `ThanhDub-<phiên bản>-linux-x86_64.tar.xz` ở mục Releases, giải nén rồi:

```bash
./run.sh        # chạy luôn
./install.sh    # hoặc cài vào ~/.local → menu ứng dụng + lệnh `thanhdub`
```

Thư viện hệ thống cần có: `libgl1 libegl1 libxkbcommon0 fontconfig` (Ubuntu/Debian). Thêm
`sudo apt install libmpv2` để có player libmpv; thiếu thì app tự dùng Qt Multimedia. Muốn tự build:

```bash
.venv/bin/python scripts/build_linux.py all
```

Chi tiết trong [PACKAGING.md](PACKAGING.md).

## Cấu trúc

```
main.py                   điểm khởi chạy
reviewtrans/core/         mô hình dữ liệu, provider, pipeline (không phụ thuộc widget)
reviewtrans/ui/           giao diện PyQt6 (player, timeline, các trang)
tests/                    pytest (có test render thật bằng ffmpeg)
legacy/                   mã nguồn bản cũ, chỉ để tham khảo
```

Dữ liệu một project nằm trong thư mục của nó (`project.json`, `context.json`, `videos/<id>/…`, `output/`),
có thể sao chép hoặc sao lưu nguyên thư mục.

## Test

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```
