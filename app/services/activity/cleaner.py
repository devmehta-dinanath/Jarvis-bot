import re
import unicodedata


_NOISE_LINE = re.compile(
    r"^[\s\d\-_|•·.…]+$|^(ok|cancel|close|minimize|maximize|file|edit|view|help)$",
    re.IGNORECASE,
)


def clean_ocr_text(text: str | None) -> str:
    """Normalize OCR output: strip noise, dedupe lines, collapse whitespace."""
    if not text or not text.strip():
        return ""

    lines: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        if not line or _NOISE_LINE.match(line):
            continue
        key = line.casefold()
        if key in seen:
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
            if key in seen:
                continue
            seen.add(key)
            merged.append(line)
    return "\n".join(merged).strip()


def _normalize_line(line: str) -> str:
    normalized = unicodedata.normalize("NFKC", line)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized
