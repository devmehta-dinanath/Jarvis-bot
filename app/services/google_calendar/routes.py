import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from googleapiclient.errors import HttpError

from app.config import GOOGLE_CALENDAR_ID
from app.services.google_calendar.schemas import (
    AuthExchangeRequest,
    AuthStatusResponse,
    AuthUrlResponse,
    CalendarListResponse,
    CalendarStatusResponse,
    EventCreate,
    EventListResponse,
    EventResponse,
    EventUpdate,
)
from app.services.google_calendar.service import google_calendar_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["google-calendar"])


def _handle_google_error(exc: HttpError) -> None:
    detail = google_calendar_service.google_error_message(exc)
    code = exc.resp.status if exc.resp else status.HTTP_502_BAD_GATEWAY
    if code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
    if code in (401, 403):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail) from exc
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc


def _require_configured() -> None:
    status_info = google_calendar_service.auth_status()
    if not status_info.credentials_file_exists:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Google OAuth client secrets missing. Place credentials JSON at "
                "data/google_calendar_credentials.json or set GOOGLE_CALENDAR_CREDENTIALS_PATH."
            ),
        )


def _require_authorized() -> None:
    _require_configured()
    if not google_calendar_service.auth_status().authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Google Calendar not authorized. "
                "GET /api/v1/calendar/auth/url then POST /api/v1/calendar/auth/exchange with the code."
            ),
        )


@router.get("/status", response_model=CalendarStatusResponse)
def calendar_status() -> CalendarStatusResponse:
    return CalendarStatusResponse(
        google_calendar=google_calendar_service.auth_status(),
    )


@router.get("/auth/status", response_model=AuthStatusResponse)
def auth_status() -> AuthStatusResponse:
    return google_calendar_service.auth_status()


@router.get("/auth/url", response_model=AuthUrlResponse)
def auth_url() -> AuthUrlResponse:
    _require_configured()
    try:
        url = google_calendar_service.authorization_url()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    info = google_calendar_service.auth_status()
    return AuthUrlResponse(
        authorization_url=url,
        redirect_uri=info.redirect_uri,
        scopes=info.scopes,
    )


@router.get("/auth/callback")
def auth_callback(code: str | None = None, error: str | None = None) -> dict:
    """Browser redirect target after Google OAuth (also usable via GET with ?code=)."""
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth error: {error}",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing ?code= query parameter.",
        )
    _require_configured()
    try:
        google_calendar_service.exchange_code(code)
    except Exception as exc:
        logger.exception("OAuth token exchange failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=str(exc),
        ) from exc
    return {
        "status": "authorized",
        "message": "Google Calendar connected. You can close this tab and use the API.",
    }


@router.post("/auth/exchange")
def auth_exchange(payload: AuthExchangeRequest) -> dict:
    """Exchange an OAuth authorization code for a stored refresh token."""
    _require_configured()
    try:
        google_calendar_service.exchange_code(payload.code)
    except Exception as exc:
        logger.exception("OAuth token exchange failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"status": "authorized"}


@router.delete("/auth/token", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token() -> None:
    google_calendar_service.revoke()


@router.get("/calendars", response_model=CalendarListResponse)
def list_calendars() -> CalendarListResponse:
    _require_authorized()
    try:
        items = google_calendar_service.list_calendars()
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        _handle_google_error(exc)
    return CalendarListResponse(items=items)


@router.get("/events", response_model=EventListResponse)
def list_events(
    calendar_id: str | None = Query(default=None, description="Defaults to GOOGLE_CALENDAR_ID"),
    time_min: datetime | None = Query(default=None),
    time_max: datetime | None = Query(default=None),
    max_results: int = Query(default=50, ge=1, le=2500),
    page_token: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Free-text search"),
    single_events: bool = Query(default=True),
) -> EventListResponse:
    _require_authorized()
    cal_id = calendar_id or GOOGLE_CALENDAR_ID
    time_min_rfc = (
        time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if time_min
        else None
    )
    time_max_rfc = (
        time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if time_max
        else None
    )
    try:
        result = google_calendar_service.list_events(
            calendar_id=cal_id,
            time_min=time_min_rfc,
            time_max=time_max_rfc,
            max_results=max_results,
            page_token=page_token,
            q=q,
            single_events=single_events,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        _handle_google_error(exc)
    return EventListResponse(
        calendar_id=cal_id,
        items=result.get("items", []),
        next_page_token=result.get("nextPageToken"),
        time_min=time_min_rfc,
        time_max=time_max_rfc,
    )


@router.get("/events/{event_id}", response_model=EventResponse)
def get_event(
    event_id: str,
    calendar_id: str | None = Query(default=None),
) -> EventResponse:
    _require_authorized()
    try:
        event = google_calendar_service.get_event(event_id, calendar_id=calendar_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        _handle_google_error(exc)
    return EventResponse.model_validate(event)


@router.post(
    "/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_event(
    payload: EventCreate,
    calendar_id: str | None = Query(default=None),
) -> EventResponse:
    _require_authorized()
    try:
        event = google_calendar_service.create_event(payload, calendar_id=calendar_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        _handle_google_error(exc)
    return EventResponse.model_validate(event)


@router.patch("/events/{event_id}", response_model=EventResponse)
def update_event(
    event_id: str,
    payload: EventUpdate,
    calendar_id: str | None = Query(default=None),
) -> EventResponse:
    _require_authorized()
    try:
        event = google_calendar_service.update_event(
            event_id,
            payload,
            calendar_id=calendar_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        _handle_google_error(exc)
    return EventResponse.model_validate(event)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: str,
    calendar_id: str | None = Query(default=None),
    send_updates: str = Query(default="none"),
) -> None:
    _require_authorized()
    try:
        google_calendar_service.delete_event(
            event_id,
            calendar_id=calendar_id,
            send_updates=send_updates,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        _handle_google_error(exc)
