import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.whatsapp import repository as repo

logger = logging.getLogger(__name__)


def _ts_to_datetime(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.utcnow()


def _extract_text(message: dict[str, Any], msg_type: str) -> str | None:
    if msg_type == "text":
        return (message.get("text") or {}).get("body")
    if msg_type in {"button"}:
        return (message.get("button") or {}).get("text")
    if msg_type == "interactive":
        interactive = message.get("interactive") or {}
        for key in ("button_reply", "list_reply"):
            part = interactive.get(key) or {}
            if part.get("title"):
                return part.get("title")
    caption = (message.get(msg_type) or {}).get("caption") if isinstance(
        message.get(msg_type), dict
    ) else None
    return caption


def _extract_media_id(message: dict[str, Any], msg_type: str) -> str | None:
    media = message.get(msg_type)
    if isinstance(media, dict):
        return media.get("id")
    return None


def _is_forwarded_message(message: dict[str, Any]) -> bool:
    """Return True when the Cloud API marks the message as a forwarded or viral forward.

    WhatsApp sets context.forwarded=true for any forwarded message, and
    context.frequently_forwarded=true for viral chain messages.  Both are treated
    identically — the classifier decides later whether the sender added personal text.
    """
    context = message.get("context") or {}
    return bool(context.get("forwarded") or context.get("frequently_forwarded"))


def _is_group_message(message: dict[str, Any], value: dict[str, Any]) -> bool:
    """Best-effort detection of a WhatsApp group message from the webhook payload.

    The Cloud API exposes group origin inconsistently across rollouts, so we check the
    common markers defensively.
    """
    if message.get("group_id") or message.get("group"):
        return True
    context = message.get("context") or {}
    if context.get("group_id") or context.get("group"):
        return True
    if str(message.get("recipient_type") or "").lower() == "group":
        return True
    metadata = value.get("metadata") or {}
    if metadata.get("group_id"):
        return True
    return False


def process_webhook_payload(db: Session, payload: dict[str, Any]) -> int:
    """Persist inbound messages and status updates from a Meta webhook payload.

    Returns the number of new inbound messages stored.
    """
    new_messages = 0
    stored_messages = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            count, messages = _process_change_value(db, value)
            new_messages += count
            stored_messages.extend(messages)
    for message in stored_messages:
        db.refresh(message)
        _classify_if_enabled(db, message)
    db.commit()
    return new_messages


def _process_change_value(db: Session, value: dict[str, Any]) -> tuple[int, list]:
    contacts_meta = {
        c.get("wa_id"): (c.get("profile") or {}).get("name")
        for c in value.get("contacts", []) or []
    }

    new_messages = 0
    stored_messages = []
    for message in value.get("messages", []) or []:
        wa_message_id = message.get("id")
        if wa_message_id and repo.message_exists(db, wa_message_id):
            continue

        wa_id = message.get("from")
        if not wa_id:
            continue
        profile_name = contacts_meta.get(wa_id)
        contact = repo.upsert_contact(db, wa_id=wa_id, profile_name=profile_name)

        msg_type = message.get("type", "text")
        body = _extract_text(message, msg_type)
        stored = repo.insert_message(
            db,
            contact=contact,
            direction="inbound",
            msg_type=msg_type,
            body=body,
            timestamp=_ts_to_datetime(message.get("timestamp")),
            wa_message_id=wa_message_id,
            media_id=_extract_media_id(message, msg_type),
            raw_payload=message,
            is_group=_is_group_message(message, value),
            is_forwarded=_is_forwarded_message(message),
        )
        new_messages += 1
        stored_messages.append(stored)

    for status in value.get("statuses", []) or []:
        wa_message_id = status.get("id")
        state = status.get("status")
        if wa_message_id and state:
            repo.update_message_status(db, wa_message_id, state)

    return new_messages, stored_messages


def _classify_if_enabled(db: Session, message) -> None:
    try:
        from app.services import service_manager
        from app.services.whatsapp.classifier import WhatsAppAIError

        if not service_manager.whatsapp.is_enabled:
            return

        if message.msg_type in ("audio", "voice"):
            service_manager.whatsapp.handle_voice_note_now(db, message)
            return

        if message.msg_type != "text" or not (message.body or "").strip():
            return

        service_manager.whatsapp.classify_message_now(db, message)
    except WhatsAppAIError as exc:
        logger.warning("[WHATSAPP] Immediate classification failed for %s: %s", message.id, exc)
    except Exception:
        logger.exception("[WHATSAPP] Immediate classification error for message %s", message.id)
