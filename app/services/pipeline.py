from datetime import datetime

from app import models
from app.database import SessionLocal
from app.services.media.frames import extract_frames_from_video
from app.services.media.storage import ensure_recording_dirs
from app.services.paddle_ocr.processor import run_ocr_for_recording
from app.services.activity.processor import process_recording_activity
from app.services.screenpipe.capture import resolve_video_source


def process_recording_job(recording_id: int, frame_interval_seconds: float) -> None:
    db = SessionLocal()
    try:
        recording = db.query(models.Recording).filter(models.Recording.id == recording_id).first()
        if recording is None:
            return

        recording.started_at = datetime.utcnow()
        recording.status = "capturing"
        recording.error_message = None
        paths = ensure_recording_dirs(recording.id)
        recording.media_root = str(paths.root)
        recording.frames_dir = str(paths.frames)
        db.commit()

        video_path = resolve_video_source(recording, paths)
        db.commit()

        extract_frames_from_video(
            recording,
            video_path,
            paths.frames,
            frame_interval_seconds,
            db,
        )
        run_ocr_for_recording(recording, db)
        process_recording_activity(recording, db)

        recording.status = "completed"
        recording.completed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # pragma: no cover - integration pathv
        db.rollback()
        recording = db.query(models.Recording).filter(models.Recording.id == recording_id).first()
        if recording is not None:
            recording.status = "failed"
            recording.error_message = str(exc)
            recording.completed_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
