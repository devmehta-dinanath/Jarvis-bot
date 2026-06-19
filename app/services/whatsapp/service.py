import logging
import threading
from datetime import datetime

from app.config import (
    OPENAI_API_KEY,
    WHATSAPP_AUTO_ADD_CALENDAR,
    WHATSAPP_ENABLED,
    WHATSAPP_HISTORY_CONTEXT_LIMIT,
    WHATSAPP_POLL_INTERVAL_SECONDS,
)
from app.database import SessionLocal
from app.services.google_calendar.service import google_calendar_service
from app.services.whatsapp import actions
from app.services.whatsapp import calendar as wa_calendar
from app.services.whatsapp import classifier
from app.services.whatsapp import fallback as wa_fallback
from app.services.whatsapp import repository as repo
from app.services.whatsapp.classifier import WhatsAppAIError

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Classifies inbound WhatsApp messages and creates actionable suggestions."""

    def __init__(
        self,
        poll_interval_seconds: float = WHATSAPP_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_enabled(self) -> bool:
        return WHATSAPP_ENABLED and OPENAI_API_KEY is not None

    def start(self) -> None:
        if not self.is_enabled:
            if WHATSAPP_ENABLED and not OPENAI_API_KEY:
                logger.warning(
                    "[WHATSAPP] Disabled — set OPENAI_API_KEY to enable AI classification"
                )
            else:
                logger.info("[WHATSAPP] Worker disabled (WHATSAPP_ENABLED=false)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="whatsapp-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[WHATSAPP] Worker started (poll=%s s)",
            self.poll_interval_seconds,
        )

    def stop(self) -> None:
        if not self.is_enabled:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[WHATSAPP] Worker stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = self._process_batch()
            except Exception:
                logger.exception("[WHATSAPP] Classification cycle failed")
                processed = 0
            # Drain quickly when there is a backlog, otherwise wait the poll interval.
            if processed == 0:
                self._stop_event.wait(self.poll_interval_seconds)

    def _process_batch(self) -> int:
        db = SessionLocal()
        processed = 0
        try:
            while not self._stop_event.is_set():
                message = repo.next_unclassified_inbound(db)
                if message is None:
                    break
                self._classify_one(db, message)
                processed += 1
        finally:
            db.close()
        return processed

    def classify_message_now(self, db, message) -> None:
        """Classify a single inbound message (used by webhook for immediate processing)."""
        self._classify_one(db, message)

    def _classify_one(self, db, message) -> None:
        db.refresh(message)
        if message.classified_at is not None:
            return

        body = (message.body or "").strip()
        if not body:
            message.classified_at = datetime.utcnow()
            message.is_important = False
            message.category = "greeting"
            db.commit()
            return

        history = repo.recent_history(
            db,
            message.contact_id,
            limit=WHATSAPP_HISTORY_CONTEXT_LIMIT,
            before_message_id=message.id,
        )

        try:
            result = classifier.classify_message(history, body)
        except WhatsAppAIError as exc:
            logger.warning(
                "[WHATSAPP] OpenAI classification failed for message %s, using fallback: %s",
                message.id,
                exc,
            )
            result = wa_fallback.classify_message(body)
            if result is None:
                logger.error(
                    "[WHATSAPP] Classification failed for message %s: %s",
                    message.id,
                    exc,
                )
                raise

        message.classified_at = datetime.utcnow()
        message.is_important = result["is_important"]
        message.category = result["category"]
        message.language = result["language"]
        message.summary = result["summary"]

        if result["is_important"]:
            self._create_suggestion(db, message, history, body, result["category"])

        db.commit()
        logger.info(
            "[WHATSAPP] Classified message %s -> important=%s category=%s",
            message.id,
            result["is_important"],
            result["category"],
        )

    def _create_suggestion(self, db, message, history, body, category) -> None:
        if repo.suggestion_exists_for_message(db, message.id):
            return

        if category == "meeting":
            try:
                meeting = classifier.extract_meeting(history, body)
                draft = classifier.draft_reply(history, body, category)
            except WhatsAppAIError:
                logger.warning("[WHATSAPP] OpenAI meeting extraction failed, using fallback")
                meeting = wa_fallback.extract_meeting(body)
                draft = wa_fallback.draft_reply(category)
            meeting = wa_calendar.enrich_meeting_details(meeting)
            suggestion = repo.create_suggestion(
                db,
                contact_id=message.contact_id,
                message_id=message.id,
                kind="meeting",
                category=category,
                draft_text=draft,
                details=meeting,
            )
            if (
                meeting.get("time_available")
                and WHATSAPP_AUTO_ADD_CALENDAR
                and google_calendar_service.auth_status().authorized
            ):
                try:
                    actions.add_to_calendar(db, suggestion, conference=True)
                    logger.info(
                        "[WHATSAPP] Auto-booked meeting for suggestion %s",
                        suggestion.id,
                    )
                except Exception:
                    logger.exception(
                        "[WHATSAPP] Auto calendar booking failed for suggestion %s",
                        suggestion.id,
                    )
            return

        try:
            draft = classifier.draft_reply(history, body, category)
        except WhatsAppAIError:
            draft = wa_fallback.draft_reply(category)
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="reply",
            category=category,
            draft_text=draft,
        )
