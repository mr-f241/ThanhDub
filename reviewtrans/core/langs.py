from __future__ import annotations

import re

# code nội bộ -> (tên hiển thị, tên tiếng Anh cho LLM, Google, Microsoft, Whisper, Riva)
LANGUAGES: dict[str, tuple[str, str, str, str, str, str]] = {
    "auto": ("Tự nhận diện", "the source language", "auto", "", "auto", ""),
    "zh": ("Tiếng Trung (giản thể)", "Simplified Chinese", "zh-CN", "zh-Hans", "zh", "zh-CN"),
    "zh-tw": ("Tiếng Trung (phồn thể)", "Traditional Chinese", "zh-TW", "zh-Hant", "zh", "zh-TW"),
    "vi": ("Tiếng Việt", "Vietnamese", "vi", "vi", "vi", "vi"),
    "en": ("Tiếng Anh", "English", "en", "en", "en", "en"),
    "ja": ("Tiếng Nhật", "Japanese", "ja", "ja", "ja", "ja"),
    "ko": ("Tiếng Hàn", "Korean", "ko", "ko", "ko", "ko"),
    "th": ("Tiếng Thái", "Thai", "th", "th", "th", "th"),
    "id": ("Tiếng Indonesia", "Indonesian", "id", "id", "id", "id"),
    "fr": ("Tiếng Pháp", "French", "fr", "fr", "fr", "fr"),
    "de": ("Tiếng Đức", "German", "de", "de", "de", "de"),
    "es": ("Tiếng Tây Ban Nha", "Spanish", "es", "es", "es", "es-ES"),
    "ru": ("Tiếng Nga", "Russian", "ru", "ru", "ru", "ru"),
    "pt": ("Tiếng Bồ Đào Nha", "Portuguese", "pt", "pt", "pt", "pt-BR"),
    "hi": ("Tiếng Hindi", "Hindi", "hi", "hi", "hi", "hi"),
}

SOURCE_LANGUAGES = list(LANGUAGES.keys())
TARGET_LANGUAGES = [code for code in LANGUAGES if code != "auto"]


def display_name(code: str) -> str:
    return LANGUAGES.get(code, (code,))[0]


CJK_LANGS = frozenset({"zh", "ja", "ko"})
_CJK = re.compile("[㐀-䶿一-鿿豈-﫿]")


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def count_cjk(text: str) -> int:
    """Số chữ Trung còn sót — để chọn ra bản 'ít hỏng hơn' khi cả hai bản hỏi lại đều chưa đạt."""
    return len(_CJK.findall(text))


def is_cjk_lang(code: str) -> bool:
    """Mục tiêu dùng chữ Hán/Hanja → bản dịch có CJK là bình thường, không tính là sót."""
    return code.split("-")[0].strip().lower() in CJK_LANGS


def scrub_term(text: str) -> str:
    """Thuật ngữ/nhân vật bị LLM trộn chữ Trung vào bản dịch La-tinh → gỡ chữ Trung ra.

    Đã gặp thật: ``兵匪`` bị lưu thành ``binh匪 và tặc`` — một mục glossary hỏng làm
    cả prompt lẫn phép kiểm bản dịch sai theo. Chỉ gỡ khi phần còn lại còn chữ cái;
    target thuần chữ Trung là hợp lệ (giữ nguyên từ gốc / dịch sang tiếng Trung).
    """
    if not text or not has_cjk(text):
        return text
    stripped = _CJK.sub(" ", text)
    if not re.search(r"[^\W\d_]", stripped):  # không còn chữ nào → đừng xoá mất trắng
        return text
    return re.sub(r"\s+", " ", stripped).strip() or text


def english_name(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[1] if entry else code


def google_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[2] if entry else code


def microsoft_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[3] if entry else code


def whisper_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[4] if entry else code


def riva_code(code: str) -> str:
    entry = LANGUAGES.get(code)
    return entry[5] if entry else ""
