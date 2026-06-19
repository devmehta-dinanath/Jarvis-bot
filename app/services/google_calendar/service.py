from typing import Any

from googleapiclient.errors import HttpError

from app.config import (
    GOOGLE_CALENDAR_CREDENTIALS_PATH,
    GOOGLE_CALENDAR_ID,
    GOOGLE_CALENDAR_REDIRECT_URI,
    GOOGLE_CALENDAR_SCOPES,
    GOOGLE_CALENDAR_TOKEN_PATH,
)
from app.services.google_calendar import auth
from app.services.google_calendar.client import GoogleCalendarClient
from app.services.google_calendar.schemas import (
    AuthStatusResponse,
    EventCreate,
    EventUpdate,
)


class GoogleCalendarService:
    def __init__(self) -> None:
        self.client = GoogleCalendarClient()

    def auth_status(self) -> AuthStatusResponse:
        return AuthStatusResponse(
            configured=auth.credentials_configured(),
            credentials_file_exists=GOOGLE_CALENDAR_CREDENTIALS_PATH.is_file(),
            token_file_exists=GOOGLE_CALENDAR_TOKEN_PATH.is_file(),
            authorized=auth.is_authorized(),
            calendar_id=GOOGLE_CALENDAR_ID,
            redirect_uri=GOOGLE_CALENDAR_REDIRECT_URI,
            scopes=GOOGLE_CALENDAR_SCOPES,
        )

    def authorization_url(self) -> str:
        return auth.authorization_url()

    def exchange_code(self, code: str) -> None:
        auth.exchange_code(code)

    def revoke(self) -> bool:
        return auth.revoke_token()

    def list_calendars(self) -> list[dict[str, Any]]:
        return self.client.list_calendars()

    def list_events(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.list_events(**kwargs)

    def get_event(self, event_id: str, calendar_id: str | None = None) -> dict[str, Any]:
        return self.client.get_event(event_id, calendar_id=calendar_id)

    def create_event(
        self,
        payload: EventCreate,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.create_event(payload, calendar_id=calendar_id)

    def update_event(
        self,
        event_id: str,
        payload: EventUpdate,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        return self.client.update_event(event_id, payload, calendar_id=calendar_id)

    def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        send_updates: str = "none",
    ) -> None:
        self.client.delete_event(
            event_id,
            calendar_id=calendar_id,
            send_updates=send_updates,
        )

    def query_freebusy(
        self,
        *,
        time_min: str,
        time_max: str,
        calendar_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.client.query_freebusy(
            time_min=time_min,
            time_max=time_max,
            calendar_ids=calendar_ids,
        )

    @staticmethod
    def google_error_message(exc: HttpError) -> str:
        return GoogleCalendarClient.http_error_detail(exc)


google_calendar_service = GoogleCalendarService()
