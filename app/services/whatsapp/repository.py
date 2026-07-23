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
    is_group: bool = False,
) -> models.WhatsAppContact:
    # The wa_id suffix is unambiguous ground truth for group/newsletter JIDs —
    # never rely solely on a message-level heuristic that may have missed it
    # (or on a stale is_group=0 backfilled before this column existed).
    if wa_id.endswith("@g.us") or wa_id.endswith("@newsletter"):
        is_group = True

    contact = (
        db.query(models.WhatsAppContact)
        .filter(models.WhatsAppContact.wa_id == wa_id)
        .one_or_none()
    )
    if contact is None:
        contact = models.WhatsAppContact(
            wa_id=wa_id, profile_name=profile_name, is_group=is_group
        )
        db.add(contact)
        db.flush()
    else:
        if profile_name and contact.profile_name != profile_name:
            contact.profile_name = profile_name
        if is_group and not contact.is_group:
            contact.is_group = True
    return contact


def get_contact_by_wa_id(db: Session, wa_id: str) -> models.WhatsAppContact | None:
    return (
        db.query(models.WhatsAppContact)
        .filter(models.WhatsAppContact.wa_id == wa_id)
        .one_or_none()
    )


def dismiss_pending_for_contact(db: Session, contact_id: int) -> int:
    now = datetime.utcnow()
    return (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact_id,
            models.WhatsAppSuggestion.status == "pending",
        )
        .update({"status": "dismissed", "resolved_at": now}, synchronize_session=False)
    )


def dismiss_pending_older_than(db: Session, contact_id: int, cutoff: datetime) -> int:
    """Auto-clear pending suggestions the user has already moved past by replying.

    Only suggestions tied to a message strictly before `cutoff` are cleared — chips
    for messages that arrived *after* the one just replied to are left pending, since
    the user hasn't seen/handled those yet.
    """
    now = datetime.utcnow()
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact_id,
            models.WhatsAppSuggestion.status == "pending",
        )
        .all()
    )
    updated = 0
    for suggestion in pending:
        ts = None
        if suggestion.message_id is not None:
            message = db.get(models.WhatsAppMessage, suggestion.message_id)
            if message is not None:
                ts = message.timestamp
        if ts is None:
            ts = suggestion.created_at
        if ts is not None and ts < cutoff:
            suggestion.status = "dismissed"
            suggestion.resolved_at = now
            updated += 1
    return updated


def set_contact_excluded(
    db: Session, wa_id: str, excluded: bool
) -> models.WhatsAppContact | None:
    """Toggle "Stop reading" for a contact/group. Excluding also clears its pending chips."""
    contact = get_contact_by_wa_id(db, wa_id)
    if contact is None:
        return None
    contact.is_excluded = excluded
    if excluded:
        dismiss_pending_for_contact(db, contact.id)
    db.flush()
    return contact


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
    """Whether a silence nudge has already been raised for this contact's current
    silence period (pending or dismissed) — dismissing must stop the nudge from
    being immediately recreated on the next poll. A new nudge becomes possible
    again once the contact sends a fresh inbound message.
    """
    contact = db.get(models.WhatsAppContact, contact_id)
    query = db.query(models.WhatsAppSuggestion.id).filter(
        models.WhatsAppSuggestion.contact_id == contact_id,
        models.WhatsAppSuggestion.kind == "life_nudge",
    )
    if contact is not None and contact.last_inbound_at is not None:
        query = query.filter(
            models.WhatsAppSuggestion.created_at >= contact.last_inbound_at
        )
    return query.first() is not None


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
    visible_after: datetime | None = None,
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
        visible_after=visible_after,
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
    correct_response: str | None = None,
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
        correct_response=(correct_response or "").strip()[:1000] or None,
    )
    db.add(feedback)
    db.flush()
    return feedback


def list_instructions(
    db: Session, *, active_only: bool = False
) -> list[models.UserInstruction]:
    query = db.query(models.UserInstruction)
    if active_only:
        query = query.filter(models.UserInstruction.is_active.is_(True))
    return query.order_by(models.UserInstruction.created_at.asc()).all()


def create_instruction(db: Session, text: str) -> models.UserInstruction:
    instruction = models.UserInstruction(text=text.strip())
    db.add(instruction)
    db.flush()
    return instruction


def update_instruction(
    db: Session,
    instruction_id: int,
    *,
    text: str | None = None,
    is_active: bool | None = None,
) -> models.UserInstruction | None:
    instruction = db.get(models.UserInstruction, instruction_id)
    if instruction is None:
        return None
    if text is not None:
        instruction.text = text.strip()
    if is_active is not None:
        instruction.is_active = is_active
    instruction.updated_at = datetime.utcnow()
    db.flush()
    return instruction


def delete_instruction(db: Session, instruction_id: int) -> bool:
    instruction = db.get(models.UserInstruction, instruction_id)
    if instruction is None:
        return False
    db.delete(instruction)
    return True


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
            "correct_response": r.correct_response,
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
        if status == "pending":
            now = datetime.utcnow()
            base = base.filter(
                (models.WhatsAppSuggestion.visible_after.is_(None))
                | (models.WhatsAppSuggestion.visible_after <= now)
            )
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


def dismiss_all_pending(db: Session) -> int:
    now = datetime.utcnow()
    updated = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .update(
            {"status": "dismissed", "resolved_at": now},
            synchronize_session=False,
        )
    )
    db.commit()
    return updated


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
