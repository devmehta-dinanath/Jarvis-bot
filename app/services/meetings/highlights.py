import logging
from datetime import datetime, timedelta, timezone

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app import models
from app.config import CALENDAR_DEFAULT_TIMEZONE, MEETING_HIGHLIGHT_DEFAULT_DURATION_MINUTES
from app.services.google_calendar.schemas import EventCreate, EventDateTime
from app.services.google_calendar.service import google_calendar_service
from app.services.meetings.transcript import excerpt_for_time_range, sync_meeting_transcript

logger = logging.getLogger(__name__)


def create_meeting_highlight(
    db: Session,
    chunk: models.ActivityChunk,
    *,
    title: str,
    notes: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_minutes: int | None = None,
) -> models.MeetingHighlight:
    if chunk.category != "meetings":
        raise ValueError("Highlights can only be created for meeting activity chunks.")

    duration = duration_minutes or MEETING_HIGHLIGHT_DEFAULT_DURATION_MINUTES
    highlight_start = started_at or chunk.timestamp
    highlight_end = ended_at or (highlight_start + timedelta(minutes=duration))

    if highlight_end <= highlight_start:
        raise ValueError("ended_at must be after started_at.")

    if chunk.transcript_status in {"pending", "failed", "empty"}:
        sync_meeting_transcript(chunk, db)

    segments = (
        db.query(models.MeetingTranscriptSegment)
        .filter(models.MeetingTranscriptSegment.activity_chunk_id == chunk.id)
        .order_by(models.MeetingTranscriptSegment.segment_index.asc())
        .all()
    )
    excerpt = excerpt_for_time_range(segments, highlight_start, highlight_end)

    highlight = models.MeetingHighlight(
        activity_chunk_id=chunk.id,
        recording_id=chunk.recording_id,
        title=title.strip(),
        notes=notes.strip() if notes else None,
        started_at=highlight_start,
        ended_at=highlight_end,
        transcript_excerpt=excerpt or None,
        status="draft",
    )
    db.add(highlight)
    db.commit()
    db.refresh(highlight)
    return highlight


def add_highlight_to_calendar(
    db: Session,
    highlight: models.MeetingHighlight,
    *,
    calendar_id: str | None = None,
    conference: bool = False,
) -> models.MeetingHighlight:
    chunk = highlight.activity_chunk
    description_parts = []
    if highlight.notes:
        description_parts.append(highlight.notes)
    if highlight.transcript_excerpt:
        description_parts.append("Transcript excerpt:\n" + highlight.transcript_excerpt)
    if chunk.window_name:
        description_parts.append(f"Meeting window: {chunk.window_name}")
    if chunk.browser_url:
        description_parts.append(f"URL: {chunk.browser_url}")
    description_parts.append(f"Activity chunk #{chunk.id} (recording #{chunk.recording_id})")

    payload = EventCreate(
        summary=highlight.title,
        description="\n\n".join(description_parts),
        location=chunk.window_name,
        start=EventDateTime(
            date_time=_to_calendar_iso(highlight.started_at),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        end=EventDateTime(
            date_time=_to_calendar_iso(highlight.ended_at),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        conference=conference,
    )

    try:
        event = google_calendar_service.create_event(payload, calendar_id=calendar_id)
    except PermissionError:
        raise
    except HttpError:
        raise

    highlight.calendar_event_id = event.get("id")
    highlight.calendar_html_link = event.get("htmlLink")
    highlight.status = "scheduled"
    highlight.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(highlight)

    logger.info(
        "[MEETING] Created calendar event %s for highlight %s",
        highlight.calendar_event_id,
        highlight.id,
    )
    return highlight


def _to_calendar_iso(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(microsecond=0).isoformat() + "Z"
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")
