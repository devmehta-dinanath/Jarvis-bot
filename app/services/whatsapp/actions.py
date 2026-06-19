import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import models
from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    WHATSAPP_DEFAULT_MEETING_MINUTES,
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
        resolved_mode = "text" if repo.within_customer_window(contact) else "template"

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
) -> dict:
    if suggestion.kind != "meeting":
        raise WhatsAppActionError("Only meeting suggestions can be added to the calendar")

    details = _details_dict(suggestion)
    if details.get("calendar_event_id"):
        raise WhatsAppActionError("This meeting is already on the calendar")
    event_title = title or details.get("title") or "Meeting with client"
    event_agenda = agenda or details.get("agenda")

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

    details["calendar_event_id"] = event.get("id")
    details["calendar_html_link"] = event.get("htmlLink")
    suggestion.details = json.dumps(details)
    suggestion.status = "done"
    suggestion.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(suggestion)

    logger.info(
        "[WHATSAPP] Created calendar event %s for suggestion %s",
        event.get("id"),
        suggestion.id,
    )
    return event
