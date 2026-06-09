from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import JARVIS_DATABASE_FILENAME
from app.database import DATABASE_URL, sqlite_database_path


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
                                                          
5
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


def _database_label() -> str:
    if DATABASE_URL.startswith("sqlite"):
        return JARVIS_DATABASE_FILENAME
    if DATABASE_URL.startswith("postgresql"):
        return "postgresql"
    if DATABASE_URL.startswith("mysql"):
        return "mysql"
    return "external"


def _database_path() -> str | None:
    db_path = sqlite_database_path()
    return str(db_path) if db_path is not None else None


def _frame_ocr_summary(frame: models.Frame) -> schemas.FrameOcrSummary:
    paddle = frame.ocr_text or ""
    screenpipe = frame.screenpipe_ocr_text or ""
    return schemas.FrameOcrSummary(
        id=frame.id,
        frame_index=frame.frame_index,
        screenpipe_frame_id=frame.screenpipe_frame_id,
        app_name=frame.app_name,
        window_name=frame.window_name,
        ocr_status=frame.ocr_status,
        activity_status=frame.activity_status,
        paddle_chars=len(paddle),
        screenpipe_chars=len(screenpipe),
        image_on_disk=Path(frame.file_path).is_file(),
        paddle_preview=paddle.replace("\n", " ")[:200],
        screenpipe_preview=screenpipe.replace("\n", " ")[:200],
        captured_at=frame.captured_at,
        processed_at=frame.processed_at,
    )


def get_recording_pipeline_stats(
    db: Session,
    recording_id: int,
    *,
    recent_frames: int = 5,
    chroma_embeddings: int = 0,
) -> schemas.RecordingPipelineStatsResponse | None:
    recording = get_recording(db, recording_id)
    if recording is None:
        return None

    frames = list_frames(db, recording_id)
    ocr_counts: dict[str, int] = {}
    activity_counts: dict[str, int] = {}
    images_on_disk = 0
    for frame in frames:
        ocr_counts[frame.ocr_status] = ocr_counts.get(frame.ocr_status, 0) + 1
        activity_counts[frame.activity_status] = activity_counts.get(frame.activity_status, 0) + 1
        if Path(frame.file_path).is_file():
            images_on_disk += 1

    category_rows = (
        db.query(models.ActivityChunk.category, func.count(models.ActivityChunk.id))
        .filter(models.ActivityChunk.recording_id == recording_id)
        .group_by(models.ActivityChunk.category)
        .order_by(func.count(models.ActivityChunk.id).desc())
        .all()
    )
    chunks_total = sum(count for _, count in category_rows)

    recent = (
        db.query(models.Frame)
        .filter(models.Frame.recording_id == recording_id)
        .order_by(models.Frame.frame_index.desc())
        .limit(min(max(recent_frames, 1), 50))
        .all()
    )

    return schemas.RecordingPipelineStatsResponse(
        recording_id=recording.id,
        title=recording.title,
        status=recording.status,
        database=_database_label(),
        database_path=_database_path(),
        total_frames=len(frames),
        ocr_done=ocr_counts.get("done", 0),
        ocr_queued=ocr_counts.get("queued", 0),
        ocr_processing=ocr_counts.get("processing", 0),
        ocr_failed=ocr_counts.get("failed", 0),
        activity_processed=activity_counts.get("processed", 0),
        activity_pending=activity_counts.get("pending", 0),
        images_on_disk=images_on_disk,
        activity_chunks_total=chunks_total,
        activity_chunks_by_category=[
            schemas.CategoryCount(category=category, count=count)
            for category, count in category_rows
        ],
        chroma_embeddings=chroma_embeddings,
        recent_frames=[_frame_ocr_summary(frame) for frame in reversed(recent)],
    )
