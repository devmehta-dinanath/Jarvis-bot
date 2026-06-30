import json
import logging

from sqlalchemy.orm import Session

from app import models
from app.services.activity.categories import ActivityCategory
from app.services.activity.classifier import classify_activity
from app.services.activity.metadata import merge_metadata
from app.config import SAVE_ACTIVITY_JSON_FILES
from app.recording_paths import get_recording_paths
from app.services.meetings.transcript import sync_meeting_transcript
from app.services.vector.store import upsert_activity_chunk

logger = logging.getLogger(__name__)


def reclassify_activity_chunk(chunk: models.ActivityChunk, db: Session) -> models.ActivityChunk:
    """Re-run category detection and refresh meeting transcript if needed."""
    previous = chunk.category
    metadata = merge_metadata(
        app_name=chunk.app_name,
        window_name=chunk.window_name,
        browser_url=chunk.browser_url,
        text=chunk.cleaned_text,
    )
    chunk.app_name = metadata["app_name"]
    chunk.window_name = metadata["window_name"]
    chunk.browser_url = metadata["browser_url"]
    category = classify_activity(
        app_name=metadata["app_name"],
        window_name=metadata["window_name"],
        browser_url=metadata["browser_url"],
        text=chunk.cleaned_text,
    )
    chunk.category = category.value

    if chunk.category == ActivityCategory.MEETINGS.value:
        sync_meeting_transcript(chunk, db, force=True)
    elif previous == ActivityCategory.MEETINGS.value:
        chunk.transcript_status = "skipped"

    db.commit()
    db.refresh(chunk)
    if SAVE_ACTIVITY_JSON_FILES:
        _write_activity_file(chunk)

    upsert_activity_chunk(
        chunk_id=chunk.id, 
        recording_id=chunk.recording_id, 
        cleaned_text=chunk.cleaned_text or "", 
        app_name=chunk.app_name, 
        window_name=chunk.window_name, 
        browser_url=chunk.browser_url,
        category=chunk.category, 
        timestamp=chunk.timestamp.isoformat(),
    )

    logger.info(
        "[ACTIVITY] Reclassified chunk %s: %s -> %s (transcript=%s)",
        chunk.id,
        previous,
        chunk.category,
        chunk.transcript_status,
    )
    return chunk


def _write_activity_file(chunk: models.ActivityChunk) -> None:
    paths = get_recording_paths(chunk.recording_id)
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
        "transcript_error": chunk.transcript_error,
    }
    path = activity_dir / f"chunk_{chunk.id:06d}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
