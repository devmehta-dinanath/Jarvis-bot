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
) -> models.WhatsAppMessage:
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        wa_message_id=wa_message_id,
        direction=direction,
        msg_type=msg_type,
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
    message_id: int,
    kind: str,
    category: str | None,
    draft_text: str | None = None,
    details: dict | None = None,
) -> models.WhatsAppSuggestion:
    suggestion = models.WhatsAppSuggestion(
        contact_id=contact_id,
        message_id=message_id,
        kind=kind,
        category=category,
        status="pending",
        draft_text=draft_text,
        details=json.dumps(details) if details is not None else None,
    )
    db.add(suggestion)
    db.flush()
    return suggestion


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
    contact_id: int | None,
    limit: int,
    offset: int,
) -> tuple[list[models.WhatsAppSuggestion], int]:
    base = db.query(models.WhatsAppSuggestion)
    if status:
        base = base.filter(models.WhatsAppSuggestion.status == status)
    if kind:
        base = base.filter(models.WhatsAppSuggestion.kind == kind)
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
