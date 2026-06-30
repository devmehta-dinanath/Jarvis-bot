import logging
import threading
from datetime import datetime, timedelta

from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    OPENAI_API_KEY,
    WHATSAPP_AI_DRAFTS_ENABLED,
    WHATSAPP_AUTO_ADD_CALENDAR,
    WHATSAPP_CHIP_CONFIDENCE_MIN,
    WHATSAPP_CORRECTIONS_CONTEXT_LIMIT,
    WHATSAPP_ENABLED,
    WHATSAPP_EOD_REMINDER_HOUR,
    WHATSAPP_EOD_REMINDER_MINUTE,
    WHATSAPP_FOLLOWUP_FLAG_HOURS,
    WHATSAPP_FOLLOWUP_URGENT_HOURS,
    WHATSAPP_HISTORY_CONTEXT_LIMIT,
    WHATSAPP_LEAD_FOLLOWUP_HOURS,
    WHATSAPP_PERSONAL_SILENCE_CHECK_HOURS,
    WHATSAPP_PERSONAL_SILENCE_DAYS,
    WHATSAPP_POLL_INTERVAL_SECONDS,
    WHATSAPP_USER_NAMES,
)
from app.database import SessionLocal
from app.services.google_calendar.service import google_calendar_service
from app.services.whatsapp import actions
from app.services.whatsapp import calendar as wa_calendar
from app.services.whatsapp import classifier 
from app.services.whatsapp import fallback as wa_fallback
from app.services.whatsapp import repository as repo
from app.services.whatsapp import taxonomy as wa_taxonomy
from app.services.whatsapp.classifier import WhatsAppAIError

logger = logging.getLogger(__name__)


def _draft_lang_kwargs(
    *,
    result: dict | None = None,
    message=None,
) -> dict[str, str | None]:
    if result is not None:
        return {
            "language": result.get("language"),
            "translation": result.get("translation"),
        }
    if message is not None:
        return {
            "language": message.language,
            "translation": message.translation,
        }
    return {"language": None, "translation": None}


def _format_meeting_when(meeting: dict) -> str | None:
    start = meeting.get("start")
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%a %-d %b %-I:%M%p").replace("AM", "am").replace("PM", "pm")


def _meeting_chip_label(meeting: dict, confirmed: bool) -> str:
    when = _format_meeting_when(meeting)
    if confirmed and when:
        return f"Meeting {when} — add to Google Calendar?"
    if confirmed:
        return "Meeting requested — add to Google Calendar?"
    return "Unconfirmed plan — remind me to follow up"


def _followup_chip_label(hours_waiting: float | None, priority: str) -> str:
    if priority == "critical":
        if hours_waiting is not None:
            days = int(hours_waiting // 24)
            return f"Urgent: waiting {days}+ days — reply now"
        return "Urgent: client is waiting — reply now"
    if priority == "high":
        if hours_waiting is not None:
            return f"Waiting {int(hours_waiting)}h for a reply — respond"
        return "Client is waiting for a reply — respond"
    return "Follow-up — draft a reply"


def _payment_chip_label(payment_status: str | None) -> str:
    if payment_status == "received":
        return "Payment received — verify & confirm to the client"
    return "Payment overdue — acknowledge and send a reminder"


def _lead_chip_label() -> str:
    return "New lead — send a professional first reply"


def _document_chip_label(document_type: str | None, previously_sent_on: str | None) -> str:
    label = (document_type or "document").strip()
    if previously_sent_on:
        return f"{label.capitalize()} requested again — sent {previously_sent_on}, resend?"
    return f"{label.capitalize()} requested — send now?"


def _complaint_chip_label(anger_level: str | None) -> str:
    if anger_level == "high":
        return "Angry complaint — respond empathetically now"
    if anger_level == "low":
        return "Complaint — acknowledge and resolve"
    return "Complaint — respond empathetically now"


def _shipment_chip_label(shipment_status: str | None) -> str:
    if shipment_status == "delayed":
        return "Shipment delayed — send a proactive update?"
    return "Shipment update — acknowledge & close the loop"


def _greeting_chip_label() -> str:
    return "Casual message from this contact — reply when you can."


def _voice_note_chip_label(contact_name: str | None) -> str:
    who = contact_name or "this contact"
    return f"Voice note received from {who} — reply when you have listened."


def _eod_remind_at() -> datetime:
    """Next occurrence of the configured end-of-day hour in the server's local time."""
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    target = now.replace(
        hour=WHATSAPP_EOD_REMINDER_HOUR,
        minute=WHATSAPP_EOD_REMINDER_MINUTE,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target = target + timedelta(days=1)
    return target


_GROUP_FALLBACK_SURFACE = ("payment", "meeting", "complaint")


def _apply_forwarded_fallback(result: dict) -> dict:
    if result.get("category") in classifier.FILTER_LABELS:
        return result
    if result.get("category") in ("payment", "complaint") and result.get("is_important"):
        return result
    return classifier._silent_filter_result("forwarded", result.get("language"))


def _apply_group_fallback(result: dict) -> dict:
    if result.get("category") in _GROUP_FALLBACK_SURFACE and result.get("is_important"):
        result = dict(result)
        result["priority"] = "high"
        return result
    return {
        "is_important": False,
        "category": "group",
        "priority": "low",
        "payment_status": None,
        "document_type": None,
        "anger_level": None,
        "shipment_status": None,
        "language": result.get("language"),
        "translation": None,
        "summary": None,
    }


def _short_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%-d %b %Y")


def _draft_reply_with_budget(
    history,
    body: str,
    category: str,
    *,
    context_hint: str | None = None,
    result: dict | None = None,
    payment_status: str | None = None,
    shipment_status: str | None = None,
    complaint: bool = False,
    anger_level: str | None = None,
    message=None,
) -> str:
    if WHATSAPP_AI_DRAFTS_ENABLED:
        try:
            if complaint:
                return classifier.draft_complaint_reply(
                    history,
                    body,
                    anger_level,
                    **_draft_lang_kwargs(result=result, message=message),
                )
            return classifier.draft_reply(
                history,
                body,
                category,
                context_hint=context_hint,
                **_draft_lang_kwargs(result=result, message=message),
            )
        except WhatsAppAIError:
            logger.warning(
                "[WHATSAPP] AI drafting failed for category=%s, using fallback template",
                category,
            )
    return wa_fallback.draft_reply(
        category,
        payment_status=payment_status,
        shipment_status=shipment_status,
    )


class WhatsAppService:
    """Classifies inbound WhatsApp messages and creates actionable suggestions."""

    def __init__(
        self,
        poll_interval_seconds: float = WHATSAPP_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_silence_check_at: datetime = datetime.utcnow()

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

            if datetime.utcnow() >= self._next_silence_check_at:
                try:
                    self._check_personal_silence()
                except Exception:
                    logger.exception("[WHATSAPP] Personal silence check failed")
                interval_s = WHATSAPP_PERSONAL_SILENCE_CHECK_HOURS * 3600
                self._next_silence_check_at = datetime.utcnow() + timedelta(seconds=interval_s)

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
            while not self._stop_event.is_set():
                message = repo.next_unclassified_voice_note(db)
                if message is None:
                    break
                self._handle_voice_note(db, message)
                processed += 1
        finally:
            db.close()
        return processed

    def classify_message_now(self, db, message) -> None:
        """Classify a single inbound text message (used by webhook for immediate processing)."""
        self._classify_one(db, message)

    def handle_voice_note_now(self, db, message) -> None:
        """Flag a single voice note immediately (Phase 1 — no transcription)."""
        self._handle_voice_note(db, message)

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

        is_group = bool(getattr(message, "is_group", False))
        is_forwarded = bool(getattr(message, "is_forwarded", False))
        prior_count = repo.contact_prior_message_count(
            db, message.contact_id, exclude_message_id=message.id
        )

        if wa_fallback.is_likely_spam(body, has_prior_history=prior_count > 0):
            result = classifier._silent_filter_result("spam")
        else:
            corrections = repo.load_corrections_for_prompt(
                db, limit=WHATSAPP_CORRECTIONS_CONTEXT_LIMIT
            )
            try:
                result = classifier.classify_message(
                    history,
                    body,
                    is_group=is_group,
                    is_forwarded=is_forwarded,
                    user_names=WHATSAPP_USER_NAMES,
                    corrections=corrections or None,
                )
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
                if is_forwarded:
                    result = _apply_forwarded_fallback(result)
                elif is_group:
                    result = _apply_group_fallback(result)

        category = result["category"]
        lane: str = result.get("lane") or (
            "life" if category in classifier.LIFE_LANE_CATEGORIES else "work"
        )
        priority = result.get("priority", "normal")
        if category == "follow_up":
            priority = self._followup_priority(db, message)
        elif category == "complaint":
            priority = classifier.complaint_priority(result.get("anger_level"))
        if is_group and category not in ("group", *classifier.FILTER_LABELS) and result["is_important"]:
            priority = classifier.max_priority(priority, "high")
        if lane == "life":
            priority = "low"

        message.classified_at = datetime.utcnow()
        message.is_important = result["is_important"]
        message.category = category
        message.priority = priority
        message.language = result["language"]
        message.translation = result.get("translation")
        message.summary = result["summary"]

        if category in classifier.LIFE_LANE_CATEGORIES:
            repo.mark_contact_personal(db, message.contact_id)

        confidence = result.get("confidence")
        below_threshold = (
            confidence is not None
            and confidence < WHATSAPP_CHIP_CONFIDENCE_MIN
            and category not in classifier.ALWAYS_IMPORTANT
        )

        if category in classifier.FILTER_LABELS or category == "group":
            logger.info(
                "[WHATSAPP] Message %s silently filtered (category=%s)", message.id, category
            )
        elif below_threshold:
            logger.info(
                "[WHATSAPP] Message %s below confidence threshold "
                "(category=%s confidence=%s < %s) — no chip shown",
                message.id,
                category,
                confidence,
                WHATSAPP_CHIP_CONFIDENCE_MIN,
            )
        elif result["is_important"]:
            self._create_suggestion(db, message, history, body, result, priority)
        elif category == "greeting":
            self._create_greeting_suggestion(db, message, history, body)

        db.commit()
        logger.info(
            "[WHATSAPP] Classified message %s -> important=%s category=%s priority=%s confidence=%s",
            message.id,
            result["is_important"],
            category,
            priority,
            confidence,
        )

    def _followup_priority(self, db, message) -> str:
        """Escalate a follow-up by how long the client has waited for our reply."""
        last_out = repo.last_outbound_at(db, message.contact_id)
        if last_out is None:
            return "high"
        reference = message.timestamp or datetime.utcnow()
        hours_waiting = (reference - last_out).total_seconds() / 3600
        if hours_waiting >= WHATSAPP_FOLLOWUP_URGENT_HOURS:
            return "critical"
        if hours_waiting >= WHATSAPP_FOLLOWUP_FLAG_HOURS:
            return "high"
        return "normal"

    def _create_suggestion(self, db, message, history, body, result, priority) -> None:
        if repo.suggestion_exists_for_message(db, message.id):
            return

        category = result["category"]
        lane: str = result.get("lane") or (
            "life" if category in classifier.LIFE_LANE_CATEGORIES else "work"
        )
        confidence: int | None = result.get("confidence")

        if category == "payment":
            self._create_payment_suggestion(db, message, history, body, result, priority, confidence, lane)
            return

        if category == "lead":
            self._create_lead_suggestion(db, message, history, body, result, priority, confidence, lane)
            return

        if category == "document":
            self._create_document_suggestion(db, message, history, body, result, priority, confidence, lane)
            return

        if category == "personal_date":
            self._create_personal_date_suggestion(db, message, history, body, priority, confidence, lane)
            return

        if category == "personal_task":
            self._create_personal_task_suggestion(db, message, history, body, priority, confidence, lane)
            return

        if category == "family_plan":
            self._create_family_plan_suggestion(db, message, history, body, priority, confidence, lane)
            return

        if category == "complaint":
            self._create_complaint_suggestion(db, message, history, body, result, priority, confidence, lane)
            return

        if category == "shipment":
            self._create_shipment_suggestion(db, message, history, body, result, priority, confidence, lane)
            return

        if category == "meeting":
            try:
                meeting = classifier.extract_meeting(history, body)
            except WhatsAppAIError:
                logger.warning("[WHATSAPP] OpenAI meeting extraction failed, using fallback")
                meeting = wa_fallback.extract_meeting(body)
            draft = _draft_reply_with_budget(
                history,
                body,
                category,
                result=result,
            )
            meeting = wa_calendar.enrich_meeting_details(meeting)
            confirmed = bool(meeting.get("confirmed"))
            meeting["chip_label"] = _meeting_chip_label(meeting, confirmed)
            suggestion = repo.create_suggestion(
                db,
                contact_id=message.contact_id,
                message_id=message.id,
                kind="meeting",
                category=category,
                priority=priority,
                lane=lane,
                confidence=confidence,
                draft_text=draft,
                details=meeting,
            )
            if (
                confirmed
                and meeting.get("time_available")
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

        draft = _draft_reply_with_budget(history, body, category, result=result)

        details = None
        if category == "follow_up":
            details = self._followup_details(db, message, priority)
        elif category in wa_taxonomy.WORK_CATEGORIES:
            chip = wa_taxonomy.default_chip_label(category)
            if chip:
                details = {"chip_label": chip}
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="reply",
            category=category,
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=draft,
            details=details,
        )

    def _followup_details(self, db, message, priority) -> dict:
        last_out = repo.last_outbound_at(db, message.contact_id)
        reference = message.timestamp or datetime.utcnow()
        hours_waiting = None
        if last_out is not None:
            hours_waiting = round((reference - last_out).total_seconds() / 3600, 1)
        return {
            "hours_since_reply": hours_waiting,
            "chip_label": _followup_chip_label(hours_waiting, priority),
        }

    def _create_payment_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
    ) -> None:
        payment_status = result.get("payment_status") or "overdue"
        hint = classifier.payment_reply_hint(payment_status)
        draft = _draft_reply_with_budget(
            history,
            body,
            "payment",
            context_hint=hint,
            result=result,
            payment_status=payment_status,
        )

        details = {
            "payment_status": payment_status,
            "chip_label": _payment_chip_label(payment_status),
            "logged_at": datetime.utcnow().isoformat(),
        }
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="payment",
            category="payment",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=draft,
            details=details,
        )
        logger.warning(
            "[WHATSAPP] Payment message logged for contact %s (status=%s, message=%s)",
            message.contact_id,
            payment_status,
            message.id,
        )

    def _create_lead_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
    ) -> None:
        draft = _draft_reply_with_budget(history, body, "lead", result=result)

        reference = message.timestamp or datetime.utcnow()
        follow_up_due_at = reference + timedelta(hours=WHATSAPP_LEAD_FOLLOWUP_HOURS)
        details = {
            "is_new_lead": True,
            "follow_up_due_at": follow_up_due_at.isoformat(),
            "chip_label": _lead_chip_label(),
        }
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="lead",
            category="lead",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=draft,
            details=details,
        )

    def _create_document_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
    ) -> None:
        document_type = result.get("document_type")
        previous = repo.previous_document_sent(
            db,
            message.contact_id,
            document_type,
            exclude_message_id=message.id,
        )
        previously_sent_on = None
        if previous is not None:
            previously_sent_on = _short_date(previous.resolved_at or previous.created_at)

        draft = _draft_reply_with_budget(history, body, "document", result=result)

        details = {
            "document_type": document_type,
            "previously_sent": previous is not None,
            "previously_sent_on": previously_sent_on,
            "confirm_before_send": previous is not None,
            "chip_label": _document_chip_label(document_type, previously_sent_on),
        }
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="document",
            category="document",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=draft,
            details=details,
        )

    def _create_complaint_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
    ) -> None:
        anger_level = result.get("anger_level") or "high"
        draft = _draft_reply_with_budget(
            history,
            body,
            "complaint",
            result=result,
            complaint=True,
            anger_level=anger_level,
        )

        details = {
            "anger_level": anger_level,
            "chip_label": _complaint_chip_label(anger_level),
        }
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="complaint",
            category="complaint",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=draft,
            details=details,
        )
        logger.warning(
            "[WHATSAPP] Complaint logged for contact %s (anger=%s, message=%s)",
            message.contact_id,
            anger_level,
            message.id,
        )

    def _create_shipment_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
    ) -> None:
        shipment_status = result.get("shipment_status") or "good"
        is_delay = shipment_status == "delayed"
        hint = classifier.shipment_reply_hint(shipment_status)
        draft = _draft_reply_with_budget(
            history,
            body,
            "shipment",
            context_hint=hint,
            result=result,
            shipment_status=shipment_status,
        )

        details = {
            "shipment_status": shipment_status,
            "proactive_update": is_delay,
            "closes_follow_up": not is_delay,
            "chip_label": _shipment_chip_label(shipment_status),
            "logged_at": datetime.utcnow().isoformat(),
        }
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="shipment",
            category="shipment",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=draft,
            details=details,
        )
        logger.info(
            "[WHATSAPP] Shipment update logged for contact %s (status=%s, message=%s)",
            message.contact_id,
            shipment_status,
            message.id,
        )

    def _create_greeting_suggestion(self, db, message, history, body) -> None:
        if repo.suggestion_exists_for_message(db, message.id):
            return
        if repo.pending_nudge_exists(db, message.contact_id):
            return

        draft = _draft_reply_with_budget(
            history,
            body,
            "greeting",
            message=message,
        )

        details = {
            "tone": "casual",
            "chip_label": _greeting_chip_label(),
        }
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="nudge",
            category="greeting",
            priority="low",
            lane="work",
            draft_text=draft,
            details=details,
        )

    def _handle_voice_note(self, db, message) -> None:
        db.refresh(message)
        if message.classified_at is not None:
            return

        from app import models as _models
        contact = db.get(_models.WhatsAppContact, message.contact_id)
        contact_name = contact.profile_name if contact else None

        message.classified_at = datetime.utcnow()
        message.is_important = False
        message.category = "voice_note"
        message.priority = "medium"

        if not repo.suggestion_exists_for_message(db, message.id):
            voice_lane = (
                "life" if contact and getattr(contact, "contact_type", None) == "personal"
                else "work"
            )
            repo.create_suggestion(
                db,
                contact_id=message.contact_id,
                message_id=message.id,
                kind="nudge",
                category="voice_note",
                priority="medium",
                lane=voice_lane,
                draft_text=None,
                details={
                    "chip_label": _voice_note_chip_label(contact_name),
                    "phase": 1,
                    "transcription_available": False,
                },
            )

        db.commit()
        logger.info("[WHATSAPP] Voice note flagged for contact %s (message=%s)",
                    message.contact_id, message.id)

    def _create_personal_date_suggestion(
        self, db, message, history, body, priority,
        confidence: int | None = None, lane: str = "life",
    ) -> None:
        try:
            date_info = classifier.extract_personal_date(body)
        except WhatsAppAIError:
            date_info = wa_fallback.extract_personal_date(body)

        calendar_event_id = None
        calendar_html_link = None
        if (
            date_info.get("date")
            and WHATSAPP_AUTO_ADD_CALENDAR
            and google_calendar_service.auth_status().authorized
        ):
            try:
                from datetime import date as _date
                from zoneinfo import ZoneInfo

                from app.services.google_calendar.schemas import EventCreate, EventDateTime

                event_date = date_info["date"]
                start_iso = f"{event_date}T09:00:00"
                end_iso = f"{event_date}T09:30:00"
                payload = EventCreate(
                    summary=date_info["reminder_title"],
                    description=date_info.get("notes"),
                    start=EventDateTime(
                        date_time=start_iso,
                        time_zone=CALENDAR_DEFAULT_TIMEZONE,
                    ),
                    end=EventDateTime(
                        date_time=end_iso,
                        time_zone=CALENDAR_DEFAULT_TIMEZONE,
                    ),
                    conference=False,
                )
                event = google_calendar_service.create_event(payload)
                calendar_event_id = event.get("id")
                calendar_html_link = event.get("htmlLink")
                logger.info(
                    "[WHATSAPP] Personal date calendar event created: %s", calendar_event_id
                )
            except Exception:
                logger.exception("[WHATSAPP] Failed to create personal date calendar event")

        chip_date = date_info.get("date") or "the date"
        label = date_info.get("event_label") or "Personal date"
        chip_label = (
            f"{label} reminder added for {chip_date}"
            if calendar_event_id
            else f"{label} on {chip_date} — add to calendar?"
        )

        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="personal_reminder",
            category="personal_date",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=None,
            details={
                **date_info,
                "calendar_event_id": calendar_event_id,
                "calendar_html_link": calendar_html_link,
                "chip_label": chip_label,
            },
        )

    def _create_personal_task_suggestion(
        self, db, message, history, body, priority,
        confidence: int | None = None, lane: str = "life",
    ) -> None:
        try:
            task_info = classifier.extract_personal_task(body)
        except WhatsAppAIError:
            task_info = wa_fallback.extract_personal_task(body)

        remind_at = _eod_remind_at()
        chip_label = f"Reminder added — {task_info['task_summary']}"

        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="personal_reminder",
            category="personal_task",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=None,
            details={
                **task_info,
                "remind_at": remind_at.isoformat(),
                "chip_label": chip_label,
            },
        )

    def _create_family_plan_suggestion(
        self, db, message, history, body, priority,
        confidence: int | None = None, lane: str = "life",
    ) -> None:
        try:
            plan = classifier.extract_family_plan(body)
        except WhatsAppAIError:
            plan = wa_fallback.extract_family_plan(body)

        calendar_event_id = None
        calendar_html_link = None
        if (
            plan.get("date")
            and WHATSAPP_AUTO_ADD_CALENDAR
            and google_calendar_service.auth_status().authorized
        ):
            try:
                from app.services.google_calendar.schemas import EventCreate, EventDateTime

                event_date = plan["date"]
                start_time = plan.get("time") or "09:00"
                start_iso = f"{event_date}T{start_time}:00"
                from datetime import datetime as _dt

                start_dt = _dt.fromisoformat(start_iso)
                end_dt = start_dt + timedelta(hours=2)
                end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

                label = plan.get("event_label") or "Family plan"
                description_parts = ["[Personal event — not work]"]
                if plan.get("place"):
                    description_parts.append(f"Location: {plan['place']}")
                description = "\n".join(description_parts)

                payload = EventCreate(
                    summary=f"[Personal] {label}",
                    description=description,
                    start=EventDateTime(
                        date_time=start_iso,
                        time_zone=CALENDAR_DEFAULT_TIMEZONE,
                    ),
                    end=EventDateTime(
                        date_time=end_iso,
                        time_zone=CALENDAR_DEFAULT_TIMEZONE,
                    ),
                    conference=False,
                )
                event = google_calendar_service.create_event(payload)
                calendar_event_id = event.get("id")
                calendar_html_link = event.get("htmlLink")
                logger.info(
                    "[WHATSAPP] Family plan calendar event created: %s", calendar_event_id
                )
            except Exception:
                logger.exception("[WHATSAPP] Failed to create family plan calendar event")

        label = plan.get("event_label") or "Family plan"
        when_parts = []
        if plan.get("date"):
            try:
                from datetime import date as _date

                parsed = _date.fromisoformat(plan["date"])
                when_parts.append(parsed.strftime("%A"))
            except ValueError:
                when_parts.append(plan["date"])
        if plan.get("time"):
            when_parts.append(plan["time"])
        when_str = " ".join(when_parts)

        if calendar_event_id:
            chip_label = f"{label} {when_str} — added to calendar ✓".strip()
        elif when_str:
            chip_label = f"{label} {when_str} — add to calendar?".strip()
        else:
            chip_label = f"{label} — add to calendar?"

        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="personal_reminder",
            category="family_plan",
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=None,
            details={
                **plan,
                "calendar_event_id": calendar_event_id,
                "calendar_html_link": calendar_html_link,
                "chip_label": chip_label,
                "is_personal_event": True,
            },
        )

    def _check_personal_silence(self) -> None:
        """Raise a gentle life-nudge for every personal contact whose silence
        exceeds the configured threshold, unless a nudge is already pending."""
        silence_hours = WHATSAPP_PERSONAL_SILENCE_DAYS * 24
        db = SessionLocal()
        try:
            contacts = repo.personal_contacts_awaiting_reply(
                db, silence_hours=silence_hours
            )
            for contact in contacts:
                reference = contact.last_inbound_at or contact.created_at
                days = max(
                    1,
                    int((datetime.utcnow() - reference).total_seconds() / 86400),
                )
                display_name = (contact.profile_name or "").strip() or contact.wa_id
                day_word = "day" if days == 1 else "days"
                chip_label = (
                    f"You haven't replied to {display_name} in {days} {day_word}"
                )
                repo.create_suggestion(
                    db,
                    contact_id=contact.id,
                    message_id=None,
                    kind="life_nudge",
                    category="personal_silence",
                    priority="low",
                    lane="life",
                    draft_text=None,
                    details={
                        "chip_label": chip_label,
                        "days_silent": days,
                        "contact_name": display_name,
                    },
                )
                logger.info(
                    "[WHATSAPP] Silence nudge raised for contact %s (%d days)",
                    contact.id,
                    days,
                )
        finally:
            db.close()
