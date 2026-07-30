import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import WHATSAPP_PROVIDER
from app.services.whatsapp import repository as repo

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
        is_group = _is_group_message(message, value)
        contact = repo.upsert_contact(
            db, wa_id=wa_id, profile_name=profile_name, is_group=is_group
        )

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
            is_group=is_group,
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
        elif stored.direction == "outbound":
            # Typed directly in WhatsApp (not sent through the app) — treat it as the
            # user handling the conversation and clear older pending chips for this contact.
            cleared = repo.dismiss_pending_older_than(db, stored.contact_id, stored.timestamp)
            if cleared:
                logger.info(
                    "[WHATSAPP] Auto-dismissed %s older pending suggestion(s) for contact %s "
                    "after outbound WhatsApp reply",
                    cleared,
                    stored.contact_id,
                )
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
    # NOWEB nested message types
    nested = body.get("message")
    if isinstance(nested, dict):
        if nested.get("imageMessage"):
            return "image"
        if nested.get("audioMessage") or nested.get("pttMessage"):
            return "audio"
        if nested.get("videoMessage"):
            return "video"
        if nested.get("documentMessage"):
            return "document"
    return "text"


def _waha_group_id(body: dict[str, Any]) -> str | None:
    """Find whichever field actually carries the group JID.

    WAHA engines are inconsistent about which of `from` / `chatId` / `to`
    holds the group id vs. the individual sender, so check all three instead
    of trusting one field — otherwise a group message can get filed under
    the sender as if it were a 1:1 contact.
    """
    for key in ("from", "chatId", "to"):
        value = body.get(key)
        if value and str(value).endswith("@g.us"):
            return str(value)
    return None


def _waha_is_group(body: dict[str, Any]) -> bool:
    if _waha_group_id(body):
        return True
    # `participant` / `author` is only ever populated on group messages —
    # a reliable signal even on engines that never surface an @g.us id.
    return bool(body.get("participant") or body.get("author"))


def _waha_extract_text(body: dict[str, Any], msg_type: str) -> str | None:
    """Pull text from WEBJS/NOWEB payload shapes."""
    direct = body.get("body")
    if isinstance(direct, str) and direct.strip():
        return direct
    text_obj = body.get("text")
    if isinstance(text_obj, str) and text_obj.strip():
        return text_obj
    if isinstance(text_obj, dict):
        nested_body = text_obj.get("body")
        if isinstance(nested_body, str) and nested_body.strip():
            return nested_body
    caption = body.get("caption")
    if isinstance(caption, str) and caption.strip():
        return caption

    nested = body.get("message")
    if isinstance(nested, dict):
        conv = nested.get("conversation")
        if isinstance(conv, str) and conv.strip():
            return conv
        ext = nested.get("extendedTextMessage")
        if isinstance(ext, dict):
            ext_text = ext.get("text")
            if isinstance(ext_text, str) and ext_text.strip():
                return ext_text
        for media_key in ("imageMessage", "videoMessage", "documentMessage"):
            media = nested.get(media_key)
            if isinstance(media, dict) and isinstance(media.get("caption"), str):
                cap = media["caption"].strip()
                if cap:
                    return cap

    if msg_type != "text":
        media = body.get("media") or {}
        if isinstance(media, dict):
            return media.get("filename") or media.get("url")
    return None


def _waha_profile_name(body: dict[str, Any]) -> str | None:
    for key in ("pushName", "notifyName", "senderName", "name"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = body.get("_data")
    if isinstance(data, dict):
        for key in ("notifyName", "pushName", "verifiedBizName"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # Contact update style: notify on nested objects
        for key in ("notify", "verifiedName"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _waha_peer_chat_id(body: dict[str, Any]) -> str | None:
    """Resolve the contact/chat JID for inbox storage."""
    group_id = _waha_group_id(body)
    if group_id:
        # Always key a group chat by its own JID, whichever field it came from —
        # never file it under the individual participant who happened to send it.
        return group_id

    from_me = bool(body.get("fromMe"))
    chat_id = body.get("chatId")
    if from_me:
        peer = body.get("to") or chat_id or body.get("from")
    else:
        peer = body.get("from") or chat_id
        participant = body.get("participant") or body.get("author")
        # Some engines put the sender only in participant, with chatId=@lid/@c.us.
        if participant and not peer:
            peer = participant
    if peer:
        return str(peer)
    return None


def _store_waha_message(
    db: Session,
    envelope: dict[str, Any],
    body: dict[str, Any],
    *,
    event: str,
):
    from app.services.whatsapp.waha_client import resolve_contact_wa_id

    from_me = bool(body.get("fromMe"))
    # Avoid duplicating outbound messages we already persisted after sendText.
    if from_me and str(body.get("source") or "").lower() == "api":
        return None

    wa_message_id = body.get("id") or body.get("messageId")
    if isinstance(wa_message_id, dict):
        wa_message_id = (
            wa_message_id.get("_serialized")
            or wa_message_id.get("id")
            or wa_message_id.get("messageId")
        )
    if wa_message_id and repo.message_exists(db, str(wa_message_id)):
        return None

    peer = _waha_peer_chat_id(body)
    if not peer:
        logger.warning("[WAHA] message missing from/to/chatId: event=%s keys=%s", event, list(body.keys())[:20])
        return None

    wa_id = resolve_contact_wa_id(str(peer), body)
    is_group = _waha_is_group(body)
    if is_group:
        # _waha_profile_name() only ever carries the *sender's* pushName — using it here
        # would label the group with whoever last happened to post in it. Look up the
        # group's own subject instead (cached, so this only hits the API once per group).
        from app.services.whatsapp.waha_client import fetch_group_name

        profile_name = fetch_group_name(wa_id)
    else:
        profile_name = _waha_profile_name(body)
    contact = repo.upsert_contact(
        db, wa_id=wa_id, profile_name=profile_name, is_group=is_group
    )

    msg_type = _waha_msg_type(body)
    text_value = _waha_extract_text(body, msg_type)

    logger.info(
        "[WAHA] Storing event=%s peer=%s wa_id=%s type=%s from_me=%s body_len=%s",
        event,
        peer,
        wa_id,
        msg_type,
        from_me,
        len(text_value or ""),
    )

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
        is_group=is_group,
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


_MEDIA_MSG_TYPES = {"image", "video", "document", "sticker", "contacts", "location", "media"}


def _classify_if_enabled(db: Session, message) -> None:
    try:
        from app.services import service_manager
        from app.services.whatsapp.classifier import WhatsAppAIError

        if not service_manager.whatsapp.is_enabled:
            return

        if message.msg_type in ("audio", "voice"):
            service_manager.whatsapp.handle_voice_note_now(db, message)
            return

        # Media (photo/video/document/sticker/...) is never read or classified — not the
        # file, and not its caption either, even when one is present. Previously only
        # bodiless (caption-less) media was skipped here; a captioned PDF/photo still fell
        # through to the normal classifier below and raised a real suggestion card off the
        # caption text, which is the "still being shown media" bug this closes.
        if message.direction == "inbound" and message.msg_type in _MEDIA_MSG_TYPES:
            service_manager.whatsapp.handle_media_message_now(db, message)
            return

        if not (message.body or "").strip():
            return

        # Any message with usable text — a plain text message — goes through the normal
        # classifier.
        service_manager.whatsapp.classify_message_now(db, message)
    except WhatsAppAIError as exc:
        logger.warning("[WHATSAPP] Immediate classification failed for %s: %s", message.id, exc)
    except Exception:
        logger.exception("[WHATSAPP] Immediate classification error for message %s", message.id)
