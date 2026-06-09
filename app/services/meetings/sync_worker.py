import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import models
from app.config import (
    MEETING_AUDIO_SYNC_ENABLED,
    MEETING_TRANSCRIPT_RETRY_MAX_AGE_HOURS,
    MEETING_TRANSCRIPT_SYNC_INTERVAL_SECONDS,
    SCREENPIPE_API_URL,
)
from app.database import SessionLocal
from app.services.activity.categories import ActivityCategory
from app.services.meetings.transcript import sync_meeting_transcript
from app.services.screenpipe.client import is_healthy

logger = logging.getLogger(__name__)

_RETRY_STATUSES = ("pending", "failed", "ocr_fallback", "syncing")


class MeetingTranscriptSyncService:
    """Retry meeting audio transcript sync when Screenpipe API becomes available."""

    def __init__(
        self,
        poll_interval_seconds: float = MEETING_TRANSCRIPT_SYNC_INTERVAL_SECONDS,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not MEETING_AUDIO_SYNC_ENABLED:
            logger.info("[MEETING] Transcript retry worker disabled (MEETING_AUDIO_SYNC_ENABLED=false)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="meeting-transcript-sync",
            daemon=True,
        )
        self._thread.start()
        logger.info("[MEETING] Transcript retry worker started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[MEETING] Transcript retry worker stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if is_healthy(SCREENPIPE_API_URL):
                db = SessionLocal()
                try:
                    self._retry_pending_chunks(db)
                except Exception:
                    logger.exception("[MEETING] Transcript retry worker error")
                    db.rollback()
                finally:
                    db.close()
            self._stop_event.wait(self.poll_interval_seconds)

    def _retry_pending_chunks(self, db: Session) -> None:
        cutoff = datetime.utcnow() - timedelta(hours=MEETING_TRANSCRIPT_RETRY_MAX_AGE_HOURS)
        chunks = (
            db.query(models.ActivityChunk)
            .filter(
                models.ActivityChunk.category == ActivityCategory.MEETINGS.value,
                models.ActivityChunk.transcript_status.in_(_RETRY_STATUSES),
                models.ActivityChunk.timestamp >= cutoff,
            )
            .order_by(models.ActivityChunk.id.desc())
            .limit(5)
            .all()
        )
        for chunk in chunks:
            previous_status = chunk.transcript_status
            sync_meeting_transcript(chunk, db, force=True)
            if chunk.transcript_status != previous_status:
                logger.info(
                    "[MEETING] Retried chunk %s transcript: %s -> %s",
                    chunk.id,
                    previous_status,
                    chunk.transcript_status,
                )
