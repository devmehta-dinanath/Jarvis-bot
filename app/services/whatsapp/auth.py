import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_BASE,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_ID,
    WHATSAPP_APP_SECRET,
    WHATSAPP_TOKEN_PATH,
    WHATSAPP_TOKEN_REFRESH_CHECK_INTERVAL_SECONDS,
    WHATSAPP_TOKEN_REFRESH_DAYS_BEFORE_EXPIRY,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15.0

_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_expires_at: int | None = None
_refresh_thread: threading.Thread | None = None
_refresh_stop = threading.Event()


class WhatsAppTokenError(Exception):
    """WhatsApp access token could not be loaded or refreshed."""


def can_auto_refresh() -> bool:
    return bool(WHATSAPP_APP_ID and WHATSAPP_APP_SECRET)


def get_access_token() -> str | None:
    with _token_lock:
        if _cached_token:
            return _cached_token
        loaded = _load_persisted_token()
        if loaded:
            _apply_token(*loaded, persist=False)
            return _cached_token
        if WHATSAPP_ACCESS_TOKEN:
            _apply_token(WHATSAPP_ACCESS_TOKEN, None, persist=False)
            return _cached_token
    return None


def token_status() -> dict[str, Any]:
    token = get_access_token()
    if not token:
        return {
            "configured": False,
            "auto_refresh_enabled": can_auto_refresh(),
            "expires_at": None,
            "expires_in_days": None,
            "needs_refresh": False,
            "source": None,
        }

    expires_at = _cached_expires_at
    source = "cache"
    if expires_at is None:
        persisted = _read_token_file()
        if persisted and persisted.get("access_token") == token:
            expires_at = persisted.get("expires_at")
            source = "file"
        elif token == WHATSAPP_ACCESS_TOKEN:
            source = "env"

    if expires_at is None and can_auto_refresh():
        try:
            inspected = inspect_token(token)
            expires_at = inspected.get("expires_at")
            if inspected.get("is_valid"):
                _apply_token(token, expires_at, persist=True)
        except WhatsAppTokenError:
            pass

    expires_in_days = None
    needs_refresh = False
    if expires_at == 0:
        needs_refresh = False
    elif expires_at:
        remaining = expires_at - int(datetime.now(timezone.utc).timestamp())
        expires_in_days = round(remaining / 86400, 1)
        needs_refresh = remaining <= WHATSAPP_TOKEN_REFRESH_DAYS_BEFORE_EXPIRY * 86400

    return {
        "configured": True,
        "auto_refresh_enabled": can_auto_refresh(),
        "expires_at": expires_at if expires_at else None,
        "expires_in_days": expires_in_days,
        "needs_refresh": needs_refresh,
        "source": source,
    }


def refresh_if_needed(*, force: bool = False) -> bool:
    if not can_auto_refresh():
        return False

    token = get_access_token()
    if not token:
        return False

    if not force:
        status = token_status()
        if status.get("expires_at") == 0:
            return False
        if status.get("expires_at") and not status.get("needs_refresh"):
            return False

    try:
        new_token, expires_in = exchange_token(token)
    except WhatsAppTokenError as exc:
        logger.error("[WHATSAPP] Failed to refresh access token: %s", exc)
        return False

    expires_at = None
    if expires_in:
        expires_at = int(datetime.now(timezone.utc).timestamp()) + int(expires_in)

    with _token_lock:
        _apply_token(new_token, expires_at, persist=True)

    logger.info(
        "[WHATSAPP] Refreshed access token (expires_in_days=%s)",
        round(expires_in / 86400, 1) if expires_in else "never",
    )
    return True


def exchange_token(current_token: str) -> tuple[str, int | None]:
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": WHATSAPP_APP_ID,
        "client_secret": WHATSAPP_APP_SECRET,
        "fb_exchange_token": current_token,
        "set_token_expires_in_60_days": "true",
    }
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_API_VERSION}/oauth/access_token"
    try:
        response = httpx.get(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise WhatsAppTokenError(str(exc)) from exc

    if response.status_code >= 400:
        # Non-expiring system user tokens may reject set_token_expires_in_60_days.
        if "set_token_expires_in_60_days" in response.text:
            params.pop("set_token_expires_in_60_days", None)
            try:
                response = httpx.get(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
            except httpx.HTTPError as exc:
                raise WhatsAppTokenError(str(exc)) from exc
        if response.status_code >= 400:
            raise WhatsAppTokenError(f"{response.status_code}: {response.text}")

    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise WhatsAppTokenError(f"Token exchange returned no access_token: {payload}")
    expires_in = payload.get("expires_in")
    return access_token, int(expires_in) if expires_in else None


def inspect_token(token: str) -> dict[str, Any]:
    if not can_auto_refresh():
        raise WhatsAppTokenError("WHATSAPP_APP_ID and WHATSAPP_APP_SECRET are required")
    app_access_token = f"{WHATSAPP_APP_ID}|{WHATSAPP_APP_SECRET}"
    url = f"{WHATSAPP_API_BASE}/debug_token"
    params = {"input_token": token, "access_token": app_access_token}
    try:
        response = httpx.get(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise WhatsAppTokenError(str(exc)) from exc
    if response.status_code >= 400:
        raise WhatsAppTokenError(f"{response.status_code}: {response.text}")

    data = response.json().get("data") or {}
    return {
        "is_valid": bool(data.get("is_valid")),
        "expires_at": int(data["expires_at"]) if data.get("expires_at") else 0,
        "type": data.get("type"),
    }


def start_refresh_worker() -> None:
    global _refresh_thread
    if not can_auto_refresh():
        logger.info(
            "[WHATSAPP] Token auto-refresh disabled — set WHATSAPP_APP_ID and WHATSAPP_APP_SECRET"
        )
        return
    if _refresh_thread and _refresh_thread.is_alive():
        return

    refresh_if_needed()

    _refresh_stop.clear()
    _refresh_thread = threading.Thread(
        target=_refresh_loop,
        name="whatsapp-token-refresh",
        daemon=True,
    )
    _refresh_thread.start()
    logger.info(
        "[WHATSAPP] Token refresh worker started (check every %s s)",
        WHATSAPP_TOKEN_REFRESH_CHECK_INTERVAL_SECONDS,
    )


def stop_refresh_worker() -> None:
    _refresh_stop.set()
    if _refresh_thread:
        _refresh_thread.join(timeout=5)


def _refresh_loop() -> None:
    while not _refresh_stop.is_set():
        try:
            refresh_if_needed()
        except Exception:
            logger.exception("[WHATSAPP] Token refresh check failed")
        _refresh_stop.wait(WHATSAPP_TOKEN_REFRESH_CHECK_INTERVAL_SECONDS)


def _apply_token(token: str, expires_at: int | None, *, persist: bool) -> None:
    global _cached_token, _cached_expires_at
    _cached_token = token
    _cached_expires_at = expires_at
    if persist:
        _write_token_file(token, expires_at)


def _load_persisted_token() -> tuple[str, int | None] | None:
    data = _read_token_file()
    if not data:
        return None
    token = (data.get("access_token") or "").strip()
    if not token:
        return None
    expires_at = data.get("expires_at")
    return token, int(expires_at) if expires_at else None


def _read_token_file() -> dict[str, Any] | None:
    path = WHATSAPP_TOKEN_PATH
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[WHATSAPP] Invalid token file %s: %s", path, exc)
        return None


def _write_token_file(token: str, expires_at: int | None) -> None:
    path = WHATSAPP_TOKEN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token,
        "expires_at": expires_at,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("[WHATSAPP] Saved access token to %s", path)
