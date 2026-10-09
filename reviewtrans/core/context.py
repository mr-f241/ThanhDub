from __future__ import annotations

import json
import re

from .langs import english_name, scrub_term
from .models import Character, ContextLog, GlossaryEntry, ProjectContext, Segment

MAX_SUMMARY_CHARS = 4000


def context_block(context: ProjectContext, instructions: str = "") -> str:
    """Khối ngữ cảnh chèn vào prompt dịch."""
    parts: list[str] = []
    if context.style_notes.strip():
        parts.append("GENRE / TONE:\n" + context.style_notes.strip())
    if context.summary.strip():
        parts.append("STORY SO FAR:\n" + context.summary.strip())
    if context.characters:
        lines = []
        for c in context.characters:
            if not c.source and not c.target:
                continue
            detail = [x for x in (c.gender, c.role) if x]
            line = f"- {c.source} => {scrub_term(c.target) or c.source}"
            if detail:
                line += f" ({', '.join(detail)})"
            if c.addressing:
                line += f"; addressing: {c.addressing}"
            if c.note:
                line += f"; note: {c.note}"
            lines.append(line)
        if lines:
            parts.append("CHARACTERS (use these names and forms of address consistently):\n" + "\n".join(lines))
    if context.glossary:
        lines = [
            f"- {g.source} => {scrub_term(g.target)}" + (f" ({g.note})" if g.note else "")
            for g in context.glossary
            if g.source and g.target
        ]
        if lines:
            parts.append("GLOSSARY (mandatory translations):\n" + "\n".join(lines))
    if instructions.strip():
        parts.append("EXTRA INSTRUCTIONS FROM THE USER:\n" + instructions.strip())
    return "\n\n".join(parts)


def glossary_pairs(context: ProjectContext) -> list[tuple[str, str]]:
    # scrub_term: mục hỏng kiểu "binh匪 và tặc" không được đem đi ép bản dịch
    pairs = [(g.source, scrub_term(g.target)) for g in context.glossary if g.source and g.target]
    pairs += [(c.source, scrub_term(c.target)) for c in context.characters if c.source and c.target]
    # thay cụm dài trước để tránh thay một phần
    return sorted(set(pairs), key=lambda p: len(p[0]), reverse=True)


def apply_glossary_to_source(text: str, pairs: list[tuple[str, str]]) -> str:
    """Với máy dịch (Google/Microsoft): thay trước thuật ngữ gốc bằng bản dịch đã chốt."""
    for source, target in pairs:
        if source in text:
            text = text.replace(source, target)
    return text


# ------------------------------------------------------------------ auto update

UPDATE_SYSTEM = (
    "You maintain a shared translation context (a 'series bible') for a multi-episode "
    "video translation project. You read the latest episode's source lines and their "
    "translations, then report ONLY new or corrected information as JSON."
)


def name_rule(target_lang: str) -> str:
    """Quy tắc dựng tên riêng — riêng tiếng Việt phải theo âm Hán Việt, không trộn pinyin.

    Trả về "" với ngôn ngữ mục tiêu khác tiếng Việt (quy tắc đọc âm Hán Việt vô nghĩa ở đó).
    """
    if english_name(target_lang) != "Vietnamese":
        return ""
    return (
        "NAME RULE (Chinese personal names): every hanzi in a name is read with its standard "
        "Sino-Vietnamese syllable, then the syllables are joined — "
        '萧长 → "Tiêu Trường", 林凡 → "Lâm Phàm", 楚天翼 → "Sở Thiên Dực". '
        "Never mix Mandarin pinyin syllables (Xiao, Chang, Lin, Chu, Wang…) into a Vietnamese "
        "name, never spell a name the Mandarin way, and never translate what its characters mean. "
        "A name already present in PROJECT CONTEXT is authoritative — copy it exactly.\n"
    )


def build_update_prompt(
    context: ProjectContext,
    segments: list[Segment],
    source_lang: str,
    target_lang: str,
    video_name: str,
    max_lines: int = 600,
) -> str:
    lines = []
    step = max(1, len(segments) // max_lines) if len(segments) > max_lines else 1
    for seg in segments[::step]:
        if seg.source or seg.text:
            lines.append(f"{seg.source} || {seg.text}")
    current = {
        "summary": context.summary,
        "style_notes": context.style_notes,
        "characters": [
            {"source": c.source, "target": c.target, "gender": c.gender, "role": c.role, "addressing": c.addressing}
            for c in context.characters
        ],
        "glossary": [{"source": g.source, "target": g.target, "note": g.note} for g in context.glossary],
    }
    rule = name_rule(target_lang)
    name_line = (
        '"target" of characters and of glossary entries must follow this rule:\n' + rule
        if rule
        else ""
    )
    return (
        f"Source language: {english_name(source_lang)}. Target language: {english_name(target_lang)}.\n"
        f"Episode: {video_name}\n\n"
        "CURRENT CONTEXT (JSON):\n"
        f"{json.dumps(current, ensure_ascii=False)}\n\n"
        "EPISODE LINES (source || translation):\n"
        + "\n".join(lines)
        + "\n\n"
        "Return a JSON object with these keys:\n"
        '- "summary": the updated cumulative story summary in the TARGET language, '
        f"at most {MAX_SUMMARY_CHARS // 2} characters, merging the old summary with this episode.\n"
        '- "style_notes": genre/tone notes in the target language (keep the old text if nothing changes).\n'
        '- "characters": list of NEW or CORRECTED characters, each '
        '{"source","target","gender","role","addressing"}. "addressing" describes, in the target language, '
        "how this character refers to themselves and to others (pronouns / honorifics).\n"
        '- "glossary": list of NEW recurring proper nouns or terms {"source","target","note"} '
        "(places, sects, techniques, items, titles). Skip common words.\n"
        + name_line
        + "Keep names consistent with the existing context. Output JSON only."
    )


CONTEXT_KEYS = ("summary", "style_notes", "characters", "glossary")
# trường chỉ có ở nhân vật — dùng để phân biệt với thuật ngữ khi model trả mảng trần
CHARACTER_ONLY_KEYS = ("gender", "role", "addressing")


def _is_glossary_item(item: dict) -> bool:
    """Mục có 'note' mà không có trường nhân vật thì là thuật ngữ."""
    if any(str(item.get(k) or "").strip() for k in CHARACTER_ONLY_KEYS):
        return False
    return bool(str(item.get("note") or "").strip())


def context_dict(data):
    """Chuẩn hoá về dict cho ngữ cảnh.

    Model hay tuột kiểu mẫu, trả về lệch dạng:
    - bọc object đúng nghĩa trong mảng ``[{...}]``
    - trả thẳng mảng mục ``[{"source": ...}, ...]`` (mất object bọc)
    - hoặc mảng object rỗng ``[{"characters": [...]}, {"glossary": [...]}]``
    """
    if isinstance(data, list):
        if not data:
            return {}  # không có gì mới -> merge_update coi như không thay đổi
        dicts = [x for x in data if isinstance(x, dict)]
        if len(dicts) != len(data):
            return data  # lẫn kiểu lạ -> để update_context báo lỗi kèm dữ liệu
        # object đã đúng dạng, chỉ bị bọc nhầm trong mảng
        proper = [it for it in dicts if any(k in it for k in CONTEXT_KEYS)]
        if proper:
            merged: dict = {}
            for item in proper:
                merged.update(item)
            return merged if len(proper) > 1 else proper[0]
        # mảng mục đơn lẻ: chỉ nhận khi trông như mục ngữ cảnh (có "source")
        if not all("source" in it for it in dicts):
            return data
        characters = [it for it in dicts if not _is_glossary_item(it)]
        glossary = [it for it in dicts if _is_glossary_item(it)]
        out: dict = {}
        if characters:
            out["characters"] = characters
        if glossary:
            out["glossary"] = glossary
        return out
    if isinstance(data, dict) and "source" in data and not any(
        k in data for k in CONTEXT_KEYS
    ):
        return context_dict([data])  # {"source": ...} lẻ tẻ
    return data


def extract_json(text: str):
    """Lấy JSON đầu tiên trong câu trả lời của LLM (bỏ ```json ...``` và chữ "json" thừa)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    # model nhỏ hay in thêm "json" / "JSON:" ngay trước mảng
    text = re.sub(r"^(?:json|JSON)\s*[:：]?\s*", "", text).strip()
    # cắt theo opener SỚM NHẤT: mảng đứng trước object thì phải lấy theo mảng,
    # nếu không sẽ cắt mất ngoặc mở và hỏng JSON
    starts = [(text.find(opener), opener, closer) for opener, closer in (("{", "}"), ("[", "]")) if opener in text]
    for start, opener, closer in sorted(starts):
        end = text.rfind(closer)
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    # model viết {"id": 1, …}, {"id": 2, …} (quên ngoặc mảng) → bọc lại
    if text.startswith("{") and "}," in text.replace(" ", ""):
        return json.loads(f"[{text}]")
    return json.loads(text)


def merge_update(context: ProjectContext, data: dict, video_name: str) -> list[str]:
    """Gộp kết quả LLM vào ngữ cảnh, tôn trọng mục đã khoá. Trả về danh sách thay đổi."""
    changes: list[str] = []
    summary = str(data.get("summary") or "").strip()
    if summary and summary != context.summary:
        context.summary = summary[:MAX_SUMMARY_CHARS]
        changes.append("cập nhật tóm tắt")
    style = str(data.get("style_notes") or "").strip()
    if style and style != context.style_notes:
        context.style_notes = style
        changes.append("cập nhật thể loại/giọng văn")

    by_source = {c.source.strip(): c for c in context.characters if c.source.strip()}
    for raw in data.get("characters") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip()
        if not source:
            continue
        existing = by_source.get(source)
        if existing is None:
            char = Character(
                source=source,
                target=scrub_term(str(raw.get("target") or "").strip()),
                gender=str(raw.get("gender") or "").strip(),
                role=str(raw.get("role") or "").strip(),
                addressing=str(raw.get("addressing") or "").strip(),
                auto=True,
            )
            context.characters.append(char)
            by_source[source] = char
            changes.append(f"thêm nhân vật {source} → {char.target}")
        elif not existing.locked:
            updated = False
            for key in ("target", "gender", "role", "addressing"):
                value = str(raw.get(key) or "").strip()
                if key == "target":
                    value = scrub_term(value)
                if value and value != getattr(existing, key):
                    setattr(existing, key, value)
                    updated = True
            if updated:
                changes.append(f"sửa nhân vật {source}")

    glossary = {g.source.strip(): g for g in context.glossary if g.source.strip()}
    for raw in data.get("glossary") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip()
        target = scrub_term(str(raw.get("target") or "").strip())
        if not source or not target or source in by_source:
            continue
        existing = glossary.get(source)
        if existing is None:
            entry = GlossaryEntry(source=source, target=target, note=str(raw.get("note") or "").strip(), auto=True)
            context.glossary.append(entry)
            glossary[source] = entry
            changes.append(f"thêm thuật ngữ {source} → {target}")
        elif not existing.locked and existing.target != target:
            existing.target = target
            changes.append(f"sửa thuật ngữ {source} → {target}")

    if changes:
        context.changelog.append(ContextLog(video=video_name, message="; ".join(changes)))
        context.changelog = context.changelog[-200:]
    return changes
