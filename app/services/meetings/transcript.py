import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import models
from app.config import (
    MEETING_AUDIO_SEARCH_LIMIT,
    MEETING_AUDIO_SEARCH_PADDING_SECONDS,
    MEETING_AUDIO_SYNC_ENABLED,
    SCREENPIPE_API_URL,
)
from app.services.activity.categories import ActivityCategory
from app.services.screenpipe.client import (
    ScreenpipeApiError,
    ensure_audio_capture,
    is_api_unreachable_error,
    list_audio_transcripts,
)

logger = logging.getLogger(__name__)


def sync_meeting_transcript(
    chunk: models.ActivityChunk,
    db: Session,
    *,
    force: bool = False,
) -> models.ActivityChunk:
    """Pull audio transcriptions from Screenpipe for a meeting activity chunk."""
    if chunk.category != ActivityCategory.MEETINGS.value:
        chunk.transcript_status = "skipped"
        db.commit()
        return chunk

    if not MEETING_AUDIO_SYNC_ENABLED:
        chunk.transcript_status = "skipped"
        db.commit()
        return chunk

    if chunk.transcript_status == "synced" and not force:
        return chunk

    previous_status = chunk.transcript_status
    chunk.transcript_status = "syncing"
    chunk.transcript_error = None
    db.commit()

    end_time = chunk.end_timestamp or datetime.utcnow()

    audio_bootstrap = ensure_audio_capture(SCREENPIPE_API_URL)
    bootstrap_message = str(audio_bootstrap.get("message") or "")
    if not audio_bootstrap.get("enabled"):
        logger.warning(
            "[MEETING] Audio not available for chunk %s: %s",
            chunk.id,
            bootstrap_message,
        )
        if is_api_unreachable_error(bootstrap_message):
            return _defer_transcript_sync(
                chunk,
                db,
                "Screenpipe API not ready yet; will retry when audio is available.",
            )

    try:
        segments = _fetch_all_audio_segments(
            start_time=chunk.timestamp,
            end_time=end_time,
            app_name=chunk.app_name,
        )
    except ScreenpipeApiError as exc:
        if is_api_unreachable_error(exc):
            return _defer_transcript_sync(chunk, db, str(exc))
        chunk.transcript_status = "failed"
        chunk.transcript_error = str(exc)
        db.commit()
        logger.warning("[MEETING] Transcript sync failed for chunk %s: %s", chunk.id, exc)
        return chunk
    except Exception as exc:  # pragma: no cover - integration path
        if is_api_unreachable_error(exc):
            return _defer_transcript_sync(chunk, db, str(exc))
        chunk.transcript_status = "failed"
        chunk.transcript_error = str(exc)
        db.commit()
        logger.exception("[MEETING] Transcript sync failed for chunk %s", chunk.id)
        return chunk

    db.query(models.MeetingTranscriptSegment).filter(
        models.MeetingTranscriptSegment.activity_chunk_id == chunk.id
    ).delete()

    merged_lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        speaker = segment.get("speaker")
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        prefix = f"{speaker}: " if speaker else ""
        merged_lines.append(f"{prefix}{text}")
        db.add(
            models.MeetingTranscriptSegment(
                activity_chunk_id=chunk.id,
                recording_id=chunk.recording_id,
                segment_index=index,
                text=text,
                speaker=speaker,
                started_at=segment["started_at"],
                screenpipe_chunk_id=segment.get("screenpipe_chunk_id"),
            )
        )

    chunk.transcript_text = "\n".join(merged_lines).strip() or None
    if chunk.transcript_text:
        chunk.transcript_status = "synced"
    elif chunk.cleaned_text and chunk.cleaned_text.strip():
        chunk.transcript_text = chunk.cleaned_text.strip()
        chunk.transcript_status = "ocr_fallback"
        chunk.transcript_error = (
            "No Screenpipe audio transcript available; using OCR text as fallback."
        )
        log = logger.debug if previous_status == "ocr_fallback" else logger.warning
        log(
            "[MEETING] No audio for chunk %s — using OCR fallback (%s chars)",
            chunk.id,
            len(chunk.transcript_text),
        )
    else:
        chunk.transcript_status = "empty"
        chunk.transcript_error = (
            "No audio transcript found. Enable Screenpipe audio (PulseAudio) and retry."
        )
    db.commit()
    db.refresh(chunk)

    logger.info(
        "[MEETING] Synced %s transcript segment(s) for chunk %s (status=%s)",
        len(merged_lines),
        chunk.id,
        chunk.transcript_status,
    )
    return chunk


def _defer_transcript_sync(
    chunk: models.ActivityChunk,
    db: Session,
    reason: str,
) -> models.ActivityChunk:
    """Keep or restore pending/ocr_fallback when Screenpipe is still starting."""
    cleaned = (chunk.cleaned_text or "").strip()
    if cleaned:
        chunk.transcript_text = cleaned
        chunk.transcript_status = "ocr_fallback"
        chunk.transcript_error = (
            f"{reason} Using OCR text until audio transcript is available."
        )
        logger.warning(
            "[MEETING] Screenpipe unavailable for chunk %s — OCR fallback (%s chars), "
            "will retry audio sync",
            chunk.id,
            len(cleaned),
        )
    else:
        chunk.transcript_status = "pending"
        chunk.transcript_error = reason
        logger.warning(
            "[MEETING] Screenpipe unavailable for chunk %s — deferred audio sync",
            chunk.id,
        )
    db.commit()
    db.refresh(chunk)
    return chunk


def _audio_search_window(
    start_time: datetime,
    end_time: datetime,
) -> tuple[datetime, datetime]:
    """Widen narrow single-frame windows so nearby audio segments can match."""
    pad = timedelta(seconds=MEETING_AUDIO_SEARCH_PADDING_SECONDS)
    start = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    end = end_time if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)
    if end <= start:
        end = start + timedelta(seconds=1)
    return start - pad, end + pad


def _fetch_all_audio_segments(
    *,
    start_time: datetime,
    end_time: datetime,
    app_name: str | None,
) -> list[dict]:
    window_start, window_end = _audio_search_window(start_time, end_time)
    collected = _fetch_audio_segments_in_window(
        start_time=window_start,
        end_time=window_end,
        app_name=app_name,
    )
    if not collected and app_name:
        collected = _fetch_audio_segments_in_window(
            start_time=window_start,
            end_time=window_end,
            app_name=None,
        )
    return collected


def _fetch_audio_segments_in_window(
    *,
    start_time: datetime,
    end_time: datetime,
    app_name: str | None,
) -> list[dict]:
    collected: list[dict] = []
    offset = 0
    seen_keys: set[tuple[str, str]] = set()

    while True:
        batch = list_audio_transcripts(
            SCREENPIPE_API_URL,
            start_time=start_time,
            end_time=end_time,
            app_name=app_name,
            limit=MEETING_AUDIO_SEARCH_LIMIT,
            offset=offset,
        )
        if not batch:
            break

        for segment in batch:
            key = (
                str(segment.get("screenpipe_chunk_id") or ""),
                (segment.get("text") or "")[:120],
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            collected.append(segment)

        if len(batch) < MEETING_AUDIO_SEARCH_LIMIT:
            break
        offset += MEETING_AUDIO_SEARCH_LIMIT

    collected.sort(key=lambda item: item["started_at"])
    return collected


def excerpt_for_time_range(
    segments: list[models.MeetingTranscriptSegment],
    started_at: datetime,
    ended_at: datetime,
) -> str:
    lines: list[str] = []
    for segment in segments:
        if started_at <= segment.started_at <= ended_at:
            prefix = f"{segment.speaker}: " if segment.speaker else ""
            lines.append(f"{prefix}{segment.text}")
    return "\n".join(lines).strip()
