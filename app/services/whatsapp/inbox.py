"""Inbox helpers — refresh actionable suggestions from recent inbound messages."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

from app.services.whatsapp import meeting_scope

logger = logging.getLogger(__name__)

# Cloudflare / proxy origin timeouts are often ~30–100s. Keep this endpoint well under.
_REFRESH_TIME_BUDGET_SEC = 8.0
_REFRESH_MAX_RECLASSIFY = 2


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


def _insert_recovery_chip(db: Session, message: models.WhatsAppMessage) -> bool:
    """Create a meeting/nudge chip without re-running the LLM."""
    from app.services.whatsapp import repository as wa_repo

    if not message.is_important:
        return False
    if wa_repo.suggestion_exists_for_message(db, message.id):
        return False
    if meeting_scope.MEETINGS_REMINDERS_ONLY and not meeting_scope.is_meeting_or_reminder(
        category=message.category
    ):
        return False

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
    logger.warning(
        "[WHATSAPP] Inserted recovery chip for message %s (category=%s)",
        message.id,
        message.category,
    )
    return True


def refresh_pending_suggestions(db: Session, *, lookback_hours: int = 168) -> dict:
    """Recover actionable cards that were marked done without a sent reply.

    Intentionally does **not** reopen ``dismissed`` suggestions — Dismiss must stick.
    Also re-classifies a small number of inbound messages that never received a suggestion.
    Bound by time/count so Cloudflare proxies do not 524 the Inbox load.
    """
    from app.services import service_manager

    started = time.monotonic()
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
        if time.monotonic() - started > _REFRESH_TIME_BUDGET_SEC:
            logger.info(
                "[WHATSAPP] refresh-pending time budget hit after %.1fs "
                "(reopened=%s reclassified=%s)",
                time.monotonic() - started,
                reopened,
                reclassified,
            )
            break

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

        # Already classified as meeting — insert chip without another OpenAI round-trip.
        if message.classified_at is not None and _insert_recovery_chip(db, message):
            reopened += 1
            continue

        call_ask = meeting_scope.looks_like_call_or_meeting_request(message.body)
        # In meetings-only mode, only spend LLM budget on clear call/meeting asks
        # (or already-meeting categories handled above). Full-inbox mode still
        # reclassifies important messages that somehow lost their chip.
        if meeting_scope.MEETINGS_REMINDERS_ONLY:
            should_reclassify = message.classified_at is not None and call_ask
        else:
            should_reclassify = message.classified_at is not None and (
                bool(message.is_important) or call_ask
            )

        if not should_reclassify:
            continue
        if reclassified >= _REFRESH_MAX_RECLASSIFY:
            logger.info(
                "[WHATSAPP] refresh-pending reclassify cap (%s) hit — remaining skipped",
                _REFRESH_MAX_RECLASSIFY,
            )
            continue

        message.classified_at = None
        message.category = None
        message.is_important = None
        db.flush()
        service_manager.whatsapp.classify_message_now(db, message)
        reclassified += 1
        logger.info("[WHATSAPP] Reclassified message %s", message.id)
        db.refresh(message)
        if _insert_recovery_chip(db, message):
            reopened += 1

    db.commit()
    status = inbox_status(db)
    return {
        "ok": True,
        "reopened": reopened,
        "reclassified": reclassified,
        **status,
    }
