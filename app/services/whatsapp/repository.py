import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import models
from app.config import WHATSAPP_CUSTOMER_WINDOW_HOURS

logger = logging.getLogger(__name__)

# Sentinel distinguishing "field not provided" from "field explicitly set to None" in
# partial-update functions (e.g. update_forwarding_rule) whose columns are legitimately
# nullable — a plain `None` default can't tell those two cases apart.
_UNSET = object()


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


def work_contacts_awaiting_reply(
    db: Session,
    *,
    flag_hours: float,
    active_within_days: int = 30,
) -> list[models.WhatsAppContact]:
    """Client/work-lane counterpart to personal_contacts_awaiting_reply. Unlike the
    'follow_up' category (which only fires once the other side re-pings you), this
    catches a client who sent one message and simply never got a reply — proactively,
    on elapsed time alone."""
    cutoff = datetime.utcnow() - timedelta(hours=flag_hours)
    active_since = datetime.utcnow() - timedelta(days=active_within_days)
    contacts = (
        db.query(models.WhatsAppContact)
        .filter(
            or_(
                models.WhatsAppContact.contact_type.is_(None),
                models.WhatsAppContact.contact_type != "personal",
            ),
            models.WhatsAppContact.is_group.is_(False),
            models.WhatsAppContact.is_excluded.is_(False),
            models.WhatsAppContact.last_inbound_at.isnot(None),
            models.WhatsAppContact.last_inbound_at >= active_since,
            models.WhatsAppContact.last_inbound_at <= cutoff,
            (
                models.WhatsAppContact.last_replied_at.is_(None)
                | (models.WhatsAppContact.last_replied_at <= models.WhatsAppContact.last_inbound_at)
            ),
        )
        .all()
    )
    return [c for c in contacts if not pending_followup_nudge_exists(db, c.id)]


def pending_followup_nudge_exists(db: Session, contact_id: int) -> bool:
    """Same dedup rule as pending_life_nudge_exists: one awaiting-reply nudge per
    contact per silence period — a new one becomes possible once they message again."""
    contact = db.get(models.WhatsAppContact, contact_id)
    query = db.query(models.WhatsAppSuggestion.id).filter(
        models.WhatsAppSuggestion.contact_id == contact_id,
        models.WhatsAppSuggestion.kind == "followup_nudge",
    )
    if contact is not None and contact.last_inbound_at is not None:
        query = query.filter(
            models.WhatsAppSuggestion.created_at >= contact.last_inbound_at
        )
    return query.first() is not None


def pending_commitment_for_contact(db: Session, contact_id: int) -> models.WhatsAppCommitment | None:
    """The one open (unfulfilled) commitment for this contact, if any — only one is
    tracked at a time per contact."""
    return (
        db.query(models.WhatsAppCommitment)
        .filter(
            models.WhatsAppCommitment.contact_id == contact_id,
            models.WhatsAppCommitment.fulfilled_at.is_(None),
        )
        .order_by(models.WhatsAppCommitment.created_at.desc())
        .first()
    )


def create_commitment(
    db: Session,
    *,
    contact_id: int,
    message_id: int | None,
    commitment_type: str,
    label: str,
) -> models.WhatsAppCommitment:
    commitment = models.WhatsAppCommitment(
        contact_id=contact_id,
        message_id=message_id,
        commitment_type=commitment_type,
        label=label,
    )
    db.add(commitment)
    db.flush()
    return commitment


def fulfill_commitment(db: Session, commitment_id: int) -> None:
    commitment = db.get(models.WhatsAppCommitment, commitment_id)
    if commitment is not None:
        commitment.fulfilled_at = datetime.utcnow()


def commitments_awaiting_reminder(
    db: Session,
    *,
    flag_hours: float,
    reminder_interval_hours: float,
) -> list[models.WhatsAppCommitment]:
    """Unfulfilled commitments old enough to flag, that haven't been reminded about
    recently. last_reminded_at (not the created suggestion) is the dedup source of
    truth — a reminder chip can get dismissed by an unrelated later reply, but the
    underlying promise is still open and due to be re-flagged."""
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=flag_hours)
    reminder_cutoff = now - timedelta(hours=reminder_interval_hours)
    return (
        db.query(models.WhatsAppCommitment)
        .join(models.WhatsAppContact, models.WhatsAppCommitment.contact_id == models.WhatsAppContact.id)
        .filter(
            models.WhatsAppCommitment.fulfilled_at.is_(None),
            models.WhatsAppCommitment.created_at <= cutoff,
            (
                models.WhatsAppCommitment.last_reminded_at.is_(None)
                | (models.WhatsAppCommitment.last_reminded_at <= reminder_cutoff)
            ),
            models.WhatsAppContact.is_excluded.is_(False),
        )
        .all()
    )


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


def next_uncommitment_checked_outbound(db: Session) -> models.WhatsAppMessage | None:
    """Oldest outbound text message not yet checked for a new/fulfilled commitment.
    Reuses classified_at as the 'processed' marker — safe because classification only
    ever queries direction == 'inbound'."""
    return (
        db.query(models.WhatsAppMessage)
        .filter(
            models.WhatsAppMessage.direction == "outbound",
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


def pending_clarification_exists(db: Session, contact_id: int) -> bool:
    """Rule 13 — max one clarifying question per conversation at a time."""
    return (
        db.query(models.WhatsAppSuggestion.id)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact_id,
            models.WhatsAppSuggestion.kind == "clarify",
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
    """"wrong" = genuine category mistakes; "edited" = the AI's wording was corrected
    before sending, category was fine. Both carry a usable correct_response, but only
    "wrong" belongs in classifier._corrections_block — see feedback_type filtering there."""
    rows = (
        db.query(models.WhatsAppFeedback)
        .filter(models.WhatsAppFeedback.feedback_type.in_(["wrong", "edited"]))
        .order_by(models.WhatsAppFeedback.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "feedback_type": r.feedback_type,
            "original_category": r.original_category,
            "original_confidence": r.original_confidence,
            "message_snippet": r.message_snippet,
            "correct_response": r.correct_response,
        }
        for r in rows
    ]


def recent_outbound_examples(
    db: Session,
    *,
    personal: bool,
    limit: int = 8,
) -> list[str]:
    """Real messages the user actually sent — the ground truth for "how do they write."
    Scoped to personal vs work contacts, matching the tone split already used for
    drafting (see classifier._REPLY_SYSTEM_PERSONAL / service.py's is_personal checks)."""
    query = (
        db.query(models.WhatsAppMessage)
        .join(models.WhatsAppContact, models.WhatsAppMessage.contact_id == models.WhatsAppContact.id)
        .filter(models.WhatsAppMessage.direction == "outbound")
        .filter(models.WhatsAppMessage.body.isnot(None))
    )
    if personal:
        query = query.filter(models.WhatsAppContact.contact_type == "personal")
    else:
        query = query.filter(
            (models.WhatsAppContact.contact_type.is_(None))
            | (models.WhatsAppContact.contact_type != "personal")
        )
    rows = (
        query.order_by(models.WhatsAppMessage.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [r.body.strip() for r in rows if r.body and r.body.strip()]


def get_or_create_learning_state(db: Session) -> models.WhatsAppLearningState:
    """The single global anchor for the 7-day silent observation window (see
    WhatsAppLearningState). Anchored retroactively to the earliest outbound message
    already on record, so an account with existing history doesn't lose suggestions
    for a week the moment this feature ships — a genuinely fresh install (no outbound
    history at all) anchors to now, giving it the real 7-day window."""
    state = db.query(models.WhatsAppLearningState).first()
    if state is not None:
        return state

    earliest_outbound = (
        db.query(func.min(models.WhatsAppMessage.timestamp))
        .filter(models.WhatsAppMessage.direction == "outbound")
        .scalar()
    )
    state = models.WhatsAppLearningState(
        observation_started_at=earliest_outbound or datetime.utcnow()
    )
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


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
        # Rule 9's timing (instant/30min/4-6h) gates when the DRAFTED REPLY is ready to
        # show, not whether the message/card itself is visible — the card always lists
        # the moment it's classified. See routes._suggestion_response, which hides
        # draft_text until visible_after has passed.
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


# ── Rule 14 — Forward to team ──────────────────────────────────────────────


def list_forwarding_rules(db: Session) -> list[models.TeamForwardingRule]:
    return (
        db.query(models.TeamForwardingRule)
        .order_by(models.TeamForwardingRule.created_at.asc())
        .all()
    )


def create_forwarding_rule(
    db: Session,
    *,
    label: str,
    trigger_category: str,
    trigger_payment_status: str | None = None,
    team_member_name: str | None = None,
    team_member_wa_id: str | None = None,
) -> models.TeamForwardingRule:
    rule = models.TeamForwardingRule(
        label=label.strip(),
        trigger_category=trigger_category.strip(),
        trigger_payment_status=(trigger_payment_status or "").strip() or None,
        team_member_name=(team_member_name or "").strip() or None,
        team_member_wa_id=(team_member_wa_id or "").strip() or None,
    )
    db.add(rule)
    db.flush()
    return rule


def update_forwarding_rule(
    db: Session,
    rule_id: int,
    *,
    label: str | None = None,
    trigger_category: str | None = None,
    trigger_payment_status: str | None | object = _UNSET,
    team_member_name: str | None | object = _UNSET,
    team_member_wa_id: str | None | object = _UNSET,
    is_active: bool | None = None,
) -> models.TeamForwardingRule | None:
    rule = db.get(models.TeamForwardingRule, rule_id)
    if rule is None:
        return None
    if label is not None:
        rule.label = label.strip()
    if trigger_category is not None:
        rule.trigger_category = trigger_category.strip()
    if trigger_payment_status is not _UNSET:
        rule.trigger_payment_status = (trigger_payment_status or "").strip() or None
    if team_member_name is not _UNSET:
        rule.team_member_name = (team_member_name or "").strip() or None
    if team_member_wa_id is not _UNSET:
        rule.team_member_wa_id = (team_member_wa_id or "").strip() or None
    if is_active is not None:
        rule.is_active = is_active
    db.flush()
    return rule


def delete_forwarding_rule(db: Session, rule_id: int) -> bool:
    rule = db.get(models.TeamForwardingRule, rule_id)
    if rule is None:
        return False
    db.delete(rule)
    return True


def find_forwarding_rule(
    db: Session,
    *,
    category: str | None,
    payment_status: str | None = None,
) -> models.TeamForwardingRule | None:
    """The matching active, fully-configured rule for a suggestion, if any — used to
    decide whether the UI shows a one-tap Forward button (see routes._suggestion_response)."""
    if not category:
        return None
    rows = (
        db.query(models.TeamForwardingRule)
        .filter(
            models.TeamForwardingRule.is_active.is_(True),
            models.TeamForwardingRule.trigger_category == category,
        )
        .all()
    )
    for rule in rows:
        if not rule.team_member_wa_id:
            continue
        if rule.trigger_payment_status and rule.trigger_payment_status != payment_status:
            continue
        return rule
    return None
