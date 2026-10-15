"""Optional bearer auth for destructive-ish WhatsApp maintenance endpoints."""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import SYNC_API_KEY

_bearer = HTTPBearer(auto_error=False)


def _configured_reprocess_token() -> str | None:
    explicit = (os.getenv("WHATSAPP_REPROCESS_TOKEN") or "").strip()
    if explicit:
        return explicit
    if SYNC_API_KEY:
        return SYNC_API_KEY
    return None


def require_reprocess_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Require Bearer token when WHATSAPP_REPROCESS_TOKEN or SYNC_API_KEY is set."""
    expected = _configured_reprocess_token()
    if not expected:
        return
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing reprocess authorization token",
        )
