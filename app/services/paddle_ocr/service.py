import logging
import threading

from app import models
from app.config import OCR_POLL_INTERVAL_SECONDS
from app.database import SessionLocal
from app.services.paddle_ocr.engine import OCRDependencyError
from app.services.paddle_ocr.processor import process_frame

logger = logging.getLogger(__name__)


class PaddleOcrService:
    """Background OCR worker: picks queued frames and marks them done (text saved under media/.../ocr/)."""

    def __init__(self, poll_interval_seconds: float = OCR_POLL_INTERVAL_SECONDS) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ocr_disabled_logged = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._recover_stuck_frames()
        self._thread = threading.Thread(target=self._run_loop, name="paddle-ocr-worker", daemon=True)
        self._thread.start()
        logger.info("[OCR] Paddle OCR worker started (processing queued frames)")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Paddle OCR service stopped")

    def _recover_stuck_frames(self) -> None:
        """Re-queue frames left in processing after a crash or container restart."""
        db = SessionLocal()
        try:
            stuck = (
                db.query(models.Frame)
                .filter(
                    models.Frame.ocr_status == "processing",
                    models.Frame.processed_at.is_(None),
                )
                .all()
            )
            if not stuck:
                return
            for frame in stuck:
                frame.ocr_status = "queued"
                frame.error_message = None
            db.commit()
            logger.warning("[OCR] Re-queued %s frame(s) stuck in processing", len(stuck))
        except Exception:
            logger.exception("[OCR] Failed to recover stuck frames")
            db.rollback()
        finally:
            db.close()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                frame = (
                    db.query(models.Frame)
                    .filter(models.Frame.ocr_status == "queued")
                    .order_by(models.Frame.id.asc())
                    .first()
                )
                if frame is None:
                    db.close()
                    self._stop_event.wait(self.poll_interval_seconds)
                    continue

                recording = db.query(models.Recording).filter(models.Recording.id == frame.recording_id).first()
                if recording is None:
                    frame.ocr_status = "failed"
                    frame.error_message = "Recording not found"
                    db.commit()
                    continue

                if recording.status not in ("running_ocr", "capturing", "extracting_frames", "completed"):
                    recording.status = "running_ocr"
                    db.commit()

                queued_count = (
                    db.query(models.Frame)
                    .filter(
                        models.Frame.recording_id == recording.id,
                        models.Frame.ocr_status == "queued",
                    )
                    .count()
                )
                logger.info(
                    "[OCR] Picked queued frame id=%s index=%s screenpipe_id=%s; "
                    "%s still queued for recording %s",
                    frame.id,
                    frame.frame_index,
                    frame.screenpipe_frame_id or "n/a",
                    queued_count,
                    recording.id,
                )

                process_frame(frame, recording, db)
                if frame.ocr_status == "failed" and frame.error_message and "PaddleOCR" in frame.error_message:
                    if not self._ocr_disabled_logged:
                        logger.error("Paddle OCR unavailable: %s", frame.error_message)
                        self._ocr_disabled_logged = True
            except OCRDependencyError as exc:
                if not self._ocr_disabled_logged:
                    logger.error("Paddle OCR unavailable: %s", exc)
                    self._ocr_disabled_logged = True
                db.rollback()
            except Exception:
                logger.exception("Paddle OCR frame processing failed")
                db.rollback()
            finally:
                db.close()

            if self._stop_event.is_set():
                break
            self._stop_event.wait(self.poll_interval_seconds)
