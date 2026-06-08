import json
import logging

from sqlalchemy.orm import Session

from app import models
from app.services.activity.categories import ActivityCategory
from app.services.activity.classifier import classify_activity
from app.config import SAVE_ACTIVITY_JSON_FILES
from app.services.media.storage import get_recording_paths
from app.services.meetings.transcript import sync_meeting_transcript

logger = logging.getLogger(__name__)


def reclassify_activity_chunk(chunk: models.ActivityChunk, db: Session) -> models.ActivityChunk:
    """Re-run category detection and refresh meeting transcript if needed."""
    previous = chunk.category
    category = classify_activity(
        app_name=chunk.app_name,
        window_name=chunk.window_name,
        browser_url=chunk.browser_url,
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
