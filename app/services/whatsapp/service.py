import logging
import random
import threading
from datetime import datetime, timedelta

from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    OPENAI_API_KEY,
    WHATSAPP_AI_DRAFTS_ENABLED,
    WHATSAPP_AUTO_ADD_CALENDAR,
    WHATSAPP_CASUAL_SUGGESTION_DELAY_MAX_HOURS,
    WHATSAPP_CASUAL_SUGGESTION_DELAY_MIN_HOURS,
    WHATSAPP_CHIP_CONFIDENCE_MIN,
    WHATSAPP_CORRECTIONS_CONTEXT_LIMIT,
    WHATSAPP_ENABLED,
    WHATSAPP_EOD_REMINDER_HOUR,
    WHATSAPP_EOD_REMINDER_MINUTE,
    WHATSAPP_FOLLOWUP_FLAG_HOURS,
    WHATSAPP_FOLLOWUP_URGENT_HOURS,
    WHATSAPP_HISTORY_CONTEXT_LIMIT,
    WHATSAPP_LEAD_FOLLOWUP_HOURS,
    WHATSAPP_NORMAL_SUGGESTION_DELAY_MINUTES,
    WHATSAPP_PERSONAL_SILENCE_CHECK_HOURS,
    WHATSAPP_PERSONAL_SILENCE_DAYS,
    WHATSAPP_POLL_INTERVAL_SECONDS,
    WHATSAPP_REPLY_COOLDOWN_HOURS,
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


def _clarify_chip_label(question: str) -> str:
    return f"Needs one detail — {question}"


def _voice_note_chip_label(contact_name: str | None) -> str:
    who = contact_name or "this contact"
    return f"Voice note received from {who} — reply when you have listened."


_MEDIA_TYPE_LABELS = {
    "image": "a photo",
    "video": "a video",
    "document": "a document",
    "sticker": "a sticker",
    "contacts": "a contact card",
    "location": "a location",
    "media": "a file",
}


def _media_chip_label(contact_name: str | None, msg_type: str) -> str:
    who = contact_name or "this contact"
    what = _MEDIA_TYPE_LABELS.get(msg_type, "a file")
    return f"{who} sent {what} with no caption — open WhatsApp to view it."


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
    instructions: list[str] | None = None,
    corrections: list[dict] | None = None,
    personal: bool = False,
    voice_examples: list[str] | None = None,
) -> str:
    if WHATSAPP_AI_DRAFTS_ENABLED:
        try:
            if complaint:
                return classifier.draft_complaint_reply(
                    history,
                    body,
                    anger_level,
                    instructions=instructions,
                    corrections=corrections,
                    voice_examples=voice_examples,
                    **_draft_lang_kwargs(result=result, message=message),
                )
            return classifier.draft_reply(
                history,
                body,
                category,
                context_hint=context_hint,
                instructions=instructions,
                corrections=corrections,
                personal=personal,
                voice_examples=voice_examples,
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
                try:
                    self._check_work_awaiting_reply()
                except Exception:
                    logger.exception("[WHATSAPP] Work awaiting-reply check failed")
                try:
                    self._check_pending_commitments()
                except Exception:
                    logger.exception("[WHATSAPP] Pending commitments check failed")
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
                try:
                    self._handle_voice_note(db, message)
                except Exception:
                    # Same queue-jam risk as text messages — next_unclassified_voice_note()
                    # always refetches the oldest one, so a failure here would otherwise
                    # block every voice note behind it forever.
                    logger.exception(
                        "[WHATSAPP] Voice note handling failed unexpectedly for message %s — "
                        "marking as failed instead of blocking the queue",
                        message.id,
                    )
                    db.rollback()
                    self._mark_classification_failed(db, message)
                processed += 1
            while not self._stop_event.is_set():
                message = repo.next_uncommitment_checked_outbound(db)
                if message is None:
                    break
                self._analyze_commitment(db, message)
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

    def handle_media_message_now(self, db, message) -> None:
        """Flag a caption-less media message (photo/video/document/sticker/...)

        so it shows up as a chip instead of being silently dropped just
        because there's no text body for the classifier to run on.
        """
        self._handle_media_message(db, message)

    def _classify_one(self, db, message) -> None:
        db.refresh(message)
        if message.classified_at is not None:
            return

        if message.contact is not None and message.contact.is_excluded:
            message.classified_at = datetime.utcnow()
            message.is_important = False
            message.category = "excluded"
            db.commit()
            return

        body = (message.body or "").strip()
        if not body:
            message.classified_at = datetime.utcnow()
            message.is_important = False
            message.category = "greeting"
            db.commit()
            return

        try:
            self._classify_one_unsafe(db, message, body)
        except Exception:
            # next_unclassified_inbound() always fetches the single oldest unclassified
            # message across ALL contacts — if this message's classification keeps raising,
            # it would get retried forever and permanently block every message queued behind
            # it (any contact, group or not) from ever being classified. Fail this one message
            # closed instead: surface it manually and let the queue move on.
            logger.exception(
                "[WHATSAPP] Classification failed unexpectedly for message %s — "
                "marking as failed instead of blocking the queue",
                message.id,
            )
            db.rollback()
            self._mark_classification_failed(db, message)

    def _mark_classification_failed(self, db, message) -> None:
        db.refresh(message)
        if message.classified_at is not None:
            return
        message.classified_at = datetime.utcnow()
        message.is_important = False
        message.category = "error"
        message.priority = "normal"
        if not repo.suggestion_exists_for_message(db, message.id):
            repo.create_suggestion(
                db,
                contact_id=message.contact_id,
                message_id=message.id,
                kind="nudge",
                category="error",
                priority="normal",
                lane="work",
                draft_text=None,
                details={
                    "chip_label": "Couldn't classify this message automatically — please review it manually.",
                    "classification_error": True,
                },
            )
        db.commit()

    def _analyze_commitment(self, db, message) -> None:
        """Check one outbound message: does it either fulfill the contact's currently
        open commitment, or make a brand-new one? Only one open commitment is tracked
        per contact at a time — see WhatsAppCommitment."""
        db.refresh(message)
        if message.classified_at is not None:
            return
        try:
            self._analyze_commitment_unsafe(db, message)
        except Exception:
            # Same queue-jam risk as classification/voice notes — always refetches the
            # oldest unprocessed outbound message, so a repeated failure here would
            # otherwise block every later outbound message from ever being checked.
            logger.exception(
                "[WHATSAPP] Commitment check failed unexpectedly for message %s — "
                "marking as processed instead of blocking the queue",
                message.id,
            )
            db.rollback()
            db.refresh(message)
            if message.classified_at is None:
                message.classified_at = datetime.utcnow()
                db.commit()

    def _analyze_commitment_unsafe(self, db, message) -> None:
        body = (message.body or "").strip()
        message.classified_at = datetime.utcnow()
        if not body:
            db.commit()
            return

        pending = repo.pending_commitment_for_contact(db, message.contact_id)
        try:
            if pending is not None:
                if classifier.check_commitment_fulfilled(pending.label, body):
                    repo.fulfill_commitment(db, pending.id)
                    logger.info(
                        "[WHATSAPP] Commitment fulfilled for contact %s: %s",
                        message.contact_id, pending.label,
                    )
            else:
                detected = classifier.detect_commitment(body)
                if detected["is_commitment"]:
                    repo.create_commitment(
                        db,
                        contact_id=message.contact_id,
                        message_id=message.id,
                        commitment_type=detected["commitment_type"] or "other",
                        label=detected["label"] or "Follow up on this",
                    )
                    logger.info(
                        "[WHATSAPP] New commitment detected for contact %s: %s",
                        message.contact_id, detected["label"],
                    )
        except WhatsAppAIError as exc:
            logger.warning(
                "[WHATSAPP] OpenAI commitment check failed for message %s: %s",
                message.id, exc,
            )
        db.commit()

    def _classify_one_unsafe(self, db, message, body: str) -> None:
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
        contact_name = message.contact.profile_name if message.contact is not None else None
        is_known_sender = contact_name is not None
        is_personal_contact = bool(
            message.contact is not None and message.contact.contact_type == "personal"
        )
        instructions = [
            i.text for i in repo.list_instructions(db, active_only=True)
        ]
        voice_examples = repo.recent_outbound_examples(db, personal=is_personal_contact)

        # Rule 12 — 7-day silent observation. Anchored retroactively to the earliest
        # outbound message already on record (see get_or_create_learning_state), so an
        # account with existing history doesn't lose a week of suggestions the moment
        # this ships; a genuinely fresh install gets the real 7-day silent window.
        learning_state = repo.get_or_create_learning_state(db)
        in_silent_observation = (
            datetime.utcnow() - learning_state.observation_started_at < timedelta(days=7)
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
                    instructions=instructions or None,
                    contact_name=contact_name,
                    is_known_sender=is_known_sender,
                    is_personal_contact=is_personal_contact,
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
        is_urgent_category = category in ("payment", "complaint", "lead")
        is_life_lane = category in classifier.LIFE_LANE_CATEGORIES
        reads_as_personal = is_personal_contact or bool(result.get("personal_tone"))

        # Fail closed for client/WORK-lane messages: an unparseable/missing confidence score
        # is treated as "not confident" and no such category (including payment/complaint) is
        # exempt, since a wrong suggestion to a client is worse than a missed one. A contact
        # already established as personal (family/close friend, from an earlier
        # personal_date/personal_task/family_plan message) — or whose first message simply
        # reads as personal in tone — is a different risk profile: a slightly-off casual reply
        # costs nothing, so their non-urgent chatter always gets a drafted attempt instead of
        # being held to the same client-accuracy bar.
        below_threshold = (
            confidence is None or confidence < WHATSAPP_CHIP_CONFIDENCE_MIN
        ) and not (reads_as_personal and not is_urgent_category)

        # Rule 9 — reply threshold/timing rules. This is a client-triage rule and does not
        # apply to LIFE lane messages (family/close friends, handled entirely separately).
        # Payment/complaint/lead are "urgent": they always surface immediately and skip the
        # known-contact/reply-cooldown gates below. Everything else must be from a contact
        # with prior history, and the user must not have replied to them within the cooldown
        # window, or it is suppressed entirely.
        bypasses_reply_rules = is_urgent_category or is_life_lane
        known_contact = prior_count > 0
        recently_replied = (
            message.contact is not None
            and message.contact.last_replied_at is not None
            and (datetime.utcnow() - message.contact.last_replied_at)
            < timedelta(hours=WHATSAPP_REPLY_COOLDOWN_HOURS)
        )
        suppressed_by_reply_rules = not bypasses_reply_rules and (
            not known_contact or recently_replied
        )

        if bypasses_reply_rules:
            delay = timedelta(0)
        elif category == "greeting":
            delay = timedelta(
                hours=random.uniform(
                    WHATSAPP_CASUAL_SUGGESTION_DELAY_MIN_HOURS,
                    WHATSAPP_CASUAL_SUGGESTION_DELAY_MAX_HOURS,
                )
            )
        else:
            delay = timedelta(minutes=WHATSAPP_NORMAL_SUGGESTION_DELAY_MINUTES)
        visible_after = datetime.utcnow() + delay

        suggestion = None
        if result.get("safety_concern"):
            logger.warning(
                "[WHATSAPP] Message %s flagged as a possible safety concern — surfacing for "
                "manual response, no AI draft", message.id,
            )
            self._create_safety_concern_suggestion(db, message, category, lane)
        elif category in classifier.FILTER_LABELS or category == "group":
            logger.info(
                "[WHATSAPP] Message %s silently filtered (category=%s)", message.id, category
            )
        elif in_silent_observation and (result["is_important"] or category == "greeting"):
            # Rule 12 — 7-day silent observation. Checked ahead of Rule 9 (known-contact/
            # cooldown gate) and the clarification/confidence gates deliberately: those all
            # exist to decide whether an AI draft is trustworthy enough to show, but during
            # this window there is no AI draft at all — every is_important/greeting message,
            # including a brand-new contact's very first message, gets the same empty reply
            # box. The point is to learn Sujay's own tone, not contaminate it with AI wording,
            # and to capture exactly the first-contact replies that matter most for that.
            # His own reply is saved as a normal outbound message either way, which is what
            # feeds recent_outbound_examples/voice learning afterwards.
            logger.info(
                "[WHATSAPP] Message %s in 7-day silent observation window (started %s, "
                "category=%s) — surfacing with no AI draft for manual reply",
                message.id, learning_state.observation_started_at, category,
            )
            suggestion = self._create_blank_suggestion(
                db, message, category, priority, lane, confidence
            )
        elif suppressed_by_reply_rules:
            logger.info(
                "[WHATSAPP] Message %s suppressed by reply-threshold rules "
                "(known_contact=%s recently_replied=%s category=%s)",
                message.id, known_contact, recently_replied, category,
            )
        elif result.get("needs_clarification"):
            if repo.pending_clarification_exists(db, message.contact_id):
                # Rule 13 — max one question per conversation at a time. Don't stack a
                # second one; fall back to the plain low-confidence nudge instead of
                # going silent entirely.
                logger.info(
                    "[WHATSAPP] Message %s needs clarification but one is already "
                    "pending for contact %s — falling back to unconfident nudge",
                    message.id, message.contact_id,
                )
                suggestion = self._create_unconfident_suggestion(
                    db, message, category, priority, confidence, lane
                )
            else:
                logger.info(
                    "[WHATSAPP] Message %s is ambiguous — asking: %s",
                    message.id, result.get("clarifying_question"),
                )
                suggestion = self._create_clarification_suggestion(
                    db, message, category, priority, confidence, lane,
                    result["clarifying_question"], result["clarifying_options"],
                )
        elif below_threshold:
            logger.info(
                "[WHATSAPP] Message %s below confidence threshold "
                "(category=%s confidence=%s < %s) — showing message, no AI draft",
                message.id,
                category,
                confidence,
                WHATSAPP_CHIP_CONFIDENCE_MIN,
            )
            suggestion = self._create_unconfident_suggestion(
                db, message, category, priority, confidence, lane
            )
        elif result["is_important"]:
            suggestion = self._create_suggestion(
                db, message, history, body, result, priority, instructions, corrections, voice_examples
            )
        elif category == "greeting":
            suggestion = self._create_greeting_suggestion(
                db, message, history, body, result, instructions, corrections, voice_examples
            )

        if suggestion is not None:
            suggestion.visible_after = visible_after

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

    def _create_suggestion(
        self,
        db,
        message,
        history,
        body,
        result,
        priority,
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
        if repo.suggestion_exists_for_message(db, message.id):
            return None

        category = result["category"]
        lane: str = result.get("lane") or (
            "life" if category in classifier.LIFE_LANE_CATEGORIES else "work"
        )
        confidence: int | None = result.get("confidence")
        is_personal = bool(
            message.contact is not None and message.contact.contact_type == "personal"
        ) or bool(result.get("personal_tone"))

        if category == "payment":
            return self._create_payment_suggestion(
                db, message, history, body, result, priority, confidence, lane, instructions, corrections, voice_examples
            )

        if category == "lead":
            return self._create_lead_suggestion(
                db, message, history, body, result, priority, confidence, lane, instructions, corrections, voice_examples
            )

        if category == "document":
            return self._create_document_suggestion(
                db, message, history, body, result, priority, confidence, lane, instructions, corrections, voice_examples
            )

        if category == "personal_date":
            self._create_personal_date_suggestion(db, message, history, body, priority, confidence, lane)
            return None

        if category == "personal_task":
            self._create_personal_task_suggestion(db, message, history, body, priority, confidence, lane)
            return None

        if category == "family_plan":
            self._create_family_plan_suggestion(db, message, history, body, priority, confidence, lane)
            return None

        if category == "complaint":
            return self._create_complaint_suggestion(
                db, message, history, body, result, priority, confidence, lane, instructions, corrections, voice_examples
            )

        if category == "shipment":
            return self._create_shipment_suggestion(
                db, message, history, body, result, priority, confidence, lane, instructions, corrections, voice_examples
            )

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
                instructions=instructions,
                corrections=corrections,
                voice_examples=voice_examples,
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
            return suggestion

        if category == "timeline":
            try:
                deadline = classifier.extract_deadline(body)
            except WhatsAppAIError:
                logger.warning("[WHATSAPP] OpenAI deadline extraction failed, using fallback")
                deadline = wa_fallback.extract_deadline(body)

            draft = _draft_reply_with_budget(
                history,
                body,
                category,
                result=result,
                instructions=instructions,
                corrections=corrections,
                voice_examples=voice_examples,
            )

            calendar_event_id = None
            calendar_html_link = None
            if (
                deadline.get("date")
                and WHATSAPP_AUTO_ADD_CALENDAR
                and google_calendar_service.auth_status().authorized
            ):
                try:
                    from app.services.google_calendar.schemas import EventCreate, EventDateTime

                    event_date = deadline["date"]
                    payload = EventCreate(
                        summary=deadline["deadline_label"],
                        description=body.strip()[:500] or None,
                        start=EventDateTime(
                            date_time=f"{event_date}T09:00:00",
                            time_zone=CALENDAR_DEFAULT_TIMEZONE,
                        ),
                        end=EventDateTime(
                            date_time=f"{event_date}T09:30:00",
                            time_zone=CALENDAR_DEFAULT_TIMEZONE,
                        ),
                        conference=False,
                    )
                    event = google_calendar_service.create_event(payload)
                    calendar_event_id = event.get("id")
                    calendar_html_link = event.get("htmlLink")
                    logger.info(
                        "[WHATSAPP] Deadline reminder calendar event created: %s", calendar_event_id
                    )
                except Exception:
                    logger.exception("[WHATSAPP] Failed to create deadline calendar event")

            chip_label = wa_taxonomy.default_chip_label(category) or "Timeline question — confirm dates"
            if deadline.get("date"):
                chip_label = (
                    f"{deadline['deadline_label']} — reminder added for {deadline['date']}"
                    if calendar_event_id
                    else f"{deadline['deadline_label']} on {deadline['date']} — add to calendar?"
                )

            return repo.create_suggestion(
                db,
                contact_id=message.contact_id,
                message_id=message.id,
                kind="reply",
                category=category,
                priority=priority,
                lane=lane,
                confidence=confidence,
                draft_text=draft,
                details={
                    "chip_label": chip_label,
                    "deadline_label": deadline.get("deadline_label"),
                    "deadline_date": deadline.get("date"),
                    "calendar_event_id": calendar_event_id,
                    "calendar_html_link": calendar_html_link,
                },
            )

        draft = _draft_reply_with_budget(
            history,
            body,
            category,
            result=result,
            instructions=instructions,
            corrections=corrections,
            personal=is_personal,
            voice_examples=voice_examples,
        )

        details = None
        if category == "follow_up":
            details = self._followup_details(db, message, priority)
        elif category in wa_taxonomy.WORK_CATEGORIES:
            chip = wa_taxonomy.default_chip_label(category)
            if chip:
                details = {"chip_label": chip}
        return repo.create_suggestion(
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
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
        payment_status = result.get("payment_status") or "overdue"
        hint = classifier.payment_reply_hint(payment_status)
        draft = _draft_reply_with_budget(
            history,
            body,
            "payment",
            context_hint=hint,
            result=result,
            payment_status=payment_status,
            instructions=instructions,
            corrections=corrections,
            voice_examples=voice_examples,
        )

        details = {
            "payment_status": payment_status,
            "chip_label": _payment_chip_label(payment_status),
            "logged_at": datetime.utcnow().isoformat(),
        }
        suggestion = repo.create_suggestion(
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
        return suggestion

    def _create_lead_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
        draft = _draft_reply_with_budget(
            history, body, "lead", result=result, instructions=instructions, corrections=corrections,
            voice_examples=voice_examples,
        )

        reference = message.timestamp or datetime.utcnow()
        follow_up_due_at = reference + timedelta(hours=WHATSAPP_LEAD_FOLLOWUP_HOURS)
        details = {
            "is_new_lead": True,
            "follow_up_due_at": follow_up_due_at.isoformat(),
            "chip_label": _lead_chip_label(),
        }
        return repo.create_suggestion(
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
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
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

        draft = _draft_reply_with_budget(
            history, body, "document", result=result, instructions=instructions, corrections=corrections,
            voice_examples=voice_examples,
        )

        details = {
            "document_type": document_type,
            "previously_sent": previous is not None,
            "previously_sent_on": previously_sent_on,
            "confirm_before_send": previous is not None,
            "chip_label": _document_chip_label(document_type, previously_sent_on),
        }
        return repo.create_suggestion(
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
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
        anger_level = result.get("anger_level") or "high"
        draft = _draft_reply_with_budget(
            history,
            body,
            "complaint",
            result=result,
            complaint=True,
            anger_level=anger_level,
            instructions=instructions,
            corrections=corrections,
            voice_examples=voice_examples,
        )

        details = {
            "anger_level": anger_level,
            "chip_label": _complaint_chip_label(anger_level),
        }
        suggestion = repo.create_suggestion(
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
        return suggestion

    def _create_shipment_suggestion(
        self, db, message, history, body, result, priority,
        confidence: int | None = None, lane: str = "work",
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
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
            instructions=instructions,
            corrections=corrections,
            voice_examples=voice_examples,
        )

        details = {
            "shipment_status": shipment_status,
            "proactive_update": is_delay,
            "closes_follow_up": not is_delay,
            "chip_label": _shipment_chip_label(shipment_status),
            "logged_at": datetime.utcnow().isoformat(),
        }
        suggestion = repo.create_suggestion(
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
        return suggestion

    def _create_greeting_suggestion(
        self,
        db,
        message,
        history,
        body,
        result: dict | None = None,
        instructions: list[str] | None = None,
        corrections: list[dict] | None = None,
        voice_examples: list[str] | None = None,
    ):
        if repo.suggestion_exists_for_message(db, message.id):
            return None
        if repo.pending_nudge_exists(db, message.contact_id):
            return None

        # A contact already established as personal (from a prior clean personal_date/
        # personal_task/family_plan hit) is the strongest signal; failing that, fall back to
        # this message's own tone — a brand-new contact's first "hey" shouldn't get a
        # client-service-toned reply just because we haven't seen enough history yet.
        is_personal = bool(
            message.contact is not None and message.contact.contact_type == "personal"
        ) or bool((result or {}).get("personal_tone"))

        draft = _draft_reply_with_budget(
            history,
            body,
            "greeting",
            message=message,
            instructions=instructions,
            corrections=corrections,
            personal=is_personal,
            voice_examples=voice_examples,
        )

        details = {
            "tone": "casual",
            "chip_label": _greeting_chip_label(),
        }
        return repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="nudge",
            category="greeting",
            priority="low",
            lane="life" if is_personal else "work",
            draft_text=draft,
            details=details,
        )

    def _create_unconfident_suggestion(
        self, db, message, category: str, priority: str, confidence: int | None, lane: str
    ) -> None:
        """AI could not classify/draft this with enough confidence to trust — surface the raw
        message so the user knows it needs a look, but never auto-generate a reply for it.
        No draft_text is set; the UI shows the message with no send/edit action."""
        if repo.suggestion_exists_for_message(db, message.id):
            return
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="nudge",
            category=category,
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=None,
            details={
                "chip_label": "Low confidence — AI did not draft a reply, please respond manually.",
                "low_confidence": True,
            },
        )

    def _create_blank_suggestion(
        self, db, message, category: str, priority: str, lane: str, confidence: int | None
    ):
        """7-day silent observation (Rule 12): surface the message with NO AI-drafted text —
        draft_text stays None so the app shows an empty, editable reply box instead of hiding
        it outright. Whatever the owner types and sends goes through the normal reply path
        and is saved as a real outbound message, which is what future voice-example/tone
        learning reads from."""
        if repo.suggestion_exists_for_message(db, message.id):
            return None
        chip = wa_taxonomy.default_chip_label(category)
        return repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="reply",
            category=category,
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=None,
            details={
                "chip_label": chip or "New message — write your own reply below",
                "silent_observation": True,
            },
        )

    def _create_clarification_suggestion(
        self,
        db,
        message,
        category: str,
        priority: str,
        confidence: int | None,
        lane: str,
        question: str,
        options: list[str],
    ):
        """Rule 13 — ask ONE tap-option question instead of guessing wrong when the
        message is genuinely, concretely ambiguous (see classifier.py's CLARIFYING
        QUESTIONS rule). No draft_text until the user taps an option — see
        actions.answer_clarification, which regenerates the draft using their answer
        as context."""
        if repo.suggestion_exists_for_message(db, message.id):
            return None
        return repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="clarify",
            category=category,
            priority=priority,
            lane=lane,
            confidence=confidence,
            draft_text=None,
            details={
                "needs_clarification": True,
                "clarifying_question": question,
                "clarifying_options": options,
                "chip_label": _clarify_chip_label(question),
            },
        )

    def _create_safety_concern_suggestion(self, db, message, category: str, lane: str) -> None:
        """The message may indicate the sender is in danger or distress — always surface it,
        regardless of confidence or category, and never auto-generate a reply for it. This
        needs a human's personal attention, not a templated response."""
        if repo.suggestion_exists_for_message(db, message.id):
            return
        repo.create_suggestion(
            db,
            contact_id=message.contact_id,
            message_id=message.id,
            kind="nudge",
            category=category,
            priority="critical",
            lane=lane,
            confidence=None,
            draft_text=None,
            details={
                "chip_label": "Possible safety concern — please respond personally, do not send an automated reply.",
                "safety_concern": True,
            },
        )

    def _handle_voice_note(self, db, message) -> None:
        db.refresh(message)
        if message.classified_at is not None:
            return

        from app import models as _models
        contact = db.get(_models.WhatsAppContact, message.contact_id)
        contact_name = contact.profile_name if contact else None

        if contact is not None and contact.is_excluded:
            message.classified_at = datetime.utcnow()
            message.is_important = False
            message.category = "excluded"
            db.commit()
            return

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

    def _handle_media_message(self, db, message) -> None:
        db.refresh(message)
        if message.classified_at is not None:
            return

        from app import models as _models
        contact = db.get(_models.WhatsAppContact, message.contact_id)
        contact_name = contact.profile_name if contact else None

        if contact is not None and contact.is_excluded:
            message.classified_at = datetime.utcnow()
            message.is_important = False
            message.category = "excluded"
            db.commit()
            return

        message.classified_at = datetime.utcnow()
        message.is_important = False
        message.category = "media"
        message.priority = "low"

        if not repo.suggestion_exists_for_message(db, message.id):
            media_lane = (
                "life" if contact and getattr(contact, "contact_type", None) == "personal"
                else "work"
            )
            repo.create_suggestion(
                db,
                contact_id=message.contact_id,
                message_id=message.id,
                kind="nudge",
                category="media",
                priority="low",
                lane=media_lane,
                draft_text=None,
                details={
                    "chip_label": _media_chip_label(contact_name, message.msg_type),
                    "msg_type": message.msg_type,
                },
            )

        db.commit()
        logger.info("[WHATSAPP] Caption-less media flagged for contact %s (message=%s type=%s)",
                    message.contact_id, message.id, message.msg_type)

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
            plan = classifier.extract_family_plan(history, body)
        except WhatsAppAIError:
            plan = wa_fallback.extract_family_plan(body)

        confirmed = bool(plan.get("confirmed"))

        calendar_event_id = None
        calendar_html_link = None
        if (
            confirmed
            and plan.get("date")
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
                contact_name = message.contact.profile_name if message.contact else None
                summary = f"[Personal] {label}"
                if contact_name and contact_name.lower() not in summary.lower():
                    summary = f"{summary} — {contact_name}"
                description_parts = ["[Personal event — not work]"]
                if contact_name:
                    description_parts.append(f"With: {contact_name}")
                description = "\n".join(description_parts)

                payload = EventCreate(
                    summary=summary,
                    description=description,
                    location=plan.get("place"),
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
        elif confirmed and when_str:
            chip_label = f"{label} {when_str} — add to calendar?".strip()
        elif confirmed:
            chip_label = f"{label} — add to calendar?"
        elif when_str:
            chip_label = f"{label} {when_str} mentioned — no time confirmed yet".strip()
        else:
            chip_label = f"{label} mentioned — no time confirmed yet"

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

    def _check_work_awaiting_reply(self) -> None:
        """Client/work-lane counterpart to _check_personal_silence: proactively flag a
        client whose message you haven't answered, on elapsed time alone — not only when
        they re-ping you (that's the separate 'follow_up' category). Escalates from a
        plain flag past WHATSAPP_FOLLOWUP_FLAG_HOURS (24h) to urgent past
        WHATSAPP_FOLLOWUP_URGENT_HOURS (3 days)."""
        db = SessionLocal()
        try:
            contacts = repo.work_contacts_awaiting_reply(
                db, flag_hours=WHATSAPP_FOLLOWUP_FLAG_HOURS
            )
            for contact in contacts:
                hours_waiting = (
                    datetime.utcnow() - contact.last_inbound_at
                ).total_seconds() / 3600
                is_urgent_wait = hours_waiting >= WHATSAPP_FOLLOWUP_URGENT_HOURS
                priority = "very_high" if is_urgent_wait else "high"
                days = max(1, int(hours_waiting / 24))
                day_word = "day" if days == 1 else "days"
                display_name = (contact.profile_name or "").strip() or contact.wa_id
                chip_label = (
                    f"{display_name} has waited {days} {day_word} for your reply — urgent"
                    if is_urgent_wait
                    else f"You haven't replied to {display_name} in {days} {day_word}"
                )
                repo.create_suggestion(
                    db,
                    contact_id=contact.id,
                    message_id=None,
                    kind="followup_nudge",
                    category="awaiting_reply",
                    priority=priority,
                    lane="work",
                    draft_text=None,
                    details={
                        "chip_label": chip_label,
                        "hours_waiting": round(hours_waiting, 1),
                        "contact_name": display_name,
                    },
                )
                logger.info(
                    "[WHATSAPP] Awaiting-reply nudge raised for contact %s (%.1f hours, priority=%s)",
                    contact.id,
                    hours_waiting,
                    priority,
                )
        finally:
            db.close()

    def _check_pending_commitments(self) -> None:
        """Remind the owner about their own unfulfilled promises (pricing/documents they
        said they'd send) before the client has to chase them — the commitment
        counterpart to _check_work_awaiting_reply, which flags the client's side of an
        unanswered thread instead of the owner's own side. Re-uses the same 24h/3-day
        thresholds: first reminder past 24h unfulfilled, escalating to urgent past 3
        days, re-reminded every further 24h while still open."""
        db = SessionLocal()
        try:
            commitments = repo.commitments_awaiting_reminder(
                db,
                flag_hours=WHATSAPP_FOLLOWUP_FLAG_HOURS,
                reminder_interval_hours=WHATSAPP_FOLLOWUP_FLAG_HOURS,
            )
            for commitment in commitments:
                contact = commitment.contact
                if contact is None:
                    continue
                hours_waiting = (
                    datetime.utcnow() - commitment.created_at
                ).total_seconds() / 3600
                is_urgent_wait = hours_waiting >= WHATSAPP_FOLLOWUP_URGENT_HOURS
                priority = "very_high" if is_urgent_wait else "high"
                display_name = (contact.profile_name or "").strip() or contact.wa_id
                chip_label = (
                    f"Still haven't sent {display_name}: {commitment.label} — urgent"
                    if is_urgent_wait
                    else f"Reminder: you said you'd send {display_name} — {commitment.label}"
                )
                repo.create_suggestion(
                    db,
                    contact_id=commitment.contact_id,
                    message_id=commitment.message_id,
                    kind="commitment_reminder",
                    category="pending_commitment",
                    priority=priority,
                    lane="work",
                    draft_text=None,
                    details={
                        "chip_label": chip_label,
                        "commitment_label": commitment.label,
                        "commitment_type": commitment.commitment_type,
                        "hours_waiting": round(hours_waiting, 1),
                    },
                )
                commitment.last_reminded_at = datetime.utcnow()
                db.commit()
                logger.info(
                    "[WHATSAPP] Commitment reminder raised for contact %s (%.1f hours, "
                    "priority=%s): %s",
                    commitment.contact_id,
                    hours_waiting,
                    priority,
                    commitment.label,
                )
        finally:
            db.close()
