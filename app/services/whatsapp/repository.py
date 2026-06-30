import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import models
from app.config import WHATSAPP_CUSTOMER_WINDOW_HOURS

logger = logging.getLogger(__name__)


def upsert_contact(
    db: Session,
    *,
    wa_id: str,
    profile_name: str | None = None,
) -> models.WhatsAppContact:
    contact = (
        db.query(models.WhatsAppContact)
        .filter(models.WhatsAppContact.wa_id == wa_id)
        .one_or_none()
    )
    if contact is None:
        contact = models.WhatsAppContact(wa_id=wa_id, profile_name=profile_name)
        db.add(contact)
        db.flush()
    elif profile_name and contact.profile_name != profile_name:
        contact.profile_name = profile_name
    return contact


def get_contact_by_wa_id(db: Session, wa_id: str) -> models.WhatsAppContact | None:
    return (
        db.query(models.WhatsAppContact)
        .filter(models.WhatsAppContact.wa_id == wa_id)
        .one_or_none()
    )


def message_exists(db: Session, wa_message_id: str) -> bool:
    if not wa_message_id:
        return False
    return (
        db.query(models.WhatsAppMessage.id)
        .filter(models.WhatsAppMessage.wa_message_id == wa_message_id)
        .first()
        is not None
    )


def insert_message(
    db: Session,
    *,
    contact: models.WhatsAppContact,
    direction: str,
    msg_type: str,
    body: str | None,
    timestamp: datetime,
    wa_message_id: str | None = None,
    media_id: str | None = None,
    status: str | None = None,
    raw_payload: dict | None = None,
    is_group: bool = False,
    is_forwarded: bool = False,
) -> models.WhatsAppMessage:
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        wa_message_id=wa_message_id,
        direction=direction,
        msg_type=msg_type,
        is_group=is_group,
        is_forwarded=is_forwarded,
        body=body,
        media_id=media_id,
        status=status,
        timestamp=timestamp,
        raw_payload=json.dumps(raw_payload) if raw_payload is not None else None,
    )
    db.add(message)

    contact.last_message_at = timestamp
    if direction == "inbound":
        contact.last_inbound_at = timestamp
    elif direction == "outbound":
        contact.last_replied_at = timestamp
    db.flush()
    return message


def update_message_status(db: Session, wa_message_id: str, status: str) -> bool:
    message = (
        db.query(models.WhatsAppMessage)
        .filter(models.WhatsAppMessage.wa_message_id == wa_message_id)
        .one_or_none()
    )
    if message is None:
        return False
    message.status = status
    return True


def recent_history(
    db: Session,
    contact_id: int,
    *,
    limit: int,
    before_message_id: int | None = None,
) -> list[dict[str, str]]:
    query = db.query(models.WhatsAppMessage).filter(
        models.WhatsAppMessage.contact_id == contact_id
    )
    if before_message_id is not None:
        query = query.filter(models.WhatsAppMessage.id < before_message_id)
    rows = (
        query.order_by(models.WhatsAppMessage.id.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [{"direction": r.direction, "body": r.body or ""} for r in rows]


def mark_contact_personal(db: Session, contact_id: int) -> None:
    """Tag a contact as personal if they are not already typed (never overwrites 'work')."""
    contact = db.get(models.WhatsAppContact, contact_id)
    if contact and contact.contact_type is None:
        contact.contact_type = "personal"


def pending_life_nudge_exists(db: Session, contact_id: int) -> bool:
    """Whether a silence nudge is already pending for this personal contact."""
    return (
        db.query(models.WhatsAppSuggestion.id)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact_id,
            models.WhatsAppSuggestion.kind == "life_nudge",
            models.WhatsAppSuggestion.status == "pending",
        )
        .first()
        is not None
    )


def personal_contacts_awaiting_reply(
    db: Session,
    *,
    silence_hours: float,
    active_within_days: int = 30,
) -> list[models.WhatsAppContact]:
    cutoff = datetime.utcnow() - timedelta(hours=silence_hours)
    active_since = datetime.utcnow() - timedelta(days=active_within_days)
    contacts = (
        db.query(models.WhatsAppContact)
        .filter(
            models.WhatsAppContact.contact_type == "personal",
            models.WhatsAppContact.last_inbound_at.isnot(None),
            models.WhatsAppContact.last_inbound_at >= active_since,
            (
                models.WhatsAppContact.last_replied_at.is_(None)
                | (models.WhatsAppContact.last_replied_at <= cutoff)
            ),
        )
        .all()
    )
    return [c for c in contacts if not pending_life_nudge_exists(db, c.id)]


def contact_prior_message_count(db: Session, contact_id: int, *, exclude_message_id: int) -> int:
    return (
        db.query(models.WhatsAppMessage.id)
        .filter(
            models.WhatsAppMessage.contact_id == contact_id,
            models.WhatsAppMessage.id != exclude_message_id,
        )
        .count()
    )


def last_outbound_at(db: Session, contact_id: int) -> datetime | None:
    """Timestamp of the most recent outbound message (our reply) to a contact."""
    row = (
        db.query(models.WhatsAppMessage.timestamp)
        .filter(
            models.WhatsAppMessage.contact_id == contact_id,
            models.WhatsAppMessage.direction == "outbound",
        )
        .order_by(models.WhatsAppMessage.timestamp.desc())
        .first()
    )
    return row[0] if row else None


def next_unclassified_inbound(db: Session) -> models.WhatsAppMessage | None:
    return (
        db.query(models.WhatsAppMessage)
        .filter(
            models.WhatsAppMessage.direction == "inbound",
            models.WhatsAppMessage.classified_at.is_(None),
            models.WhatsAppMessage.msg_type == "text",
        )
        .order_by(models.WhatsAppMessage.id.asc())
        .first()
    )


def next_unclassified_voice_note(db: Session) -> models.WhatsAppMessage | None:
    """Oldest unclassified inbound audio/voice message (Phase 1 — content not analysed)."""
    return (
        db.query(models.WhatsAppMessage)
        .filter(
            models.WhatsAppMessage.direction == "inbound",
            models.WhatsAppMessage.classified_at.is_(None),
            models.WhatsAppMessage.msg_type.in_(("audio", "voice")),
        )
        .order_by(models.WhatsAppMessage.id.asc())
        .first()
    )


def previous_document_sent(
    db: Session,
    contact_id: int,
    document_type: str | None,
    *,
    exclude_message_id: int | None = None,
) -> models.WhatsAppSuggestion | None:
    """Most recent already-sent document suggestion to this contact for the same document.

    Returns the matching suggestion (so the caller can reference when it was sent) or None.
    Acts as lightweight per-contact memory of what we've already shared.
    """
    query = db.query(models.WhatsAppSuggestion).filter(
        models.WhatsAppSuggestion.contact_id == contact_id,
        models.WhatsAppSuggestion.kind == "document",
        models.WhatsAppSuggestion.status == "done",
    )
    if exclude_message_id is not None:
        query = query.filter(models.WhatsAppSuggestion.message_id != exclude_message_id)
    rows = query.order_by(models.WhatsAppSuggestion.resolved_at.desc()).all()

    wanted = (document_type or "").strip().lower()
    for row in rows:
        if not wanted:
            return row
        details = {}
        if row.details:
            try:
                details = json.loads(row.details)
            except json.JSONDecodeError:
                details = {}
        prev = str(details.get("document_type") or "").strip().lower()
        if prev and (prev == wanted or prev in wanted or wanted in prev):
            return row
    return None


def pending_nudge_exists(db: Session, contact_id: int) -> bool:
    """Whether a casual/greeting nudge is already pending for this contact."""
    return (
        db.query(models.WhatsAppSuggestion.id)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact_id,
            models.WhatsAppSuggestion.kind == "nudge",
            models.WhatsAppSuggestion.status == "pending",
        )
        .first()
        is not None
    )


def suggestion_exists_for_message(db: Session, message_id: int) -> bool:
    return (
        db.query(models.WhatsAppSuggestion.id)
        .filter(models.WhatsAppSuggestion.message_id == message_id)
        .first()
        is not None
    )


def create_suggestion(
    db: Session,
    *,
    contact_id: int,
    message_id: int | None,
    kind: str,
    category: str | None,
    priority: str = "normal",
    lane: str = "work",
    confidence: int | None = None,
    draft_text: str | None = None,
    details: dict | None = None,
) -> models.WhatsAppSuggestion:
    suggestion = models.WhatsAppSuggestion(
        contact_id=contact_id,
        message_id=message_id,
        kind=kind,
        category=category,
        priority=priority,
        lane=lane,
        confidence=confidence,
        status="pending",
        draft_text=draft_text,
        details=json.dumps(details) if details is not None else None,
    )
    db.add(suggestion)
    db.flush()
    return suggestion

def record_feedback(
    db: Session,
    *,
    suggestion_id: int,
    feedback_type: str,
    original_category: str | None,
    original_confidence: int | None,
    message_snippet: str | None,
    contact_id: int | None = None,
    message_id: int | None = None,
) -> models.WhatsAppFeedback:
    """Persist a user correction. Called by the feedback API endpoint."""
    feedback = models.WhatsAppFeedback(
        suggestion_id=suggestion_id,
        contact_id=contact_id,
        message_id=message_id,
        feedback_type=feedback_type,
        original_category=original_category,
        original_confidence=original_confidence,
        message_snippet=(message_snippet or "")[:300] or None,
    )
    db.add(feedback)
    db.flush()
    return feedback


def load_corrections_for_prompt(
    db: Session,
    *,
    limit: int = 20,
) -> list[dict]:
    rows = (
        db.query(models.WhatsAppFeedback)
        .filter(models.WhatsAppFeedback.feedback_type == "wrong")
        .order_by(models.WhatsAppFeedback.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "original_category": r.original_category,
            "original_confidence": r.original_confidence,
            "message_snippet": r.message_snippet,
        }
        for r in rows
    ]


def list_contacts(
    db: Session,
    *,
    limit: int,
    offset: int,
) -> tuple[list[models.WhatsAppContact], int]:
    base = db.query(models.WhatsAppContact)
    total = base.count()
    items = (
        base.order_by(models.WhatsAppContact.last_message_at.desc().nullslast())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return items, total


def list_messages(
    db: Session,
    *,
    contact_id: int,
    limit: int,
    offset: int,
) -> tuple[list[models.WhatsAppMessage], int]:
    base = db.query(models.WhatsAppMessage).filter(
        models.WhatsAppMessage.contact_id == contact_id
    )
    total = base.count()
    items = (
        base.order_by(models.WhatsAppMessage.timestamp.asc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return items, total


def list_suggestions(
    db: Session,
    *,
    status: str | None,
    kind: str | None,
    lane: str | None,
    contact_id: int | None,
    limit: int,
    offset: int,
) -> tuple[list[models.WhatsAppSuggestion], int]:
    base = db.query(models.WhatsAppSuggestion)
    if status:
        base = base.filter(models.WhatsAppSuggestion.status == status)
    if kind:
        base = base.filter(models.WhatsAppSuggestion.kind == kind)
    if lane:
        base = base.filter(models.WhatsAppSuggestion.lane == lane)
    if contact_id is not None:
        base = base.filter(models.WhatsAppSuggestion.contact_id == contact_id)
    total = base.count()
    items = (
        base.order_by(models.WhatsAppSuggestion.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return items, total


def get_suggestion(db: Session, suggestion_id: int) -> models.WhatsAppSuggestion | None:
    return db.get(models.WhatsAppSuggestion, suggestion_id)


def within_customer_window(contact: models.WhatsAppContact, *, now: datetime | None = None) -> bool:
    if contact.last_inbound_at is None:
        return False
    now = now or datetime.utcnow()
    return now - contact.last_inbound_at <= timedelta(hours=WHATSAPP_CUSTOMER_WINDOW_HOURS)


def within_reply_window(
    db: Session,
    contact: models.WhatsAppContact,
    suggestion: models.WhatsAppSuggestion | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """True when a free-form WhatsApp text reply is allowed."""
    if within_customer_window(contact, now=now):
        return True
    if suggestion is None or suggestion.message_id is None:
        return False
    message = db.get(models.WhatsAppMessage, suggestion.message_id)
    if message is None or message.timestamp is None:
        return False
    now = now or datetime.utcnow()
    return now - message.timestamp <= timedelta(hours=WHATSAPP_CUSTOMER_WINDOW_HOURS)
