import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app import models
from app.services.activity.chunker import FrameChunk, chunk_frames
from app.services.activity.classifier import classify_activity
from app.config import (
    CHROMA_ENABLED,
    SAVE_ACTIVITY_JSON_FILES,
    SCREENPIPE_API_URL,
    SCREENPIPE_ENABLED,
    is_client_role,
)
from app.services.activity.cleaner import merge_cleaned_texts, merge_frame_ocr_sources
from app.services.activity.metadata import merge_metadata
from app.services.screenpipe.client import ScreenpipeApiError, fetch_frame_ocr_text
from app.frame_cleanup import enforce_frame_image_retention
from app.recording_paths import get_recording_paths
from app.services.activity.categories import ActivityCategory
from app.services.meetings.transcript import sync_meeting_transcript
from app.services.vector.store import upsert_activity_chunk

logger = logging.getLogger(__name__)

_GROUP_PREVIEW_CHARS = 120


def _log_activity_group(
    chunk: models.ActivityChunk,
    *,
    recording_id: int,
    frame_ids: list[int],
    indexed: bool,
) -> None:
    cleaned = (chunk.cleaned_text or "").strip()
    preview = cleaned.replace("\n", " ")
    if len(preview) > _GROUP_PREVIEW_CHARS:
        preview = preview[:_GROUP_PREVIEW_CHARS] + "…"
    logger.info(
        "[GROUP] chunk=%s recording=%s category=%s frames=[%s] app=%r window=%r "
        "paddle_chars=%s screenpipe_chars=%s merged_chars=%s indexed=%s | %s",
        chunk.id,
        recording_id,
        chunk.category,
        ", ".join(str(fid) for fid in frame_ids),
        chunk.app_name,
        chunk.window_name,
        getattr(chunk, "_paddle_chars", 0),
        getattr(chunk, "_screenpipe_chars", 0),
        len(cleaned),
        indexed,
        preview or "(empty)",
    )


def process_recording_activity(recording: models.Recording, db: Session) -> list[models.ActivityChunk]:
    """Clean, chunk, and classify OCR'd frames for a recording."""
    frames = (
        db.query(models.Frame)
        .filter(
            models.Frame.recording_id == recording.id,
            models.Frame.ocr_status == "done",
            models.Frame.activity_status == "pending",
        )
        .order_by(models.Frame.frame_index.asc())
        .all()
    )
    if not frames:
        return []

    for frame in frames:
        _ensure_screenpipe_ocr(frame)
        _enrich_frame_metadata(frame)

    db.commit()
    frame_chunks = chunk_frames(frames)
    created: list[models.ActivityChunk] = []

    for frame_chunk in frame_chunks:
        chunk, raw_chars, cleaned_chars, frame_ids = _build_activity_chunk(
            recording.id, frame_chunk
        )
        db.add(chunk)
        created.append((chunk, frame_ids, raw_chars, cleaned_chars))
        for frame in frame_chunk.frames:
            frame.activity_status = "processed"

    db.commit()
    indexed = 0
    transcripts_synced = 0
    for chunk, frame_ids, _raw_chars, _cleaned_chars in created:
        db.refresh(chunk)
        if SAVE_ACTIVITY_JSON_FILES:
            _write_activity_file(recording.id, chunk)
        if chunk.category == ActivityCategory.MEETINGS.value:
            sync_meeting_transcript(chunk, db)
            if chunk.transcript_status in {"synced", "empty"}:
                transcripts_synced += 1
        chunk_indexed = False
        if is_client_role():
            chunk.sync_status = "pending"
            chunk.client_chunk_id = chunk.id
        elif CHROMA_ENABLED:
            chunk_indexed = upsert_activity_chunk(
                chunk_id=chunk.id,
                recording_id=recording.id,
                cleaned_text=chunk.cleaned_text or "",
                app_name=chunk.app_name,
                window_name=chunk.window_name,
                browser_url=chunk.browser_url,
                category=chunk.category,
                timestamp=chunk.timestamp.isoformat(),
                frame_ids=frame_ids,
                paddle_chars=getattr(chunk, "_paddle_chars", 0),
                screenpipe_chars=getattr(chunk, "_screenpipe_chars", 0),
            )
            if chunk_indexed:
                indexed += 1
                enforce_frame_image_retention(db, recording_id=recording.id)
        _log_activity_group(
            chunk,
            recording_id=recording.id,
            frame_ids=frame_ids,
            indexed=chunk_indexed,
        )

    db.commit()
    chunks = [chunk for chunk, _, _, _ in created]
    logger.debug(
        "[ACTIVITY] Recording %s: classified %s frame(s) into %s chunk(s), "
        "indexed %s in Chroma, synced %s meeting transcript(s)",
        recording.id,
        len(frames),
        len(chunks),
        indexed,
        transcripts_synced,
    )
    return chunks


def _ensure_screenpipe_ocr(frame: models.Frame) -> None:
    if not SCREENPIPE_ENABLED or frame.screenpipe_frame_id is None:
        return
    if frame.screenpipe_ocr_text and frame.screenpipe_ocr_text.strip():
        return
    try:
        text = fetch_frame_ocr_text(SCREENPIPE_API_URL, frame.screenpipe_frame_id)
        if text:
            frame.screenpipe_ocr_text = text
    except ScreenpipeApiError:
        logger.debug(
            "Screenpipe OCR unavailable for frame id=%s screenpipe_id=%s",
            frame.id,
            frame.screenpipe_frame_id,
            exc_info=True,
        )
    except Exception:
        logger.debug("Screenpipe OCR fetch failed for frame id=%s", frame.id, exc_info=True)


def _enrich_frame_metadata(frame: models.Frame) -> None:
    merged = merge_metadata(
        app_name=frame.app_name,
        window_name=frame.window_name,
        browser_url=frame.browser_url,
        text=merge_frame_ocr_sources(frame.ocr_text, frame.screenpipe_ocr_text),
    )
    if merged["app_name"] and merged["app_name"] != frame.app_name:
        frame.app_name = merged["app_name"]
    if merged["window_name"] and merged["window_name"] != frame.window_name:
        frame.window_name = merged["window_name"]
    if merged["browser_url"] and merged["browser_url"] != frame.browser_url:
        frame.browser_url = merged["browser_url"]


def _build_activity_chunk(
    recording_id: int,
    frame_chunk: FrameChunk,
) -> tuple[models.ActivityChunk, int, int, list[int]]:
    paddle_chars = 0
    screenpipe_chars = 0
    merged_frame_texts: list[str] = []
    for frame in frame_chunk.frames:
        paddle_chars += len(frame.ocr_text or "")
        screenpipe_chars += len(frame.screenpipe_ocr_text or "")
        merged = merge_frame_ocr_sources(frame.ocr_text, frame.screenpipe_ocr_text)
        if merged:
            merged_frame_texts.append(merged)

    raw_chars = paddle_chars + screenpipe_chars
    cleaned_text = merge_cleaned_texts(merged_frame_texts)
    cleaned_chars = len(cleaned_text)
    metadata = merge_metadata(
        app_name=frame_chunk.app_name,
        window_name=frame_chunk.window_name,
        browser_url=frame_chunk.browser_url,
        text=cleaned_text,
    )
    category = classify_activity(
        app_name=metadata["app_name"],
        window_name=metadata["window_name"],
        browser_url=metadata["browser_url"],
        text=cleaned_text,
    )
    frame_ids = [frame.id for frame in frame_chunk.frames]

    chunk = models.ActivityChunk(
        recording_id=recording_id,
        app_name=metadata["app_name"],
        window_name=metadata["window_name"],
        browser_url=metadata["browser_url"],
        category=category.value,
        timestamp=frame_chunk.timestamp,
        end_timestamp=frame_chunk.end_timestamp,
        cleaned_text=cleaned_text or None,
        frame_ids=json.dumps(frame_ids),
        frame_count=len(frame_ids),
    )
    chunk._paddle_chars = paddle_chars  # type: ignore[attr-defined]
    chunk._screenpipe_chars = screenpipe_chars  # type: ignore[attr-defined]
    return chunk, raw_chars, cleaned_chars, frame_ids


def _write_activity_file(recording_id: int, chunk: models.ActivityChunk) -> None:
    paths = get_recording_paths(recording_id)
    activity_dir = paths.root / "activity"
    activity_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": chunk.id,
        "app_name": chunk.app_name,
        "category": chunk.category,
        "timestamp": chunk.timestamp.isoformat(),
        "end_timestamp": chunk.end_timestamp.isoformat() if chunk.end_timestamp else None,
        "window_name": chunk.window_name, 
        "browser_url": chunk.browser_url, 
        "frame_count": chunk.frame_count, 
        "cleaned_text": chunk.cleaned_text, 
        "transcript_status": chunk.transcript_status, 
        "transcript_text": chunk.transcript_text, 
    }
    path = activity_dir / f"chunk_{chunk.id:06d}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
