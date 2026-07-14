"""Outbound WhatsApp messages via WAHA API (not Meta Cloud API)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import WAHA_API_KEY, WAHA_BASE_URL, WAHA_SESSION

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15.0


class WahaApiError(Exception):
    """WAHA API call failed."""


def is_configured() -> bool:
    return bool(WAHA_BASE_URL and WAHA_SESSION)


def to_chat_id(wa_id: str) -> str:
    """Normalize a stored contact id to a WAHA chatId."""
    value = (wa_id or "").strip()
    if not value:
        raise WahaApiError("Empty WhatsApp id")
    if "@" in value:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise WahaApiError(f"Invalid WhatsApp id: {wa_id}")
    return f"{digits}@c.us"


def normalize_contact_id(chat_id: str) -> str:
    """Store personal chats as digits; keep group ids with @g.us."""
    value = (chat_id or "").strip()
    if not value:
        return value
    if value.endswith("@g.us"):
        return value
    return value.split("@", 1)[0]


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    return headers


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
