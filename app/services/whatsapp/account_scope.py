"""Show Inbox chats only for the WhatsApp account currently linked in WAHA.

Chats captured under a different login stay in the database. Logging back into
that same account shows them again.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import column, table, text
from sqlalchemy.orm import Session

from app import models
from app.services.whatsapp import waha_client

logger = logging.getLogger(__name__)

# Chats we could not tie to the phone that is logged in now.
OTHER_ACCOUNT = "__other_account__"

_ACCOUNTS = table(
    "whatsapp_contact_accounts",
    column("contact_id"),
    column("account_key"),
)


def _digits(value: object) -> str:
    if not value:
        return ""
    head = str(value).split("@", 1)[0]
    return "".join(ch for ch in head if ch.isdigit())


def owner_key_from_payload(body: dict | None) -> str | None:
    """Phone digits of the WAHA account that received or sent this payload.

    On an incoming message that number is ``to``. On an outgoing message it is
    ``from``, when that field is the account rather than the other person.
    """
    if not isinstance(body, dict):
        return None
    from_me = waha_client.payload_is_from_me(body)
    sources: list[dict] = [body]
    data = body.get("_data")
    if isinstance(data, dict):
        sources.append(data)
    for src in sources:
        from_digits = _digits(src.get("from"))
        to_digits = _digits(src.get("to"))
        chat_digits = _digits(src.get("chatId") or body.get("chatId"))
        if not from_me:
            if len(to_digits) >= 8 and to_digits != from_digits:
                return to_digits
        else:
            peer = chat_digits or to_digits
            if len(from_digits) >= 8 and from_digits != peer:
                return from_digits
    return None


def key_for_unscoped_contact(
    inferred: str | None,
    current: str | None,
    *,
    saw_current: bool,
    saw_other: bool,
) -> str | None:
    """Which account a chat with no saved link belongs to.

    If any saved chat names an owner and this one does not match the phone
    logged in now, keep it off that phone's Inbox. If nothing in the database
    names an owner, keep the history — that is the same account reconnecting
    without owner ids in the payload.
    """
    if inferred:
        return inferred
    if not current:
        return None
    if saw_current or saw_other:
        return OTHER_ACCOUNT
    return current


def ensure_account_table(db: Session) -> None:
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS whatsapp_contact_accounts ("
            "contact_id INTEGER PRIMARY KEY, "
            "account_key VARCHAR(32) NOT NULL)"
        )
    )


def contact_account_key(db: Session, contact_id: int) -> str | None:
    ensure_account_table(db)
    return db.execute(
        text(
            "SELECT account_key FROM whatsapp_contact_accounts "
            "WHERE contact_id = :contact_id"
        ),
        {"contact_id": contact_id},
    ).scalar()


def remember_contact_account(db: Session, contact_id: int, account_key: str | None) -> None:
    """Attach a new chat to the account that is logged in. Never retag an existing chat."""
    if not account_key:
        return
    ensure_account_table(db)
    existing = contact_account_key(db, contact_id)
    if existing:
        return
    db.execute(
        text(
            "INSERT INTO whatsapp_contact_accounts (contact_id, account_key) "
            "VALUES (:contact_id, :account_key)"
        ),
        {"contact_id": contact_id, "account_key": account_key},
    )


def _infer_contact_owner_key(db: Session, contact_id: int) -> str | None:
    rows = (
        db.query(models.WhatsAppMessage.raw_payload)
        .filter(models.WhatsAppMessage.contact_id == contact_id)
        .order_by(models.WhatsAppMessage.id.desc())
        .limit(40)
        .all()
    )
    for (raw,) in rows:
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        key = owner_key_from_payload(payload)
        if key:
            return key
    return None


def scope_contacts_to_connected_account(db: Session) -> str | None:
    """Tag chats that have no account yet, and return the phone logged in now."""
    ensure_account_table(db)
    current = waha_client.connected_account_key()
    missing = (
        db.query(models.WhatsAppContact)
        .outerjoin(_ACCOUNTS, _ACCOUNTS.c.contact_id == models.WhatsAppContact.id)
        .filter(_ACCOUNTS.c.contact_id.is_(None))
        .all()
    )
    if not missing:
        return current

    inferred = {contact.id: _infer_contact_owner_key(db, contact.id) for contact in missing}
    saw_current = bool(current and current in set(inferred.values()))
    saw_other = any(key and key != current for key in inferred.values())
    wrote = False
    for contact in missing:
        key = key_for_unscoped_contact(
            inferred[contact.id],
            current,
            saw_current=saw_current,
            saw_other=saw_other,
        )
        if not key:
            continue
        remember_contact_account(db, contact.id, key)
        wrote = True
    if wrote:
        db.commit()
        logger.info(
            "[WHATSAPP] Scoped %s chat(s) against connected account %s",
            len(missing),
            current or "unknown",
        )
    return current


def apply_connected_account_filter(query, db: Session, contact_id_column):
    """Limit a query to chats from the WhatsApp account linked in WAHA right now."""
    current = scope_contacts_to_connected_account(db)
    if not current:
        return query
    return query.join(
        _ACCOUNTS, _ACCOUNTS.c.contact_id == contact_id_column
    ).filter(_ACCOUNTS.c.account_key == current)


def contact_visible_for_connected_account(db: Session, contact_id: int) -> bool:
    current = scope_contacts_to_connected_account(db)
    if not current:
        return True
    return contact_account_key(db, contact_id) == current
