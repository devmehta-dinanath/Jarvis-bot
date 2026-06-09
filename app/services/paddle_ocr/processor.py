import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.config import SAVE_FRAME_OCR_FILES, SCREENPIPE_API_URL, SCREENPIPE_ENABLED
from app.services.activity.cleaner import merge_frame_ocr_sources
from app.services.media.storage import get_recording_paths
from app.services.paddle_ocr.engine import OCRDependencyError, extract_text
from app.services.screenpipe.client import ScreenpipeApiError, fetch_frame_ocr_text

logger = logging.getLogger(__name__)


def _fetch_screenpipe_ocr(frame: models.Frame) -> None:
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
        logger.debug(
            "Screenpipe OCR fetch failed for frame id=%s",
            frame.id,
            exc_info=True,
        )


def _ocr_output_path(recording_id: int, frame_index: int) -> Path:
    paths = get_recording_paths(recording_id)
    paths.ocr.mkdir(parents=True, exist_ok=True)
    return paths.ocr / f"frame_{frame_index:06d}.txt"


def process_frame(frame: models.Frame, recording: models.Recording, db: Session) -> None:
    sp_id = frame.screenpipe_frame_id or "n/a"
    logger.info(
        "[OCR] Processing frame id=%s index=%s screenpipe_id=%s recording=%s path=%s",
        frame.id,
        frame.frame_index,
        sp_id,
        recording.id,
        frame.file_path,
    )
    frame.ocr_status = "processing"
    db.commit()
    try:
        frame.ocr_text = extract_text(frame.file_path)
        _fetch_screenpipe_ocr(frame)
        frame.ocr_status = "done"
        frame.processed_at = datetime.utcnow()
        recording.ocr_completed_frames += 1

        merged_preview = merge_frame_ocr_sources(
            frame.ocr_text,
            frame.screenpipe_ocr_text,
        ).replace("\n", " ")[:80]
        if SAVE_FRAME_OCR_FILES:
            ocr_path = _ocr_output_path(recording.id, frame.frame_index)
            ocr_path.write_text(frame.ocr_text or "", encoding="utf-8")
            saved_to = str(ocr_path)
        else:
            saved_to = "sqlite only (SAVE_FRAME_OCR_FILES=false)"
        logger.info(
            "[OCR] Done frame id=%s index=%s screenpipe_id=%s recording=%s -> %s "
            "paddle=%s chars screenpipe=%s chars merged_preview: %s",
            frame.id,
            frame.frame_index,
            sp_id,
            recording.id,
            saved_to,
            len(frame.ocr_text or ""),
            len(frame.screenpipe_ocr_text or ""),
            merged_preview or "(empty)",
        )
    except OCRDependencyError as exc:
        frame.ocr_status = "failed"
        frame.error_message = str(exc)
        frame.processed_at = datetime.utcnow()
        logger.error(
            "[OCR] Failed frame id=%s index=%s screenpipe_id=%s recording=%s: %s",
            frame.id,
            frame.frame_index,
            sp_id,
            recording.id,
            exc,
        )
    except Exception as exc:  # pragma: no cover - integration path
        frame.ocr_status = "failed"
        frame.error_message = str(exc)
        frame.processed_at = datetime.utcnow()
        logger.error(
            "[OCR] Failed frame id=%s index=%s screenpipe_id=%s recording=%s: %s",
            frame.id,
            frame.frame_index,
            sp_id,
            recording.id,
            exc,
        )
    finally:
        recording.processed_frames += 1
        db.commit()
        if recording.processed_frames == recording.total_frames or recording.processed_frames % 10 == 0:
            logger.info(
                "[OCR] Recording %s progress: %s/%s processed, %s OCR done",
                recording.id,
                recording.processed_frames,
                recording.total_frames,
                recording.ocr_completed_frames,
            )


def run_ocr_for_recording(recording: models.Recording, db: Session) -> None:
    recording.status = "running_ocr"
    db.commit()

    frames = (
        db.query(models.Frame)
        .filter(models.Frame.recording_id == recording.id)
        .order_by(models.Frame.frame_index.asc())
        .all()
    )

    for frame in frames:
        if frame.ocr_status == "done":
            continue
        process_frame(frame, recording, db)
