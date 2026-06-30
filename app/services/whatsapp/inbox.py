"""Inbox helpers — refresh actionable suggestions from recent inbound messages."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

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
    """Re-open recent suggestions that were dismissed or marked done without a sent reply.

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
            if suggestion.sent_message_id is not None:
                continue
            suggestion.status = "pending"
            suggestion.resolved_at = None
            reopened += 1
            logger.info(
                "[WHATSAPP] Reopened suggestion %s for message %s",
                suggestion.id,
                message.id,
            )
            continue

        if message.is_important and message.classified_at is not None:
            message.classified_at = None
            message.category = None
            message.is_important = None
            db.flush()
            service_manager.whatsapp.classify_message_now(db, message)
            reclassified += 1
            logger.info("[WHATSAPP] Reclassified message %s", message.id)

    db.commit()
    status = inbox_status(db)
    return {
        "ok": True,
        "reopened": reopened,
        "reclassified": reclassified,
        **status,
    }
