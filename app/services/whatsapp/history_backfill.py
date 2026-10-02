"""Optional WAHA → local DB history backfill for richer draft context."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.services.whatsapp import repository as repo
from app.services.whatsapp import settings as wa_settings
from app.services.whatsapp import waha_client

logger = logging.getLogger(__name__)


def _ts_to_datetime(value) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return None
    return None


def _chat_id_from_overview(chat: dict) -> str | None:
    for key in ("id", "chatId"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = chat.get("_chat")
    if isinstance(nested, dict):
        value = nested.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def backfill_recent_history(
    db: Session,
    *,
    chat_limit: int | None = None,
    messages_per_chat: int | None = None,
) -> dict[str, int]:
    """Pull recent WAHA chats/messages into jarvis.db (idempotent by wa_message_id).

    Does not create Inbox chips — context / tone history only. Rows are marked
    classified immediately so the worker will not re-process the flood.
    """
    chat_limit = chat_limit or wa_settings.WHATSAPP_HISTORY_BACKFILL_CHAT_LIMIT
    messages_per_chat = (
        messages_per_chat or wa_settings.WHATSAPP_HISTORY_BACKFILL_MESSAGES_PER_CHAT
    )
    if not waha_client.is_configured():
        return {"chats": 0, "inserted": 0, "skipped": 0}

    chats = waha_client.list_chats(limit=chat_limit)
    inserted = 0
    skipped = 0
    now = datetime.utcnow()

    for chat in chats:
        chat_id = _chat_id_from_overview(chat)
        if not chat_id:
            continue
        lower = chat_id.lower()
        if lower.endswith("@broadcast") or lower.endswith("@newsletter"):
            continue
        is_group = lower.endswith("@g.us")
        wa_id = waha_client.normalize_contact_id(chat_id)
        name = chat.get("name") or chat.get("pushName")
        contact = repo.upsert_contact(
            db,
            wa_id=wa_id,
            profile_name=name if isinstance(name, str) else None,
            is_group=is_group,
        )
        try:
            messages = waha_client.fetch_recent_messages(wa_id, limit=messages_per_chat)
        except Exception:
            logger.exception("[WHATSAPP] Backfill fetch failed for %s", wa_id)
            continue

        for item in messages:
            body = (item.get("body") or "").strip()
            if not body:
                skipped += 1
                continue
            wa_message_id = item.get("wa_message_id") or None
            if isinstance(wa_message_id, str) and not wa_message_id.strip():
                wa_message_id = None
            if wa_message_id and repo.message_exists(db, wa_message_id):
                skipped += 1
                continue
            direction = item.get("direction") or "inbound"
            if direction not in ("inbound", "outbound"):
                direction = "inbound"
            message = repo.insert_message(
                db,
                contact=contact,
                direction=direction,
                msg_type="text",
                body=body,
                timestamp=_ts_to_datetime(item.get("timestamp")) or now,
                wa_message_id=wa_message_id,
                media_id=None,
                raw_payload={"source": "waha_backfill"},
                is_group=is_group,
                is_forwarded=False,
            )
            message.classified_at = now
            if direction == "inbound":
                message.category = "excluded"
                message.is_important = False
            inserted += 1
        db.flush()

    db.commit()
    logger.info(
        "[WHATSAPP] History backfill done chats=%s inserted=%s skipped=%s",
        len(chats),
        inserted,
        skipped,
    )
    return {"chats": len(chats), "inserted": inserted, "skipped": skipped}
