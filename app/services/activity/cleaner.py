import re
import unicodedata


_NOISE_LINE = re.compile(
    r"^[\s\d\-_|•·.…]+$|^(ok|cancel|close|minimize|maximize|file|edit|view|help|"
    r"insert|format|tools|window|search|home|back|forward|refresh|reload|"
    r"settings|preferences|loading|waiting|submit|apply|done|yes|no|save|open|"
    r"new tab|sign in|log in|log out|copy|paste|undo|redo|cut|select all)$",
    re.IGNORECASE,
)

_UI_CHROME = re.compile(
    r"^(file|edit|view|insert|format|tools|window|help)(\s|$)",
    re.IGNORECASE,
)

_TIME_ONLY = re.compile(
    r"^\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?$",
    re.IGNORECASE,
)

_MIN_MEANINGFUL_LEN = 3


def clean_ocr_text(text: str | None) -> str:
    """Normalize OCR output: strip noise, dedupe lines, collapse whitespace."""
    if not text or not text.strip():
        return ""

    lines: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        if not line or _is_noise_line(line):
            continue
        key = line.casefold()
        if key in seen or _is_subsumed_by_seen(key, seen):
            continue
        seen.add(key)
        lines.append(line)

    return "\n".join(lines).strip()


def merge_frame_ocr_sources(paddle_text: str | None, screenpipe_text: str | None) -> str:
    """Merge PaddleOCR + Screenpipe/Tesseract text, deduplicating lines."""
    sources = [text for text in (paddle_text, screenpipe_text) if text and text.strip()]
    return merge_cleaned_texts(sources)


def merge_cleaned_texts(texts: list[str]) -> str:
    """Merge multiple cleaned OCR texts, deduplicating lines across frames."""
    merged: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for line in clean_ocr_text(text).splitlines():
            key = line.casefold()
            if key in seen or _is_subsumed_by_seen(key, seen):
                continue
            seen.add(key)
            merged.append(line)
    return "\n".join(merged).strip()


def _normalize_line(line: str) -> str:
    normalized = unicodedata.normalize("NFKC", line)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_noise_line(line: str) -> bool:
    if _NOISE_LINE.match(line):
        return True
    if _TIME_ONLY.match(line):
        return True
    if len(line) < _MIN_MEANINGFUL_LEN:
        return True
    if _UI_CHROME.match(line):
        return True
    alpha = sum(1 for c in line if c.isalpha())
    if len(line) >= 6 and alpha / len(line) < 0.25:
        return True
    if len(line) <= 4 and not alpha:
        return True
    return False


def _is_subsumed_by_seen(key: str, seen: set[str]) -> bool:
    """Drop lines already covered by a longer line (common OCR duplication)."""
    for existing in seen:
        if key != existing and (key in existing or existing in key):
            shorter, longer = (key, existing) if len(key) < len(existing) else (existing, key)
            if len(shorter) >= 8 and shorter in longer:
                return len(key) < len(existing)
    return False
