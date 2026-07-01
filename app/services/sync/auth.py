from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import SYNC_API_KEY, is_server_role

_bearer = HTTPBearer(auto_error=False)


def require_sync_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if not is_server_role():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sync API is only available on server role",
        )
    if not SYNC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SYNC_API_KEY is not configured on server",
        )
    if credentials is None or credentials.credentials != SYNC_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing sync API key",
        )
