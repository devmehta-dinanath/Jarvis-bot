import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import WHATSAPP_PROVIDER
from app.services.whatsapp import repository as repo
from app.services.whatsapp.waha_client import normalize_contact_id

logger = logging.getLogger(__name__)


def _ts_to_datetime(value: Any) -> datetime:
    try:
        ts = int(value)
        # WAHA may send seconds or milliseconds
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
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
    """Return True when the Cloud API marks the message as a forwarded or viral forward."""
    context = message.get("context") or {}
    return bool(context.get("forwarded") or context.get("frequently_forwarded"))


def _is_group_message(message: dict[str, Any], value: dict[str, Any]) -> bool:
    """Best-effort detection of a WhatsApp group message from the webhook payload."""
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


def is_waha_payload(payload: dict[str, Any]) -> bool:
    if WHATSAPP_PROVIDER == "waha":
        return True
    if isinstance(payload.get("event"), str) and "payload" in payload:
        return True
    if isinstance(payload.get("entry"), list):
        return False
    return False


def process_webhook_payload(db: Session, payload: dict[str, Any]) -> int:
    """Persist inbound messages from Meta or WAHA webhook payloads."""
    if is_waha_payload(payload):
        return _process_waha_payload(db, payload)
    return _process_meta_payload(db, payload)


def _process_meta_payload(db: Session, payload: dict[str, Any]) -> int:
    """Persist inbound messages and status updates from a Meta webhook payload."""
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


def _process_waha_payload(db: Session, payload: dict[str, Any]) -> int:
    """Persist WAHA webhook events (message / message.any / message.ack)."""
    event = str(payload.get("event") or "")
    body = payload.get("payload")
    if not isinstance(body, dict):
        logger.info("[WAHA] Ignoring event without payload: %s", event)
        return 0

    if event in {"message", "message.any", "message.reaction", "message.edited"}:
        stored = _store_waha_message(db, payload, body, event=event)
        if stored is None:
            db.commit()
            return 0
        db.refresh(stored)
        if stored.direction == "inbound":
            _classify_if_enabled(db, stored)
        db.commit()
        return 1 if stored.direction == "inbound" else 0

    if event == "message.ack":
        _apply_waha_ack(db, body)
        db.commit()
        return 0

    if event == "session.status":
        logger.info(
            "[WAHA] session.status session=%s status=%s",
            payload.get("session"),
            body.get("status") or body.get("name"),
        )
        return 0

    logger.info("[WAHA] Ignoring unsupported event=%s", event)
    return 0


def _waha_msg_type(body: dict[str, Any]) -> str:
    if body.get("hasMedia"):
        media = body.get("media") or {}
        mime = str(media.get("mimetype") or "").lower()
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("audio/") or mime.startswith("ptt"):
            return "audio"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("application/") or media.get("filename"):
            return "document"
        return "media"
    if body.get("vCards"):
        return "contacts"
    return "text"


def _waha_is_group(chat_id: str | None) -> bool:
    return bool(chat_id and str(chat_id).endswith("@g.us"))


def _store_waha_message(
    db: Session,
    envelope: dict[str, Any],
    body: dict[str, Any],
    *,
    event: str,
):
    from_me = bool(body.get("fromMe"))
    # Avoid duplicating outbound messages we already persisted after sendText.
    if from_me and str(body.get("source") or "").lower() == "api":
        return None

    wa_message_id = body.get("id") or body.get("messageId")
    if wa_message_id and repo.message_exists(db, str(wa_message_id)):
        return None

    # Peer chat id for inbox contact
    if from_me:
        peer = body.get("to") or body.get("from")
    else:
        peer = body.get("from")
    if not peer:
        logger.warning("[WAHA] message missing from/to: event=%s", event)
        return None

    wa_id = normalize_contact_id(str(peer))
    notify_name = (
        (body.get("_data") or {}).get("notifyName")
        if isinstance(body.get("_data"), dict)
        else None
    )
    profile_name = body.get("pushName") or notify_name
    contact = repo.upsert_contact(db, wa_id=wa_id, profile_name=profile_name)

    msg_type = _waha_msg_type(body)
    text_body = body.get("body")
    if isinstance(text_body, str):
        text_value = text_body
    else:
        text_value = None
    if not text_value and msg_type != "text":
        media = body.get("media") or {}
        text_value = media.get("filename") or media.get("url")

    return repo.insert_message(
        db,
        contact=contact,
        direction="outbound" if from_me else "inbound",
        msg_type=msg_type,
        body=text_value,
        timestamp=_ts_to_datetime(body.get("timestamp") or envelope.get("timestamp")),
        wa_message_id=str(wa_message_id) if wa_message_id else None,
        media_id=(body.get("media") or {}).get("url") if isinstance(body.get("media"), dict) else None,
        raw_payload={"event": event, "session": envelope.get("session"), **body},
        is_group=_waha_is_group(str(peer)),
        is_forwarded=bool(body.get("isForwarded")),
        status="sent" if from_me else "received",
    )


def _apply_waha_ack(db: Session, body: dict[str, Any]) -> None:
    wa_message_id = body.get("id") or body.get("messageId")
    ack_name = body.get("ackName") or body.get("status")
    ack = body.get("ack")
    if not wa_message_id:
        return
    status_map = {
        -1: "error",
        0: "pending",
        1: "sent",
        2: "delivered",
        3: "read",
        4: "played",
    }
    if isinstance(ack_name, str) and ack_name:
        state = ack_name.lower()
    else:
        try:
            state = status_map.get(int(ack), "sent")
        except (TypeError, ValueError):
            state = "sent"
    repo.update_message_status(db, str(wa_message_id), state)


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
