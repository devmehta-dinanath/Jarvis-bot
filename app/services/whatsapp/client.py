import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_BASE,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15.0


class WhatsAppApiError(Exception):
    """Outbound WhatsApp Cloud API call failed."""


def is_configured() -> bool:
    return bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID)


def _messages_url() -> str:
    return (
        f"{WHATSAPP_API_BASE}/{WHATSAPP_API_VERSION}/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    if not is_configured():
        raise WhatsAppApiError(
            "WhatsApp not configured — set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID"
        )
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            _messages_url(),
            json=payload,
            headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("[WHATSAPP] Graph API request failed: %s", exc)
        raise WhatsAppApiError(str(exc)) from exc

    if response.status_code >= 400:
        logger.error(
            "[WHATSAPP] Graph API error %s: %s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(f"{response.status_code}: {response.text}")
    return response.json()


def _extract_message_id(result: dict[str, Any]) -> str | None:
    messages = result.get("messages")
    if isinstance(messages, list) and messages:
        return messages[0].get("id")
    return None


def send_text(to: str, body: str) -> tuple[str | None, dict[str, Any]]:
    """Send a free-form text message. Returns (wa_message_id, raw_response)."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    result = _post(payload)
    return _extract_message_id(result), result


def send_template(
    to: str,
    template_name: str,
    *,
    language: str | None = None,
    components: list[dict[str, Any]] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Send a pre-approved template message. Returns (wa_message_id, raw_response)."""
    template: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language or WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE},
    }
    if components:
        template["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": template,
    }
    result = _post(payload)
    return _extract_message_id(result), result


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Verify Meta's X-Hub-Signature-256 header against the app secret.

    If no app secret is configured we skip verification (returns True) so the
    pipeline still works in local/testing setups.
    """
    if not WHATSAPP_APP_SECRET:
        logger.warning(
            "[WHATSAPP] WHATSAPP_APP_SECRET not set — skipping webhook signature verification"
        )
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)
