import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import (
    GOOGLE_CALENDAR_CREDENTIALS_PATH,
    GOOGLE_CALENDAR_REDIRECT_URI,
    GOOGLE_CALENDAR_SCOPES,
    GOOGLE_CALENDAR_TOKEN_PATH,
)

logger = logging.getLogger(__name__)


def credentials_configured() -> bool:
    return GOOGLE_CALENDAR_CREDENTIALS_PATH.is_file()


def token_exists() -> bool:
    return GOOGLE_CALENDAR_TOKEN_PATH.is_file()


def is_authorized() -> bool:
    creds = load_credentials()
    return creds is not None and creds.valid


def load_credentials() -> Credentials | None:
    if not GOOGLE_CALENDAR_TOKEN_PATH.is_file():
        return None
    try:
        creds = Credentials.from_authorized_user_file(
            str(GOOGLE_CALENDAR_TOKEN_PATH),
            scopes=GOOGLE_CALENDAR_SCOPES,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Invalid Google Calendar token file: %s", exc)
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds)
        except OSError as exc:
            logger.warning("Failed to refresh Google Calendar token: %s", exc)
            return None
    return creds if creds and creds.valid else None


def save_credentials(creds: Credentials) -> None:
    GOOGLE_CALENDAR_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_CALENDAR_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Saved Google Calendar token to %s", GOOGLE_CALENDAR_TOKEN_PATH)


def create_oauth_flow() -> Flow:
    if not credentials_configured():
        raise FileNotFoundError(
            f"OAuth client secrets not found at {GOOGLE_CALENDAR_CREDENTIALS_PATH}. "
            "Download credentials.json from Google Cloud Console."
        )
    return Flow.from_client_secrets_file(
        str(GOOGLE_CALENDAR_CREDENTIALS_PATH),
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=GOOGLE_CALENDAR_REDIRECT_URI,
    )


def authorization_url() -> str:
    flow = create_oauth_flow()
    url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


def exchange_code(code: str) -> Credentials:
    flow = create_oauth_flow()
    flow.fetch_token(code=code.strip())
    creds = flow.credentials
    save_credentials(creds)
    return creds
                                                                                                             

def revoke_token() -> bool:
    if not GOOGLE_CALENDAR_TOKEN_PATH.is_file():
        return False
    GOOGLE_CALENDAR_TOKEN_PATH.unlink(missing_ok=True)
    return True
