"""Inbox helpers — refresh actionable suggestions from recent inbound messages."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

from app.services.whatsapp import meeting_scope

logger = logging.getLogger(__name__)


def inbox_status(db: Session) -> dict:
    pending_count = (
        db.query(models.WhatsAppSuggestion.id)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .count()
    )
    last_inbound = (
        db.query(models.WhatsAppMessage)
        .filter(models.WhatsAppMessage.direction == "inbound")
        .order_by(models.WhatsAppMessage.timestamp.desc())
        .first()
    )
    return {
        "pending_count": pending_count,
        "last_inbound_at": last_inbound.timestamp if last_inbound else None,
        "last_inbound_preview": (
            (last_inbound.body or "").strip()[:120] if last_inbound and last_inbound.body else None
        ),
        "last_inbound_contact_id": last_inbound.contact_id if last_inbound else None,
    }


def refresh_pending_suggestions(db: Session, *, lookback_hours: int = 168) -> dict:
    """Recover actionable cards that were marked done without a sent reply.

    Intentionally does **not** reopen ``dismissed`` suggestions — Dismiss must stick.
    Also re-classifies important inbound messages that never received a suggestion.
    """
    from app.services import service_manager

    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    reopened = 0
    reclassified = 0

    latest_rows = (
        db.query(
            models.WhatsAppMessage.contact_id,
            func.max(models.WhatsAppMessage.id).label("message_id"),
        )
        .filter(
            models.WhatsAppMessage.direction == "inbound",
            models.WhatsAppMessage.timestamp >= cutoff,
        )
        .group_by(models.WhatsAppMessage.contact_id)
        .all()
    )

    for _contact_id, message_id in latest_rows:
        message = db.get(models.WhatsAppMessage, message_id)
        if message is None:
            continue

        suggestion = (
            db.query(models.WhatsAppSuggestion)
            .filter(models.WhatsAppSuggestion.message_id == message.id)
            .order_by(models.WhatsAppSuggestion.id.desc())
            .first()
        )

        if suggestion is not None:
            if suggestion.status == "pending":
                continue
            # User explicitly dismissed — keep it gone from the inbox.
            if suggestion.status == "dismissed":
                continue
            if suggestion.sent_message_id is not None:
                continue
            # Only reopen "done" / other non-pending states that never sent a reply.
            suggestion.status = "pending"
            suggestion.resolved_at = None
            reopened += 1
            logger.info(
                "[WHATSAPP] Reopened suggestion %s for message %s",
                suggestion.id,
                message.id,
            )
            continue

        # Reclassify: important messages with no chip, OR call/meeting asks that were
        # previously dropped (e.g. group filter without @mention → category=group).
        should_reclassify = message.classified_at is not None and (
            bool(message.is_important)
            or meeting_scope.looks_like_call_or_meeting_request(message.body)
        )
        if should_reclassify:
            message.classified_at = None
            message.category = None
            message.is_important = None
            db.flush()
            service_manager.whatsapp.classify_message_now(db, message)
            reclassified += 1
            logger.info("[WHATSAPP] Reclassified message %s", message.id)
            # If classify still left no chip, force a recovery suggestion so WAHA
            # traffic is visible in Inbox immediately.
            db.refresh(message)
            from app.services.whatsapp import repository as wa_repo

            if (
                message.is_important
                and not wa_repo.suggestion_exists_for_message(db, message.id)
                and (
                    not meeting_scope.MEETINGS_REMINDERS_ONLY
                    or meeting_scope.is_meeting_or_reminder(category=message.category)
                )
            ):
                chip = (
                    "Meeting requested — schedule?"
                    if message.category == "meeting"
                    else "Message needs a reply"
                )
                wa_repo.create_suggestion(
                    db,
                    contact_id=message.contact_id,
                    message_id=message.id,
                    kind="meeting" if message.category == "meeting" else "nudge",
                    category=message.category or "other",
                    priority=message.priority or "normal",
                    lane="work",
                    draft_text=None,
                    details={"chip_label": chip, "recovery_chip": True},
                )
                reopened += 1
                logger.warning(
                    "[WHATSAPP] Inserted recovery chip for message %s after reclassify",
                    message.id,
                )

    db.commit()
    status = inbox_status(db)
    return {
        "ok": True,
        "reopened": reopened,
        "reclassified": reclassified,
        **status,
    }
