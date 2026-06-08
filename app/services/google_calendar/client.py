import logging
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import GOOGLE_CALENDAR_ID
from app.services.google_calendar import auth
from app.services.google_calendar.schemas import EventCreate, EventDateTime, EventUpdate

logger = logging.getLogger(__name__)

CALENDAR_VERSION = "v3"


class GoogleCalendarClient:
    def __init__(self, calendar_id: str | None = None) -> None:
        self.calendar_id = calendar_id or GOOGLE_CALENDAR_ID

    def _service(self, creds: Credentials):
        return build("calendar", CALENDAR_VERSION, credentials=creds, cache_discovery=False)

    def _credentials(self) -> Credentials:
        creds = auth.load_credentials()
        if creds is None:
            raise PermissionError(
                "Google Calendar is not authorized. "
                "Call GET /api/v1/calendar/auth/url and POST /api/v1/calendar/auth/exchange."
            )
        return creds

    def list_calendars(self) -> list[dict[str, Any]]:
        service = self._service(self._credentials())
        result = service.calendarList().list().execute()
        return result.get("items", [])

    def list_events(
        self,
        *,
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
        page_token: str | None = None,
        q: str | None = None,
        single_events: bool = True,
        order_by: str = "startTime",
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service(self._credentials())
        cal_id = calendar_id or self.calendar_id
        params: dict[str, Any] = {
            "calendarId": cal_id,
            "maxResults": min(max_results, 2500),
            "singleEvents": single_events,
            "orderBy": order_by if single_events else None,
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        if page_token:
            params["pageToken"] = page_token
        if q:
            params["q"] = q
        params = {k: v for k, v in params.items() if v is not None}
        return service.events().list(**params).execute()

    def get_event(self, event_id: str, calendar_id: str | None = None) -> dict[str, Any]:
        service = self._service(self._credentials())
        cal_id = calendar_id or self.calendar_id
        return service.events().get(calendarId=cal_id, eventId=event_id).execute()

    def create_event(
        self,
        payload: EventCreate,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service(self._credentials())
        cal_id = calendar_id or self.calendar_id
        body = _event_body_from_create(payload)
        insert_kwargs: dict[str, Any] = {
            "calendarId": cal_id,
            "body": body,
            "sendUpdates": payload.send_updates,
        }
        if payload.conference:
            insert_kwargs["conferenceDataVersion"] = 1
        return service.events().insert(**insert_kwargs).execute()

    def update_event(
        self,
        event_id: str,
        payload: EventUpdate,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service(self._credentials())
        cal_id = calendar_id or self.calendar_id
        existing = service.events().get(calendarId=cal_id, eventId=event_id).execute()
        body = _merge_event_update(existing, payload)
        return (
            service.events()
            .patch(
                calendarId=cal_id,
                eventId=event_id,
                body=body,
                sendUpdates=payload.send_updates,
            )
            .execute()
        )

    def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        send_updates: str = "none",
    ) -> None:
        service = self._service(self._credentials())
        cal_id = calendar_id or self.calendar_id
        service.events().delete(
            calendarId=cal_id,
            eventId=event_id,
            sendUpdates=send_updates,
        ).execute()

    @staticmethod
    def http_error_detail(exc: HttpError) -> str:
        try:
            content = exc.content.decode("utf-8") if exc.content else str(exc)
        except (AttributeError, UnicodeDecodeError):
            content = str(exc)
        return content or "Google Calendar API error"


def _datetime_dict(dt: EventDateTime) -> dict[str, str]:
    data: dict[str, str] = {}
    if dt.date_time:
        data["dateTime"] = dt.date_time
    if dt.date:
        data["date"] = dt.date
    if dt.time_zone:
        data["timeZone"] = dt.time_zone
    return data


def _normalize_attendees(
    attendees: list | None,
) -> list[dict[str, str]] | None:
    if not attendees:
        return None
    result: list[dict[str, str]] = []
    for item in attendees:
        if isinstance(item, str):
            result.append({"email": item})
        else:
            entry: dict[str, str] = {"email": item.email}
            if item.optional is not None:
                entry["optional"] = str(item.optional).lower()
            if item.response_status:
                entry["responseStatus"] = item.response_status
            result.append(entry)
    return result


def _event_body_from_create(payload: EventCreate) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": payload.summary,
        "start": _datetime_dict(payload.start),
        "end": _datetime_dict(payload.end),
    }
    if payload.description:
        body["description"] = payload.description
    if payload.location:
        body["location"] = payload.location
    attendees = _normalize_attendees(payload.attendees)
    if attendees:
        body["attendees"] = attendees
    if payload.recurrence:
        body["recurrence"] = payload.recurrence
    if payload.color_id:
        body["colorId"] = payload.color_id
    if payload.conference:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"jarvis-{payload.summary[:32]}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    return body


def _merge_event_update(existing: dict[str, Any], payload: EventUpdate) -> dict[str, Any]:
    body = dict(existing)
    if payload.summary is not None:
        body["summary"] = payload.summary
    if payload.description is not None:
        body["description"] = payload.description
    if payload.location is not None:
        body["location"] = payload.location
    if payload.start is not None:
        body["start"] = _datetime_dict(payload.start)
    if payload.end is not None:
        body["end"] = _datetime_dict(payload.end)
    if payload.attendees is not None:
        body["attendees"] = _normalize_attendees(payload.attendees)
    if payload.recurrence is not None:
        body["recurrence"] = payload.recurrence
    if payload.color_id is not None:
        body["colorId"] = payload.color_id
    if payload.status is not None:
        body["status"] = payload.status
    return body
