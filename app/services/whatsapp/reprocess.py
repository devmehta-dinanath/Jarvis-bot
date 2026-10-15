"""Re-run WhatsApp reconciliation and classification on existing conversations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.services.whatsapp import product_gates
from app.services.whatsapp import repository as repo
from app.services.whatsapp import settings as wa_settings

logger = logging.getLogger(__name__)


@dataclass
class ReprocessAction:
    action: str
    reason: str
    commitment_id: int | None = None
    suggestion_id: int | None = None
    message_id: int | None = None


@dataclass
class ReprocessContactResult:
    contact_id: int
    contact_name: str
    messages_in_window: int = 0
    actions: list[ReprocessAction] = field(default_factory=list)
    manual_review: list[str] = field(default_factory=list)
    outbound_reanalyzed: int = 0
    inbound_reclassified: int = 0


@dataclass
class ReprocessSummary:
    dry_run: bool
    contacts: list[ReprocessContactResult] = field(default_factory=list)
    commitments_invalidated: int = 0
    deadlines_cleared: int = 0
    suggestions_dismissed: int = 0
    reminders_relinked: int = 0
    outbound_reanalyzed: int = 0
    inbound_reclassified: int = 0
    client_commitments_invalidated: int = 0
    reply_drafts_cleared: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "commitments_invalidated": self.commitments_invalidated,
            "deadlines_cleared": self.deadlines_cleared,
            "suggestions_dismissed": self.suggestions_dismissed,
            "reminders_relinked": self.reminders_relinked,
            "outbound_reanalyzed": self.outbound_reanalyzed,
            "inbound_reclassified": self.inbound_reclassified,
            "client_commitments_invalidated": self.client_commitments_invalidated,
            "reply_drafts_cleared": self.reply_drafts_cleared,
            "contacts": [
                {
                    "contact_id": c.contact_id,
                    "contact_name": c.contact_name,
                    "messages_in_window": c.messages_in_window,
                    "outbound_reanalyzed": c.outbound_reanalyzed,
                    "inbound_reclassified": c.inbound_reclassified,
                    "manual_review": c.manual_review,
                    "actions": [
                        {
                            "action": a.action,
                            "reason": a.reason,
                            "commitment_id": a.commitment_id,
                            "suggestion_id": a.suggestion_id,
                            "message_id": a.message_id,
                        }
                        for a in c.actions
                    ],
                }
                for c in self.contacts
            ],
        }


def resolve_contact_ids(
    db: Session,
    *,
    contact_ids: list[int] | None,
    wa_ids: list[str] | None,
) -> list[models.WhatsAppContact]:
    from app.services.whatsapp.account_scope import contact_visible_for_connected_account

    contacts: list[models.WhatsAppContact] = []
    seen: set[int] = set()
    for cid in contact_ids or []:
        if cid in seen:
            continue
        row = db.get(models.WhatsAppContact, cid)
        if row is None or row.is_excluded:
            continue
        if not contact_visible_for_connected_account(db, row.id):
            continue
        seen.add(row.id)
        contacts.append(row)
    for wa in wa_ids or []:
        row = repo.get_contact_by_wa_id(db, wa.strip())
        if row is None or row.is_excluded or row.id in seen:
            continue
        if not contact_visible_for_connected_account(db, row.id):
            continue
        seen.add(row.id)
        contacts.append(row)
    if contacts or contact_ids or wa_ids:
        return contacts
    # No ids were given, so refresh every chat stored for this account.
    rows = (
        db.query(models.WhatsAppContact)
        .filter(models.WhatsAppContact.is_excluded.is_(False))
        .order_by(models.WhatsAppContact.id.asc())
        .all()
    )
    return [row for row in rows if contact_visible_for_connected_account(db, row.id)]


def _recent_messages(
    db: Session, contact_id: int, *, limit: int
) -> list[models.WhatsAppMessage]:
    rows, _total = repo.list_messages(
        db, contact_id=contact_id, limit=limit, offset=0, newest=True
    )
    return rows


def _invalidate_owner_commitment(
    db: Session,
    commitment: models.WhatsAppCommitment,
    *,
    reason: str,
    dry_run: bool,
    result: ReprocessContactResult,
) -> None:
    result.actions.append(
        ReprocessAction(
            action="invalidate_owner_commitment",
            reason=reason,
            commitment_id=commitment.id,
            message_id=commitment.message_id,
        )
    )
    if dry_run:
        return
    repo.fulfill_commitment(db, commitment.id)
    repo.dismiss_commitment_suggestions(db, commitment.id)
    logger.info(
        "[WHATSAPP] Reprocess invalidated owner commitment %s (contact=%s reason=%s)",
        commitment.id,
        commitment.contact_id,
        reason,
    )


def _invalidate_client_commitment(
    db: Session,
    commitment: models.WhatsAppCommitment,
    *,
    reason: str,
    dry_run: bool,
    result: ReprocessContactResult,
) -> None:
    result.actions.append(
        ReprocessAction(
            action="invalidate_client_commitment",
            reason=reason,
            commitment_id=commitment.id,
            message_id=commitment.message_id,
        )
    )
    if dry_run:
        return
    repo.fulfill_commitment(db, commitment.id)
    repo.dismiss_commitment_suggestions(db, commitment.id)
    logger.info(
        "[WHATSAPP] Reprocess invalidated client commitment %s (contact=%s reason=%s)",
        commitment.id,
        commitment.contact_id,
        reason,
    )


def _clear_commitment_deadline(
    db: Session,
    commitment: models.WhatsAppCommitment,
    *,
    reason: str,
    dry_run: bool,
    result: ReprocessContactResult,
) -> None:
    result.actions.append(
        ReprocessAction(
            action="clear_unsupported_deadline",
            reason=reason,
            commitment_id=commitment.id,
            message_id=commitment.message_id,
        )
    )
    if dry_run:
        return
    commitment.deadline_at = None
    db.flush()
    if commitment.direction == "owner":
        _refresh_commitment_reminder_chip(db, commitment)
    logger.info(
        "[WHATSAPP] Reprocess cleared deadline on commitment %s (contact=%s)",
        commitment.id,
        commitment.contact_id,
    )


def _refresh_commitment_reminder_chip(
    db: Session, commitment: models.WhatsAppCommitment
) -> None:
    contact = commitment.contact or db.get(models.WhatsAppContact, commitment.contact_id)
    display = (
        ((contact.profile_name or "").strip() or contact.wa_id) if contact else "contact"
    )
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.status == "pending",
            models.WhatsAppSuggestion.kind == "commitment_reminder",
            models.WhatsAppSuggestion.contact_id == commitment.contact_id,
        )
        .all()
    )
    for row in pending:
        try:
            details = json.loads(row.details or "{}")
        except json.JSONDecodeError:
            details = {}
        if not isinstance(details, dict):
            continue
        if details.get("commitment_id") != commitment.id:
            continue
        details["deadline_at"] = None
        details["chip_label"] = product_gates.format_owner_reminder_chip(
            label=commitment.label,
            contact_name=display,
            deadline_at=None,
        )
        row.details = json.dumps(details)
        row.draft_text = None
    db.flush()


def _dismiss_suggestion(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    *,
    reason: str,
    dry_run: bool,
    result: ReprocessContactResult,
) -> None:
    result.actions.append(
        ReprocessAction(
            action="dismiss_suggestion",
            reason=reason,
            suggestion_id=suggestion.id,
            message_id=suggestion.message_id,
        )
    )
    if dry_run:
        return
    suggestion.status = "dismissed"
    suggestion.resolved_at = datetime.utcnow()
    db.flush()
    logger.info(
        "[WHATSAPP] Reprocess dismissed suggestion %s (reason=%s)",
        suggestion.id,
        reason,
    )


def _relink_reminder_to_source(
    db: Session,
    suggestion: models.WhatsAppSuggestion,
    source_id: int,
    *,
    dry_run: bool,
    result: ReprocessContactResult,
) -> None:
    if suggestion.message_id == source_id:
        return
    result.actions.append(
        ReprocessAction(
            action="relink_reminder_source",
            reason="reminder_must_quote_owner_promise_message",
            suggestion_id=suggestion.id,
            message_id=source_id,
        )
    )
    if not dry_run:
        suggestion.message_id = source_id
        db.flush()


def reconcile_contact(
    db: Session,
    contact: models.WhatsAppContact,
    *,
    dry_run: bool,
    message_limit: int,
) -> ReprocessContactResult:
    """Deterministic cleanup using product gates — no LLM."""
    display = (contact.profile_name or "").strip() or contact.wa_id
    result = ReprocessContactResult(contact_id=contact.id, contact_name=display)
    window = _recent_messages(db, contact.id, limit=message_limit)
    result.messages_in_window = len(window)
    window_ids = {m.id for m in window}

    for commitment in repo.open_commitments_for_contact(
        db, contact.id, direction="owner"
    ):
        source = (
            db.get(models.WhatsAppMessage, commitment.message_id)
            if commitment.message_id is not None
            else None
        )
        source_body = (source.body or "") if source is not None else ""
        if source is None:
            result.manual_review.append(
                f"owner commitment {commitment.id} has no source message"
            )
            continue
        if product_gates.speaker_for_direction(source.direction) != "owner":
            _invalidate_owner_commitment(
                db,
                commitment,
                reason="source_is_not_owner_turn",
                dry_run=dry_run,
                result=result,
            )
            continue
        if not product_gates.owner_reminder_still_supported(source_body, commitment.label):
            _invalidate_owner_commitment(
                db,
                commitment,
                reason="source_not_a_supported_promise",
                dry_run=dry_run,
                result=result,
            )
            continue
        if commitment.deadline_at is not None and not product_gates.supported_commitment_deadline(
            source_body,
            commitment.deadline_at.isoformat(),
        ):
            _clear_commitment_deadline(
                db,
                commitment,
                reason="deadline_not_in_source_text",
                dry_run=dry_run,
                result=result,
            )

    for commitment in repo.open_commitments_for_contact(
        db, contact.id, direction="client"
    ):
        source = (
            db.get(models.WhatsAppMessage, commitment.message_id)
            if commitment.message_id is not None
            else None
        )
        body = (source.body or "") if source is not None else ""
        if source is None:
            result.manual_review.append(
                f"client commitment {commitment.id} has no source message"
            )
            continue
        if product_gates.speaker_for_direction(source.direction) != "client":
            _invalidate_client_commitment(
                db,
                commitment,
                reason="source_is_not_client_turn",
                dry_run=dry_run,
                result=result,
            )
            continue
        if not product_gates.client_states_own_promise(body):
            _invalidate_client_commitment(
                db,
                commitment,
                reason="client_message_not_a_promise",
                dry_run=dry_run,
                result=result,
            )
            continue
        if commitment.deadline_at is not None and not product_gates.supported_commitment_deadline(
            body,
            commitment.deadline_at.isoformat(),
        ):
            _clear_commitment_deadline(
                db,
                commitment,
                reason="deadline_not_in_client_text",
                dry_run=dry_run,
                result=result,
            )

    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact.id,
            models.WhatsAppSuggestion.status == "pending",
        )
        .all()
    )
    for suggestion in pending:
        message = (
            db.get(models.WhatsAppMessage, suggestion.message_id)
            if suggestion.message_id is not None
            else None
        )
        body = (message.body or "") if message is not None else ""

        if suggestion.kind == "commitment_reminder":
            commitment_id = None
            try:
                details = json.loads(suggestion.details or "{}")
                if isinstance(details, dict):
                    commitment_id = details.get("commitment_id")
            except json.JSONDecodeError:
                pass
            commitment = (
                db.get(models.WhatsAppCommitment, commitment_id)
                if commitment_id is not None
                else None
            )
            if commitment is not None and commitment.message_id is not None:
                _relink_reminder_to_source(
                    db,
                    suggestion,
                    commitment.message_id,
                    dry_run=dry_run,
                    result=result,
                )
            continue

        slot = repo._card_slot(suggestion.kind, suggestion.category)
        if slot != "reply":
            continue
        if message is None or message.direction != "inbound":
            continue
        if not product_gates.inbound_supports_reply_draft(body):
            _dismiss_suggestion(
                db,
                suggestion,
                reason="inbound_not_reply_worthy",
                dry_run=dry_run,
                result=result,
            )
            continue
        if suggestion.draft_text and message.id not in window_ids:
            result.manual_review.append(
                f"reply suggestion {suggestion.id} kept — source outside window"
            )

    return result


def reanalyze_outbound_commitments(
    db: Session,
    contact: models.WhatsAppContact,
    *,
    dry_run: bool,
    message_limit: int,
    service,
    result: ReprocessContactResult,
) -> None:
    """Reset commitment-processed markers and replay the owner outbound queue."""
    rows = (
        db.query(models.WhatsAppMessage)
        .filter(
            models.WhatsAppMessage.contact_id == contact.id,
            models.WhatsAppMessage.direction == "outbound",
            models.WhatsAppMessage.msg_type.in_(("text", "document")),
        )
        .order_by(models.WhatsAppMessage.id.desc())
        .limit(message_limit)
        .all()
    )
    rows.reverse()
    for message in rows:
        result.actions.append(
            ReprocessAction(
                action="reanalyze_outbound",
                reason="replay_commitment_pipeline",
                message_id=message.id,
            )
        )
        if dry_run:
            result.outbound_reanalyzed += 1
            continue
        message.classified_at = None
        db.commit()
        service._analyze_commitment(db, message)
        result.outbound_reanalyzed += 1


def reclassify_inbound_messages(
    db: Session,
    contact: models.WhatsAppContact,
    *,
    dry_run: bool,
    message_limit: int,
    max_messages: int,
    service,
    result: ReprocessContactResult,
) -> None:
    """Replay inbound classification for reply-worthy messages (chronological)."""
    rows = (
        db.query(models.WhatsAppMessage)
        .filter(
            models.WhatsAppMessage.contact_id == contact.id,
            models.WhatsAppMessage.direction == "inbound",
            models.WhatsAppMessage.msg_type == "text",
        )
        .order_by(models.WhatsAppMessage.id.desc())
        .limit(message_limit)
        .all()
    )
    rows.reverse()
    processed = 0
    for message in rows:
        body = (message.body or "").strip()
        if not body or not product_gates.inbound_supports_reply_draft(body):
            continue
        if processed >= max_messages:
            result.manual_review.append(
                f"inbound reclassify cap ({max_messages}) reached for contact {contact.id}"
            )
            break
        result.actions.append(
            ReprocessAction(
                action="reclassify_inbound",
                reason="replay_classification_pipeline",
                message_id=message.id,
            )
        )
        if dry_run:
            result.inbound_reclassified += 1
            processed += 1
            continue
        message.classified_at = None
        message.category = None
        message.is_important = None
        db.commit()
        service.classify_message_now(db, message)
        result.inbound_reclassified += 1
        processed += 1


def reprocess_contacts(
    db: Session,
    *,
    contact_ids: list[int] | None = None,
    wa_ids: list[str] | None = None,
    dry_run: bool = True,
    message_limit: int | None = None,
    reanalyze_outbound: bool = True,
    reclassify_inbound: bool = False,
    max_inbound_reclassify: int = 3,
) -> ReprocessSummary:
    from app.services import service_manager

    limit = message_limit or wa_settings.WHATSAPP_CHAT_CONTEXT_MESSAGES
    limit = max(1, min(int(limit), 200))
    max_inbound_reclassify = max(0, min(int(max_inbound_reclassify), 20))

    contacts = resolve_contact_ids(db, contact_ids=contact_ids, wa_ids=wa_ids)
    summary = ReprocessSummary(dry_run=dry_run)
    service = service_manager.whatsapp

    for contact in contacts:
        result = reconcile_contact(
            db, contact, dry_run=dry_run, message_limit=limit
        )
        if reanalyze_outbound:
            reanalyze_outbound_commitments(
                db,
                contact,
                dry_run=dry_run,
                message_limit=limit,
                service=service,
                result=result,
            )
        if reclassify_inbound and max_inbound_reclassify > 0:
            reclassify_inbound_messages(
                db,
                contact,
                dry_run=dry_run,
                message_limit=limit,
                max_messages=max_inbound_reclassify,
                service=service,
                result=result,
            )
        summary.contacts.append(result)
        for action in result.actions:
            if action.action == "invalidate_owner_commitment":
                summary.commitments_invalidated += 1
            elif action.action == "invalidate_client_commitment":
                summary.client_commitments_invalidated += 1
            elif action.action == "clear_unsupported_deadline":
                summary.deadlines_cleared += 1
            elif action.action == "dismiss_suggestion":
                if action.reason == "inbound_not_reply_worthy":
                    summary.reply_drafts_cleared += 1
                summary.suggestions_dismissed += 1
            elif action.action == "relink_reminder_source":
                summary.reminders_relinked += 1
        summary.outbound_reanalyzed += result.outbound_reanalyzed
        summary.inbound_reclassified += result.inbound_reclassified
        if not dry_run:
            db.commit()

    if not dry_run:
        repo.dismiss_suggestions_already_answered(db)
        db.commit()
    else:
        db.rollback()

    logger.info(
        "[WHATSAPP] Reprocess complete dry_run=%s contacts=%s invalidated=%s cleared_deadlines=%s",
        dry_run,
        len(contacts),
        summary.commitments_invalidated + summary.client_commitments_invalidated,
        summary.deadlines_cleared,
    )
    return summary
