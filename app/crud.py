from sqlalchemy.orm import Session

from app import models, schemas


def create_recording(db: Session, payload: schemas.RecordingCreate) -> models.Recording:
    data = payload.model_dump()
    data.setdefault("status", "queued")
    recording = models.Recording(**data)
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return recording


def list_recordings(db: Session) -> list[models.Recording]:
    return db.query(models.Recording).order_by(models.Recording.created_at.desc()).all()


def get_recording(db: Session, recording_id: int) -> models.Recording | None:
    return db.query(models.Recording).filter(models.Recording.id == recording_id).first()


def get_recording_with_frames(db: Session, recording_id: int) -> models.Recording | None:
    return db.query(models.Recording).filter(models.Recording.id == recording_id).first()


def list_frames(db: Session, recording_id: int) -> list[models.Frame]:
    return (
        db.query(models.Frame)
        .filter(models.Frame.recording_id == recording_id)
        .order_by(models.Frame.frame_index.asc())
        .all()
    )
                                                          

def list_activity_chunks(
    db: Session,
    recording_id: int | None = None,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[models.ActivityChunk], int]:
    query = db.query(models.ActivityChunk)
    if recording_id is not None:
        query = query.filter(models.ActivityChunk.recording_id == recording_id)
    if category is not None:
        query = query.filter(models.ActivityChunk.category == category)

    total = query.count()
    items = (
        query.order_by(models.ActivityChunk.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def get_activity_chunk(db: Session, chunk_id: int) -> models.ActivityChunk | None:
    return db.query(models.ActivityChunk).filter(models.ActivityChunk.id == chunk_id).first()


def list_meeting_transcript_segments(
    db: Session,
    activity_chunk_id: int,
) -> list[models.MeetingTranscriptSegment]:
    return (
        db.query(models.MeetingTranscriptSegment)
        .filter(models.MeetingTranscriptSegment.activity_chunk_id == activity_chunk_id)
        .order_by(models.MeetingTranscriptSegment.segment_index.asc())
        .all()
    )


def list_meeting_highlights(
    db: Session,
    activity_chunk_id: int | None = None,
) -> list[models.MeetingHighlight]:
    query = db.query(models.MeetingHighlight)
    if activity_chunk_id is not None:
        query = query.filter(models.MeetingHighlight.activity_chunk_id == activity_chunk_id)
    return query.order_by(models.MeetingHighlight.started_at.asc()).all()


def get_meeting_highlight(db: Session, highlight_id: int) -> models.MeetingHighlight | None:
    return (
        db.query(models.MeetingHighlight)
        .filter(models.MeetingHighlight.id == highlight_id)
        .first()
    )


def update_recording(
    db: Session,
    recording: models.Recording,
    payload: schemas.RecordingUpdate,
) -> models.Recording:
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(recording, field, value)

    db.add(recording)
    db.commit()
    db.refresh(recording)
    return recording


def delete_recording(db: Session, recording: models.Recording) -> None:
    db.delete(recording)
    db.commit()
