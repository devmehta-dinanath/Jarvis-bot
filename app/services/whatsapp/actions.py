import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import models
from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    WHATSAPP_DEFAULT_MEETING_MINUTES,
    WHATSAPP_MEETING_CONFIRMATION_TEMPLATE,
    WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE,
)
from app.services.google_calendar.schemas import EventCreate, EventDateTime
from app.services.google_calendar.service import google_calendar_service
from app.services.whatsapp import client as wa_client
from app.services.whatsapp import repository as repo

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
    if suggestion.kind != "meeting":
        raise WhatsAppActionError("Only meeting suggestions can be added to the calendar")

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
