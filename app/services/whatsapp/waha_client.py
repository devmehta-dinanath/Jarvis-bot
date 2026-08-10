"""Outbound WhatsApp messages via WAHA API (not Meta Cloud API)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from urllib.parse import quote

from app.config import WAHA_API_KEY, WAHA_BASE_URL, WAHA_SESSION

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15.0

# lid (@lid) → phone digits cache (WhatsApp LID mappings are stable).
_lid_to_pn_cache: dict[str, str | None] = {}

# group JID → group subject cache (a group's name rarely changes mid-session).
_group_name_cache: dict[str, str | None] = {}

# phone digits → address-book-saved contact name cache (rarely changes mid-session).
_contact_name_cache: dict[str, str | None] = {}


class WahaApiError(Exception):
    """WAHA API call failed."""


def is_configured() -> bool:
    return bool(WAHA_BASE_URL and WAHA_SESSION)


def to_chat_id(wa_id: str) -> str:
    """Normalize a stored contact id to a WAHA chatId.

    Prefer keeping @lid / @g.us as-is — WAHA can send to LIDs directly.
    """
    value = (wa_id or "").strip()
    if not value:
        raise WahaApiError("Empty WhatsApp id")
    lower = value.lower()
    if lower.endswith(("@lid", "@g.us", "@c.us", "@newsletter", "@broadcast")):
        return value
    if "@s.whatsapp.net" in lower:
        return f"{value.split('@', 1)[0]}@c.us"
    if "@" in value:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise WahaApiError(f"Invalid WhatsApp id: {wa_id}")
    return f"{digits}@c.us"


def normalize_contact_id(chat_id: str) -> str:
    """Store a stable contact key for inbox rows.

    - Groups: keep full ``…@g.us``
    - LIDs: keep full ``…@lid`` when phone is unknown (required for replies)
    - Phone JIDs: store digits only
    """
    value = (chat_id or "").strip()
    if not value:
        return value
    lower = value.lower()
    if lower.endswith("@g.us") or lower.endswith("@newsletter"):
        return value
    if lower.endswith("@lid"):
        return value
    if "@" in value:
        return value.split("@", 1)[0]
    return value


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    return headers


def _get(path: str) -> dict[str, Any] | list[Any] | None:
    if not is_configured():
        return None
    url = f"{WAHA_BASE_URL}{path}"
    try:
        response = httpx.get(url, headers=_headers(), timeout=_REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("[WAHA] GET %s failed: %s", path, exc)
        return None
    if response.status_code >= 400:
        logger.warning("[WAHA] GET %s → %s: %s", path, response.status_code, response.text[:200])
        return None
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not is_configured():
        raise WahaApiError("WAHA not configured — set WAHA_BASE_URL and WAHA_SESSION")
    url = f"{WAHA_BASE_URL}{path}"
    try:
        response = httpx.post(
            url,
            json=payload,
            headers=_headers(),
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("[WAHA] request failed: %s", exc)
        raise WahaApiError(str(exc)) from exc

    if response.status_code >= 400:
        logger.error("[WAHA] error %s: %s", response.status_code, response.text)
        raise WahaApiError(f"{response.status_code}: {response.text}")

    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {"raw": response.text}
    return data if isinstance(data, dict) else {"data": data}


def _pn_digits(pn: str | None) -> str | None:
    if not pn or not isinstance(pn, str):
        return None
    value = pn.strip()
    if not value:
        return None
    if "@" in value:
        value = value.split("@", 1)[0]
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def extract_phone_from_payload(body: dict[str, Any]) -> str | None:
    """Best-effort phone (@c.us) from NOWEB/WEBJS webhook extras."""
    candidates: list[Any] = [
        body.get("fromPn"),
        body.get("senderPn"),
        body.get("participantPn"),
        body.get("authorPn"),
    ]
    data = body.get("_data")
    if isinstance(data, dict):
        key = data.get("key")
        if isinstance(key, dict):
            candidates.extend(
                [
                    key.get("senderPn"),
                    key.get("participantPn"),
                    key.get("remoteJidAlt"),
                ]
            )
        info = data.get("Info")
        if isinstance(info, dict):
            candidates.extend([info.get("SenderAlt"), info.get("ChatAlt")])
        candidates.extend(
            [
                data.get("senderPn"),
                data.get("participantPn"),
            ]
        )
    for item in candidates:
        if isinstance(item, str) and ("@c.us" in item or "@s.whatsapp.net" in item or item.isdigit()):
            digits = _pn_digits(item)
            if digits:
                return digits
    return None


def resolve_lid_to_phone(lid: str) -> str | None:
    """Map ``123@lid`` → phone digits via WAHA Lids API (cached)."""
    value = (lid or "").strip()
    if not value.endswith("@lid"):
        return None
    if value in _lid_to_pn_cache:
        return _lid_to_pn_cache[value]

    lid_id = value.split("@", 1)[0]
    # GET /api/{session}/lids/{lid}  — lid may be with or without @lid
    data = _get(f"/api/{WAHA_SESSION}/lids/{lid_id}")
    pn: str | None = None
    if isinstance(data, dict):
        pn = _pn_digits(data.get("pn") or data.get("phoneNumber") or data.get("phone"))
    _lid_to_pn_cache[value] = pn
    if pn:
        logger.info("[WAHA] Resolved LID %s → %s", value, pn)
    else:
        logger.info("[WAHA] No phone mapping for LID %s", value)
    return pn


def _clean_name(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def fetch_group_name(group_id: str) -> str | None:
    """Best-effort fetch of a group's subject/name via the WAHA REST API.

    Webhook payloads only ever carry the *sender's* pushName (see
    ``webhook._waha_profile_name``) — never the group's own subject — so a group's
    display name has to be looked up separately. Tries the dedicated groups endpoint
    first, then falls back to the general chats endpoint some WAHA engines expose
    instead; either may return the name under ``name`` or ``subject`` depending on
    engine/version, so both are checked. Cached in-memory per process.
    """
    value = (group_id or "").strip()
    if not value.endswith("@g.us"):
        return None
    if value in _group_name_cache:
        return _group_name_cache[value]

    encoded = quote(value, safe="")
    name: str | None = None

    data = _get(f"/api/{WAHA_SESSION}/groups/{encoded}")
    if isinstance(data, dict):
        name = _clean_name(data.get("name")) or _clean_name(data.get("subject"))

    if not name:
        data = _get(f"/api/{WAHA_SESSION}/chats/{encoded}")
        if isinstance(data, dict):
            name = _clean_name(data.get("name")) or _clean_name(data.get("subject"))

    _group_name_cache[value] = name
    if name:
        logger.info("[WAHA] Resolved group name for %s → %s", value, name)
    else:
        logger.info(
            "[WAHA] Could not resolve a name for group %s — groups/chats endpoint "
            "returned no name/subject field",
            value,
        )
    return name


def fetch_contact_name(wa_id: str) -> str | None:
    """Best-effort fetch of a 1:1 contact's name as saved in the connected phone's own
    address book, via WAHA's Contacts API.

    This is deliberately separate from ``webhook._waha_profile_name()``, which only ever
    reads ``pushName``/``notifyName``/etc. off the message envelope — that's the
    contact's own self-chosen WhatsApp display name, not what the phone's owner has them
    saved as (e.g. "Mom", "Rahul - Plumber"). WAHA's contact object distinguishes the two
    via separate ``name`` (address-book) and ``pushname`` (self-declared) fields; only
    ``name`` is used here. Tries the query-param contacts endpoint first (the documented
    WAHA shape), then a path-style fallback some engine/version combos expose instead.
    Only applies to phone-number contacts — groups use ``fetch_group_name``, and LIDs
    (obscured numbers) aren't looked up. Cached in-memory per process.
    """
    value = (wa_id or "").strip()
    if not value or value.endswith(("@g.us", "@lid")):
        return None
    if value in _contact_name_cache:
        return _contact_name_cache[value]

    try:
        chat_id = to_chat_id(value)
    except WahaApiError:
        return None
    encoded = quote(chat_id, safe="")
    name: str | None = None

    data = _get(f"/api/contacts?session={WAHA_SESSION}&contactId={encoded}")
    if isinstance(data, dict):
        name = _clean_name(data.get("name")) or _clean_name(data.get("shortName"))

    if not name:
        data = _get(f"/api/{WAHA_SESSION}/contacts/{encoded}")
        if isinstance(data, dict):
            name = _clean_name(data.get("name")) or _clean_name(data.get("shortName"))

    _contact_name_cache[value] = name
    if name:
        logger.info("[WAHA] Resolved saved contact name for %s → %s", value, name)
    else:
        logger.info(
            "[WAHA] No address-book name for %s — falling back to pushName", value
        )
    return name


def resolve_contact_wa_id(peer: str, body: dict[str, Any] | None = None) -> str:
    """Turn webhook peer JID into the wa_id we store (phone digits preferred)."""
    peer = (peer or "").strip()
    if not peer:
        return peer

    body = body or {}
    lower = peer.lower()

    if lower.endswith("@g.us") or lower.endswith("@newsletter"):
        return peer

    # Explicit phone from payload beats LID.
    phone = extract_phone_from_payload(body)
    if phone:
        return phone

    if lower.endswith("@lid"):
        resolved = resolve_lid_to_phone(peer)
        if resolved:
            return resolved
        # Keep full LID so outbound sendText can use it as chatId.
        return peer

    if lower.endswith("@c.us") or "@s.whatsapp.net" in lower:
        return peer.split("@", 1)[0]

    return normalize_contact_id(peer)


def _extract_message_id(result: dict[str, Any]) -> str | None:
    for key in ("id", "messageId", "key"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = value.get("id") or value.get("_serialized")
            if nested:
                return str(nested)
    return None


def send_text(to: str, body: str) -> tuple[str | None, dict[str, Any]]:
    """Send a free-form text message via WAHA. Returns (wa_message_id, raw_response)."""
    payload = {
        "session": WAHA_SESSION,
        "chatId": to_chat_id(to),
        "text": body,
    }
    result = _post("/api/sendText", payload)
    return _extract_message_id(result), result


def send_template(
    to: str,
    template_name: str,
    *,
    language: str | None = None,
    components: list[dict[str, Any]] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """WAHA has no Meta templates — send a plain-text fallback."""
    del language, components
    text = f"[template: {template_name}]"
    logger.warning(
        "[WAHA] Templates are not supported; sending plain text fallback for %s",
        template_name,
    )
    return send_text(to, text)
