import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import (
    WAHA_HMAC_KEY,
    WHATSAPP_API_BASE,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_PROVIDER,
    WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE,
)
from app.services.whatsapp.auth import get_access_token, refresh_if_needed

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15.0


class WhatsAppApiError(Exception):
    """Outbound WhatsApp API call failed."""


def uses_waha() -> bool:
    return WHATSAPP_PROVIDER == "waha"


def is_configured() -> bool:
    if uses_waha():
        from app.services.whatsapp import waha_client

        return waha_client.is_configured()
    return bool(get_access_token() and WHATSAPP_PHONE_NUMBER_ID)


def _messages_url() -> str:
    return (
        f"{WHATSAPP_API_BASE}/{WHATSAPP_API_VERSION}/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )


def _post(payload: dict[str, Any], *, allow_token_retry: bool = True) -> dict[str, Any]:
    if not is_configured():
        raise WhatsAppApiError(
            "WhatsApp not configured — set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID"
        )
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
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

    if response.status_code in {401, 403} and allow_token_retry and refresh_if_needed(force=True):
        return _post(payload, allow_token_retry=False)

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
    if uses_waha():
        from app.services.whatsapp import waha_client
        from app.services.whatsapp.waha_client import WahaApiError

        try:
            return waha_client.send_text(to, body)
        except WahaApiError as exc:
            raise WhatsAppApiError(str(exc)) from exc

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
    """Send a template (Meta) or plain-text fallback (WAHA)."""
    if uses_waha():
        from app.services.whatsapp import waha_client
        from app.services.whatsapp.waha_client import WahaApiError

        try:
            return waha_client.send_template(
                to,
                template_name,
                language=language,
                components=components,
            )
        except WahaApiError as exc:
            raise WhatsAppApiError(str(exc)) from exc

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
    """Verify inbound webhook authenticity for the active provider."""
    if uses_waha():
        return _verify_waha_signature(raw_body, signature_header)

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


def _verify_waha_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Optional WAHA HMAC. If WAHA_HMAC_KEY is unset, accept all payloads."""
    if not WAHA_HMAC_KEY:
        return True
    if not signature_header:
        return False
    provided = signature_header
    if provided.startswith("sha512="):
        provided = provided.split("=", 1)[1]
    expected = hmac.new(
        WAHA_HMAC_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, provided)
