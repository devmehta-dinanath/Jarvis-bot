import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import models
from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    WHATSAPP_CORRECTIONS_CONTEXT_LIMIT,
    WHATSAPP_DEFAULT_MEETING_MINUTES,
    WHATSAPP_HISTORY_CONTEXT_LIMIT,
    WHATSAPP_MEETING_CONFIRMATION_TEMPLATE,
    WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE,
)
from app.services.google_calendar.schemas import EventCreate, EventDateTime
from app.services.google_calendar.service import google_calendar_service
from app.services.whatsapp import classifier
from app.services.whatsapp import client as wa_client
from app.services.whatsapp import repository as repo
from app.services.whatsapp import taxonomy as wa_taxonomy

logger = logging.getLogger(__name__)


class WhatsAppActionError(Exception):
    """A WhatsApp action (send/calendar) could not be completed."""


def _details_dict(suggestion: models.WhatsAppSuggestion) -> dict:
    if not suggestion.details:
        return {}
    try:
        data = json.loads(suggestion.details)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def send_message(
    db: Session,
    *,
    contact: models.WhatsAppContact,
    mode: str,
    text: str | None = None,
    template_name: str | None = None,
    template_language: str | None = None,
    template_components: list[dict] | None = None,
) -> models.WhatsAppMessage:
    """Send a message via the Graph API and persist it as an outbound row."""
    if mode == "template":
        if not template_name:
            raise WhatsAppActionError("template_name is required for template messages")
        wa_message_id, _ = wa_client.send_template(
            contact.wa_id,
            template_name,
            language=template_language,
            components=template_components,
        )
        body = f"[template: {template_name}]"
        msg_type = "template"
    else:
        if not text:
            raise WhatsAppActionError("text is required for free-form messages")
        wa_message_id, _ = wa_client.send_text(contact.wa_id, text)
        body = text
        msg_type = "text"

    message = repo.insert_message(
        db,
        contact=contact,
        direction="outbound",
        msg_type=msg_type,
        body=body,
        timestamp=datetime.utcnow(),
        wa_message_id=wa_message_id,
        status="sent",
    )
    db.commit()
    db.refresh(message)
    return message


def send_reply(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    text: str | None = None,
    mode: str = "auto",
    template_name: str | None = None,
    template_language: str | None = None,
    template_components: list[dict] | None = None,
) -> models.WhatsAppMessage:
    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    if contact is None:
        raise WhatsAppActionError("Contact not found for suggestion")

    reply_text = text or suggestion.draft_text
    resolved_mode = mode
    if mode == "auto":
        in_window = repo.within_reply_window(db, contact, suggestion)
        resolved_mode = "text" if in_window else "template"

    if resolved_mode == "template" and not template_name:
        raise WhatsAppActionError(
            "Outside the customer service window — a template_name is required to reply."
        )

    original_message = (
        db.get(models.WhatsAppMessage, suggestion.message_id)
        if suggestion.message_id is not None
        else None
    )

    # Rule 12 — continuous learning: capture whether the user sent the AI's draft as-is
    # (positive reinforcement) or changed it (a style correction), before translation
    # mutates reply_text below. Only meaningful when there was an actual draft to compare
    # against — not for a suggestion with no draft_text (e.g. a low-confidence nudge).
    if suggestion.draft_text and reply_text is not None:
        approved_text = (text if text is not None else suggestion.draft_text).strip()
        was_edited = approved_text != suggestion.draft_text.strip()
        try:
            repo.record_feedback(
                db,
                suggestion_id=suggestion.id,
                feedback_type="edited" if was_edited else "helpful",
                original_category=suggestion.category,
                original_confidence=suggestion.confidence,
                message_snippet=original_message.body if original_message else None,
                contact_id=suggestion.contact_id,
                message_id=suggestion.message_id,
                correct_response=approved_text if was_edited else None,
            )
        except Exception:
            logger.exception(
                "[WHATSAPP] Failed to record style-learning feedback for suggestion %s",
                suggestion.id,
            )

    # The user always writes/edits the reply in English; auto-translate into the contact's
    # own detected language (from the message this suggestion is replying to) right before
    # sending, so the contact always receives their own language. Falls back to the English
    # text if translation fails — better to send something than nothing.
    if reply_text and original_message is not None:
        reply_language = original_message.language if original_message else None
        if reply_language and not classifier.is_english(reply_language):
            try:
                reply_text = classifier.translate_reply_to_language(reply_text, reply_language)
            except classifier.WhatsAppAIError:
                logger.warning(
                    "[WHATSAPP] Reply translation to %s failed for suggestion %s — "
                    "sending English text",
                    reply_language,
                    suggestion.id,
                )

    message = send_message(
        db,
        contact=contact,
        mode=resolved_mode,
        text=reply_text,
        template_name=template_name,
        template_language=template_language,
        template_components=template_components,
    )

    suggestion.status = "done"
    suggestion.resolved_at = datetime.utcnow()
    suggestion.sent_message_id = message.id
    db.commit()
    db.refresh(suggestion)

    # Replying means the conversation up to this point is handled — clear other
    # pending chips for messages the user already saw. Anything newer stays.
    replied_to_at = None
    if suggestion.message_id is not None:
        replied_message = db.get(models.WhatsAppMessage, suggestion.message_id)
        if replied_message is not None:
            replied_to_at = replied_message.timestamp
    if replied_to_at is None:
        replied_to_at = suggestion.created_at
    cleared = repo.dismiss_pending_older_than(db, contact.id, replied_to_at)
    if cleared:
        db.commit()
        logger.info(
            "[WHATSAPP] Auto-dismissed %s older pending suggestion(s) for contact %s "
            "after reply to suggestion %s",
            cleared,
            contact.id,
            suggestion.id,
        )

    return message


def answer_clarification(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    answer: str,
) -> models.WhatsAppSuggestion:
    """Rule 13 — the user tapped one of the clarifying tap options; regenerate the
    draft now that the ambiguity is resolved, using their answer as direct context.
    The suggestion row itself doesn't change identity — it just gains a draft_text
    it didn't have before, exactly like any other suggestion once classified."""
    if suggestion.kind != "clarify":
        raise WhatsAppActionError("This suggestion is not a clarifying question")

    answer = (answer or "").strip()
    if not answer:
        raise WhatsAppActionError("An answer is required")

    details = _details_dict(suggestion)
    question = details.get("clarifying_question") or "the earlier question"

    if suggestion.message_id is None:
        raise WhatsAppActionError("Original message not found for this suggestion")
    message = db.get(models.WhatsAppMessage, suggestion.message_id)
    if message is None:
        raise WhatsAppActionError("Original message not found for this suggestion")

    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    is_personal = bool(contact is not None and contact.contact_type == "personal")

    history = repo.recent_history(
        db, suggestion.contact_id, limit=WHATSAPP_HISTORY_CONTEXT_LIMIT, before_message_id=message.id
    )
    instructions = [i.text for i in repo.list_instructions(db, active_only=True)]
    corrections = repo.load_corrections_for_prompt(db, limit=WHATSAPP_CORRECTIONS_CONTEXT_LIMIT)
    voice_examples = repo.recent_outbound_examples(db, personal=is_personal)

    context_hint = (
        f"You previously asked the user: \"{question}\" because the client's message was "
        f"ambiguous. The user answered: \"{answer}\". Use that answer as the specific, "
        "correct context for this reply — do not ask the question again or hedge."
    )

    try:
        if suggestion.category == "complaint":
            draft = classifier.draft_complaint_reply(
                history,
                message.body or "",
                details.get("anger_level"),
                language=message.language,
                translation=message.translation,
                instructions=instructions,
                corrections=corrections,
                voice_examples=voice_examples,
            )
        else:
            draft = classifier.draft_reply(
                history,
                message.body or "",
                suggestion.category or "other",
                context_hint=context_hint,
                language=message.language,
                translation=message.translation,
                instructions=instructions,
                corrections=corrections,
                personal=is_personal,
                voice_examples=voice_examples,
            )
    except classifier.WhatsAppAIError:
        logger.warning(
            "[WHATSAPP] Drafting after clarification failed for suggestion %s — using fallback",
            suggestion.id,
        )
        draft = "Got it, thanks for confirming — I'll follow up on that shortly."

    details["needs_clarification"] = False
    details["clarified_answer"] = answer
    details["chip_label"] = wa_taxonomy.default_chip_label(suggestion.category) or "Ready to send"
    suggestion.details = json.dumps(details)
    suggestion.draft_text = draft
    # The user just actively engaged with this — show the result immediately rather
    # than making them wait out whatever Rule 9 delay the original message got.
    suggestion.visible_after = None
    db.commit()
    db.refresh(suggestion)
    return suggestion


def redraft_with_correction(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    correct_response: str,
) -> models.WhatsAppSuggestion:
    """The user tapped Wrong and typed what the reply should have said instead. This is
    an internal note, never sent to the contact — it only updates the draft shown in the
    UI (the caller is responsible for persisting the correction to the learning DB via
    repo.record_feedback). Mirrors answer_clarification's regenerate-in-place pattern."""
    correct_response = (correct_response or "").strip()
    if not correct_response:
        raise WhatsAppActionError("A correction is required")

    if suggestion.message_id is None:
        raise WhatsAppActionError("Original message not found for this suggestion")
    message = db.get(models.WhatsAppMessage, suggestion.message_id)
    if message is None:
        raise WhatsAppActionError("Original message not found for this suggestion")

    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    is_personal = bool(contact is not None and contact.contact_type == "personal")

    history = repo.recent_history(
        db, suggestion.contact_id, limit=WHATSAPP_HISTORY_CONTEXT_LIMIT, before_message_id=message.id
    )
    instructions = [i.text for i in repo.list_instructions(db, active_only=True)]
    corrections = repo.load_corrections_for_prompt(db, limit=WHATSAPP_CORRECTIONS_CONTEXT_LIMIT)
    voice_examples = repo.recent_outbound_examples(db, personal=is_personal)

    context_hint = (
        f"The previous draft reply was WRONG. The user said the reply should instead say: "
        f"\"{correct_response}\". Rewrite the reply to match what the user said — do not "
        "repeat the old wrong content."
    )

    try:
        if suggestion.category == "complaint":
            draft = classifier.draft_complaint_reply(
                history,
                message.body or "",
                _details_dict(suggestion).get("anger_level"),
                language=message.language,
                translation=message.translation,
                instructions=instructions,
                corrections=corrections,
                voice_examples=voice_examples,
                context_hint=context_hint,
            )
        else:
            draft = classifier.draft_reply(
                history,
                message.body or "",
                suggestion.category or "other",
                context_hint=context_hint,
                language=message.language,
                translation=message.translation,
                instructions=instructions,
                corrections=corrections,
                personal=is_personal,
                voice_examples=voice_examples,
            )
    except classifier.WhatsAppAIError:
        logger.warning(
            "[WHATSAPP] Redrafting after correction failed for suggestion %s — keeping "
            "prior draft and falling back to the user's own wording",
            suggestion.id,
        )
        draft = correct_response

    suggestion.draft_text = draft
    suggestion.visible_after = None
    db.commit()
    db.refresh(suggestion)
    return suggestion


def forward_to_team(db: Session, suggestion: models.WhatsAppSuggestion) -> dict:
    """Rule 14 — one-tap forward of the original client message to the assigned team
    member's WhatsApp, using the same send path as any other outbound message. No
    copy/paste, no switching apps — this is a real WAHA/Cloud API send, not a mailto-
    style deep link."""
    details = _details_dict(suggestion)
    if details.get("forwarded_to"):
        raise WhatsAppActionError("This was already forwarded")

    rule = repo.find_forwarding_rule(
        db, category=suggestion.category, payment_status=details.get("payment_status")
    )
    if rule is None:
        raise WhatsAppActionError(
            "No team member is assigned for this category yet — add one in Settings."
        )

    if suggestion.message_id is None:
        raise WhatsAppActionError("Original message not found for this suggestion")
    message = db.get(models.WhatsAppMessage, suggestion.message_id)
    if message is None:
        raise WhatsAppActionError("Original message not found for this suggestion")

    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    contact_name = (contact.profile_name if contact else None) or (
        contact.wa_id if contact else "Unknown"
    )
    forward_text = f'Forwarded from {contact_name}:\n"{(message.body or "").strip()}"'

    team_contact = repo.upsert_contact(
        db, wa_id=rule.team_member_wa_id, profile_name=rule.team_member_name, is_group=False
    )
    # Team members are an internal send target, not a client conversation — never let a
    # reply from this number get pulled into the classifier/suggestion pipeline.
    if team_contact.contact_type is None:
        team_contact.contact_type = "team"
    if not team_contact.is_excluded:
        team_contact.is_excluded = True
    db.flush()

    sent = send_message(db, contact=team_contact, mode="text", text=forward_text)

    details["forwarded_to"] = rule.label
    details["forwarded_team_member"] = rule.team_member_name
    details["forwarded_at"] = datetime.utcnow().isoformat()
    suggestion.details = json.dumps(details)
    db.commit()
    db.refresh(suggestion)

    return {
        "ok": True,
        "forwarded_to": rule.label,
        "team_member_name": rule.team_member_name,
        "sent_message_id": sent.id,
    }


def _to_calendar_iso(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(microsecond=0).isoformat() + "Z"
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_meeting_when(start_dt: datetime) -> str:
    return (
        start_dt.strftime("%a %-d %b %-I:%M%p")
        .replace("AM", "am")
        .replace("PM", "pm")
    )


def _meeting_confirmation_text(start_dt: datetime | None, link: str | None) -> str:
    if start_dt:
        when = _format_meeting_when(start_dt)
        text = f"Yes, happy to connect! I've scheduled our meeting for {when}."
    else:
        text = "Yes, happy to connect! I've added our meeting to the calendar."
    if link:
        text += f" Here's the link: {link}"
    return text


def _meeting_template_components(start_dt: datetime | None, link: str | None) -> list[dict]:
    when = _format_meeting_when(start_dt) if start_dt else "soon"
    return [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": when},
                {"type": "text", "text": link or "—"},
            ],
        }
    ]


def _send_meeting_confirmation(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    text: str,
    start_dt: datetime | None,
    link: str | None,
) -> models.WhatsAppMessage:
    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    if contact is None:
        raise WhatsAppActionError("Contact not found for suggestion")

    if repo.within_reply_window(db, contact, suggestion):
        return send_reply(db, suggestion, text=text, mode="text")

    if WHATSAPP_MEETING_CONFIRMATION_TEMPLATE:
        return send_reply(
            db,
            suggestion,
            mode="template",
            template_name=WHATSAPP_MEETING_CONFIRMATION_TEMPLATE,
            template_language=WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE,
            template_components=_meeting_template_components(start_dt, link),
        )

    raise WhatsAppActionError(
        "Outside the 24-hour WhatsApp reply window. Set "
        "WHATSAPP_MEETING_CONFIRMATION_TEMPLATE in .env, or tap Send reply "
        "while the client is within the window."
    )


def add_to_calendar(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    title: str | None = None,
    agenda: str | None = None,
    start: str | None = None,
    end: str | None = None,
    calendar_id: str | None = None,
    conference: bool = False,
    send_confirmation: bool = True,
) -> dict:
    if suggestion.category not in ("meeting", "family_plan"):
        raise WhatsAppActionError(
            "Only meeting or family plan suggestions can be added to the calendar"
        )

    if suggestion.category == "family_plan":
        return _add_family_plan_to_calendar(db, suggestion, calendar_id=calendar_id)

    details = _details_dict(suggestion)
    if details.get("calendar_event_id"):
        raise WhatsAppActionError("This meeting is already on the calendar")

    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    contact_name = (contact.profile_name if contact else None) or (
        contact.wa_id if contact else None
    )

    event_title = title or details.get("title") or "Meeting with client"
    if contact_name and contact_name.lower() not in event_title.lower():
        event_title = f"{event_title} — {contact_name}"

    event_agenda = agenda or details.get("agenda")
    if contact_name:
        contact_line = f"With: {contact_name}"
        event_agenda = f"{event_agenda}\n\n{contact_line}" if event_agenda else contact_line

    start_dt = _parse_iso(start) or _parse_iso(details.get("start"))
    if start_dt is None:
        raise WhatsAppActionError(
            "No meeting start time available; provide 'start' (ISO 8601) to schedule."
        )

    end_dt = _parse_iso(end) or _parse_iso(details.get("end"))
    if end_dt is None or end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=WHATSAPP_DEFAULT_MEETING_MINUTES)

    payload = EventCreate(
        summary=event_title,
        description=event_agenda,
        start=EventDateTime(
            date_time=_to_calendar_iso(start_dt),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        end=EventDateTime(
            date_time=_to_calendar_iso(end_dt),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        conference=conference,
    )
    event = google_calendar_service.create_event(payload, calendar_id=calendar_id)

    meet_link = event.get("hangoutLink") or event.get("htmlLink")
    details["calendar_event_id"] = event.get("id")
    details["calendar_html_link"] = event.get("htmlLink")
    if meet_link:
        details["meet_link"] = meet_link
    suggestion.details = json.dumps(details)
    db.commit()
    db.refresh(suggestion)

    reply_sent = False
    reply_error: str | None = None
    sent_message_id: int | None = None
    if send_confirmation:
        confirmation = _meeting_confirmation_text(start_dt, meet_link)
        suggestion.draft_text = confirmation
        db.commit()
        db.refresh(suggestion)
        try:
            sent = _send_meeting_confirmation(
                db,
                suggestion,
                text=confirmation,
                start_dt=start_dt,
                link=meet_link,
            )
            reply_sent = True
            sent_message_id = sent.id
        except Exception as exc:
            reply_error = str(exc)
            logger.exception(
                "[WHATSAPP] Calendar event created but confirmation reply failed "
                "for suggestion %s",
                suggestion.id,
            )
    else:
        suggestion.status = "done"
        suggestion.resolved_at = datetime.utcnow()
        db.commit()
        db.refresh(suggestion)

    if reply_sent or not send_confirmation:
        pass  # send_reply or the no-confirmation branch already marked done
    elif send_confirmation:
        suggestion.status = "pending"
        suggestion.resolved_at = None
        db.commit()
        db.refresh(suggestion)

    logger.info(
        "[WHATSAPP] Created calendar event %s for suggestion %s (reply_sent=%s)",
        event.get("id"),
        suggestion.id,
        reply_sent,
    )
    event["reply_sent"] = reply_sent
    if reply_error:
        event["reply_error"] = reply_error
    if sent_message_id is not None:
        event["sent_message_id"] = sent_message_id
    return event


def _add_family_plan_to_calendar(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    calendar_id: str | None = None,
) -> dict:
    """Create a calendar event for a confirmed personal plan (lunch/dinner/outing).

    Unlike a client meeting, this never requests a conference link and never sends a
    WhatsApp confirmation — a calendar invite for a family plan shouldn't trigger an
    automated message the way a client meeting confirmation does.
    """
    details = _details_dict(suggestion)
    if details.get("calendar_event_id"):
        raise WhatsAppActionError("This plan is already on the calendar")
    if not details.get("confirmed"):
        raise WhatsAppActionError(
            "This plan is not yet confirmed by both sides of the conversation"
        )

    event_date = details.get("date")
    if not event_date:
        raise WhatsAppActionError("No date available for this plan; cannot schedule it.")

    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    contact_name = (contact.profile_name if contact else None) or (
        contact.wa_id if contact else None
    )

    start_time = details.get("time") or "09:00"
    start_iso = f"{event_date}T{start_time}:00"
    start_dt = _parse_iso(start_iso)
    if start_dt is None:
        raise WhatsAppActionError("Could not parse the plan's date/time.")
    end_dt = start_dt + timedelta(hours=2)

    label = details.get("event_label") or "Family plan"
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
        location=details.get("place"),
        start=EventDateTime(
            date_time=start_iso,
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        end=EventDateTime(
            date_time=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        conference=False,
    )
    event = google_calendar_service.create_event(payload, calendar_id=calendar_id)

    details["calendar_event_id"] = event.get("id")
    details["calendar_html_link"] = event.get("htmlLink")
    suggestion.details = json.dumps(details)
    suggestion.status = "done"
    suggestion.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(suggestion)

    logger.info(
        "[WHATSAPP] Created family plan calendar event %s for suggestion %s",
        event.get("id"),
        suggestion.id,
    )
    event["reply_sent"] = False
    return event


def _extract_candidate_datetime(details: dict) -> datetime | None:
    """Best-effort date/time already sitting on a suggestion's details, regardless of
    category — meeting/timeline extraction uses a 'start' ISO string, family_plan/
    personal_date/personal_task use separate 'date' (+ optional 'time') fields."""
    start_dt = _parse_iso(details.get("start"))
    if start_dt is not None:
        return start_dt

    date_str = details.get("date") or details.get("deadline_date")
    if date_str:
        time_str = details.get("time") or "09:00"
        return _parse_iso(f"{date_str}T{time_str}:00")

    return None


def set_reminder(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    remind_at: str | None = None,
    title: str | None = None,
    calendar_id: str | None = None,
) -> dict:
    """A personal reminder for ANY suggestion, any category — unlike add_to_calendar this
    never requires the other side of the conversation to have confirmed anything, never
    invites the contact, and never sends a WhatsApp message. It's purely a calendar entry
    (with Google Calendar's own notification) for the account owner. Uses the message's own
    extracted date/time when one is available (meeting/timeline/family_plan/personal_date/
    personal_task all already populate one of 'start' or 'date'+'time' in details); falls
    back to 24 hours from now — "remind me about this tomorrow" — when it isn't (e.g. a
    payment nudge with no date mentioned at all)."""
    details = _details_dict(suggestion)
    if details.get("reminder_event_id"):
        raise WhatsAppActionError("A reminder is already set for this")

    start_dt = _parse_iso(remind_at) or _extract_candidate_datetime(details)
    if start_dt is None:
        start_dt = datetime.utcnow() + timedelta(hours=24)
    end_dt = start_dt + timedelta(minutes=15)

    contact = db.get(models.WhatsAppContact, suggestion.contact_id)
    contact_name = (contact.profile_name if contact else None) or (
        contact.wa_id if contact else None
    )
    label = (
        title
        or details.get("event_label")
        or details.get("chip_label")
        or wa_taxonomy.default_chip_label(suggestion.category)
        or "Reminder"
    )
    summary = f"[Reminder] {label}"
    if contact_name and contact_name.lower() not in summary.lower():
        summary = f"{summary} — {contact_name}"

    original_message = (
        db.get(models.WhatsAppMessage, suggestion.message_id)
        if suggestion.message_id is not None
        else None
    )
    description_parts = []
    if contact_name:
        description_parts.append(f"With: {contact_name}")
    if original_message is not None and (original_message.body or "").strip():
        description_parts.append(f'Message: "{original_message.body.strip()}"')
    description_parts.append(
        "Personal reminder set from Personal OS — no message was sent to the contact."
    )
    description = "\n\n".join(description_parts)

    payload = EventCreate(
        summary=summary,
        description=description,
        start=EventDateTime(
            date_time=_to_calendar_iso(start_dt),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        end=EventDateTime(
            date_time=_to_calendar_iso(end_dt),
            time_zone=CALENDAR_DEFAULT_TIMEZONE,
        ),
        conference=False,
    )
    event = google_calendar_service.create_event(payload, calendar_id=calendar_id)

    details["reminder_event_id"] = event.get("id")
    details["reminder_html_link"] = event.get("htmlLink")
    details["reminder_at"] = _to_calendar_iso(start_dt)
    suggestion.details = json.dumps(details)
    db.commit()
    db.refresh(suggestion)

    logger.info(
        "[WHATSAPP] Set reminder (event %s) for suggestion %s at %s",
        event.get("id"), suggestion.id, start_dt,
    )
    event["reminder_at"] = details["reminder_at"]
    return event
