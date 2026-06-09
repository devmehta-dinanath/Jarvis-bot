import logging
import threading

from app import models
from app.config import ACTIVITY_POLL_INTERVAL_SECONDS
from app.database import SessionLocal
from app.services.activity.processor import process_recording_activity
from app.services.media.cleanup import enforce_frame_image_retention

logger = logging.getLogger(__name__)


class ActivityClassificationService:
    """Background worker: classifies OCR'd frames into tagged activity chunks."""

    def __init__(self, poll_interval_seconds: float = ACTIVITY_POLL_INTERVAL_SECONDS) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="activity-classifier",
            daemon=True,
        )
        self._thread.start()
        logger.info("[ACTIVITY] Classification worker started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[ACTIVITY] Classification worker stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                recording_id = (
                    db.query(models.Frame.recording_id)
                    .filter(
                        models.Frame.ocr_status == "done",
                        models.Frame.activity_status == "pending",
                    )
                    .order_by(models.Frame.id.asc())
                    .limit(1)
                    .scalar()
                )
                if recording_id is None:
                    enforce_frame_image_retention(db)
                    db.close()
                    self._stop_event.wait(self.poll_interval_seconds)
                    continue

                recording = (
                    db.query(models.Recording)
                    .filter(models.Recording.id == recording_id)
                    .first()
                )
                if recording is None:
                    db.close()
                    self._stop_event.wait(self.poll_interval_seconds)
                    continue

                pending_frames = (
                    db.query(models.Frame.id, models.Frame.frame_index)
                    .filter(
                        models.Frame.recording_id == recording_id,
                        models.Frame.ocr_status == "done",
                        models.Frame.activity_status == "pending",
                    )
                    .order_by(models.Frame.frame_index.asc())
                    .all()
                )
                if pending_frames:
                    frame_ids = ", ".join(str(row.id) for row in pending_frames)
                    indexes = ", ".join(str(row.frame_index) for row in pending_frames)
                    logger.debug(
                        "[ACTIVITY] Classifying recording=%s pending_frames=%s "
                        "frame_ids=[%s] indexes=[%s]",
                        recording_id,
                        len(pending_frames),
                        frame_ids,
                        indexes,
                    )

                process_recording_activity(recording, db)
            except Exception:
                logger.exception("[ACTIVITY] Classification failed")
                db.rollback()
            finally:
                db.close()

            if self._stop_event.is_set():
                break
            self._stop_event.wait(self.poll_interval_seconds)
