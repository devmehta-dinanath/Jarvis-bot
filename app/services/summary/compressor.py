from datetime import datetime

from app import models
from app.config import (
    SUMMARY_MAX_BATCHES_PER_HOUR,
    SUMMARY_MAX_CHUNK_PREVIEW_CHARS,
    SUMMARY_MAX_INPUT_CHARS,
)
from app.services.summary.timezone_utils import naive_utc_to_local


def chunk_has_summarizable_text(chunk: models.ActivityChunk) -> bool:
    cleaned = (chunk.cleaned_text or "").strip()
    transcript = (chunk.transcript_text or "").strip()
    return bool(cleaned or transcript)


def chunk_end(chunk: models.ActivityChunk) -> datetime:
    return chunk.end_timestamp or chunk.timestamp


def _text_excerpt(text: str, preview_chars: int) -> str:
    normalized = text.replace("\n", " ").strip()
    if preview_chars <= 0 or len(normalized) <= preview_chars:
        return normalized
    return normalized[:preview_chars] + "…"


def format_chunk_line(
    chunk: models.ActivityChunk,
    *,
    preview_chars: int = SUMMARY_MAX_CHUNK_PREVIEW_CHARS,
) -> str:
    start = naive_utc_to_local(chunk.timestamp).strftime("%H:%M")
    end = naive_utc_to_local(chunk_end(chunk)).strftime("%H:%M")
    app = chunk.app_name or "unknown"
    category = chunk.category
    parts: list[str] = []

    cleaned = (chunk.cleaned_text or "").strip()
    if cleaned:
        parts.append(_text_excerpt(cleaned, preview_chars))

    transcript = (chunk.transcript_text or "").strip()
    if transcript:
        parts.append(f"[transcript] {_text_excerpt(transcript, preview_chars)}")

    body = " | ".join(parts) if parts else "(no text)"
    return f"[{start}-{end}] {app} | {category} | {body}"


def _sorted_usable_chunks(chunks: list[models.ActivityChunk]) -> list[models.ActivityChunk]:
    usable = [chunk for chunk in chunks if chunk_has_summarizable_text(chunk)]
    usable.sort(key=lambda c: (c.timestamp, c.id))
    return usable


def iter_chunk_batches(
    chunks: list[models.ActivityChunk],
    *,
    max_chars: int = SUMMARY_MAX_INPUT_CHARS,
) -> list[list[models.ActivityChunk]]:
    """Split ALL hour chunks into consecutive batches — nothing dropped."""
    usable = _sorted_usable_chunks(chunks)
    if not usable:
        return []

    batches: list[list[models.ActivityChunk]] = []
    current: list[models.ActivityChunk] = []
    current_len = 0

    for chunk in usable:
        line = format_chunk_line(chunk)
        line_len = len(line) + (1 if current else 0)

        if line_len > max_chars and not current:
            batches.append([chunk])
            continue

        if current and current_len + line_len > max_chars:
            batches.append(current)
            current = [chunk]
            current_len = len(line)
        else:
            current.append(chunk)
            current_len += line_len

    if current:
        batches.append(current)

    return batches


def format_chunks_batch(chunks: list[models.ActivityChunk]) -> str:
    return "\n".join(format_chunk_line(chunk) for chunk in chunks)


def chunk_batches_as_text(
    chunks: list[models.ActivityChunk],
    *,
    max_chars: int = SUMMARY_MAX_INPUT_CHARS,
    max_batches: int = SUMMARY_MAX_BATCHES_PER_HOUR,
) -> list[str]:
    """Return one OCR text block per API batch; every chunk appears exactly once."""
    effective_max = max_chars
    batches = iter_chunk_batches(chunks, max_chars=effective_max)

    if max_batches > 0:
        while len(batches) > max_batches and effective_max < max_chars * 10:
            effective_max = int(effective_max * 1.5)
            batches = iter_chunk_batches(chunks, max_chars=effective_max)

    return [format_chunks_batch(batch) for batch in batches]
