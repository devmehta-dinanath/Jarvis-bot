import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app import models
from app.services.activity.chunker import FrameChunk, chunk_frames
from app.services.activity.classifier import classify_activity
from app.services.activity.cleaner import merge_cleaned_texts
from app.services.activity.metadata import merge_metadata
from app.config import SAVE_ACTIVITY_JSON_FILES
from app.services.media.storage import get_recording_paths
from app.services.activity.categories import ActivityCategory
from app.services.meetings.transcript import sync_meeting_transcript
from app.services.vector.store import upsert_activity_chunk

logger = logging.getLogger(__name__)


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
    for chunk, frame_ids, raw_chars, cleaned_chars in created:
        db.refresh(chunk)
        frame_range = (
            f"{frame_ids[0]}..{frame_ids[-1]}" if len(frame_ids) > 1 else str(frame_ids[0])
        )
        logger.info(
            "[ACTIVITY] Chunk id=%s recording=%s category=%s frame_count=%s "
            "frame_ids=[%s] frame_db_ids=%s raw=%s chars cleaned=%s chars app=%r window=%r",
            chunk.id,
            recording.id,
            chunk.category,
            chunk.frame_count,
            ", ".join(str(fid) for fid in frame_ids),
            frame_range,
            raw_chars,
            cleaned_chars,
            chunk.app_name,
            chunk.window_name,
        )
        if SAVE_ACTIVITY_JSON_FILES:
            _write_activity_file(recording.id, chunk)
        if chunk.category == ActivityCategory.MEETINGS.value:
            sync_meeting_transcript(chunk, db)
            if chunk.transcript_status in {"synced", "empty"}:
                transcripts_synced += 1
        if upsert_activity_chunk(
            chunk_id=chunk.id,
            recording_id=recording.id,
            cleaned_text=chunk.cleaned_text or "",
            app_name=chunk.app_name,
            window_name=chunk.window_name,
            browser_url=chunk.browser_url,
            category=chunk.category,
            timestamp=chunk.timestamp.isoformat(),
            frame_ids=frame_ids,
        ):
            indexed += 1

    chunks = [chunk for chunk, _, _, _ in created]
    logger.info(
        "[ACTIVITY] Recording %s: classified %s frame(s) into %s chunk(s), "
        "indexed %s in Chroma, synced %s meeting transcript(s)",
        recording.id,
        len(frames),
        len(chunks),
        indexed,
        transcripts_synced,
    )
    return chunks


def _enrich_frame_metadata(frame: models.Frame) -> None:
    merged = merge_metadata(
        app_name=frame.app_name,
        window_name=frame.window_name,
        browser_url=frame.browser_url,
        text=frame.ocr_text,
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
    texts = [frame.ocr_text or "" for frame in frame_chunk.frames]
    raw_chars = sum(len(text) for text in texts)
    cleaned_text = merge_cleaned_texts(texts)
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
