from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventDateTime(BaseModel):
    """Google Calendar event start/end (dateTime or all-day date)."""

    model_config = ConfigDict(populate_by_name=True)

    date_time: str | None = Field(default=None, alias="dateTime")
    date: str | None = None
    time_zone: str | None = Field(default=None, alias="timeZone")


class AttendeeInput(BaseModel):
    email: str
    optional: bool | None = None
    response_status: str | None = Field(default=None, alias="responseStatus")


class EventCreate(BaseModel):
    summary: str
    description: str | None = None
    location: str | None = None
    start: EventDateTime
    end: EventDateTime
    attendees: list[AttendeeInput] | list[str] | None = None
    recurrence: list[str] | None = None
    color_id: str | None = Field(default=None, alias="colorId")
    send_updates: str = Field(
        default="none",
        description="all | externalOnly | none",
    )
    conference: bool = Field(
        default=False,
        description="Request a Google Meet link on create",
    )


class EventUpdate(BaseModel):
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: EventDateTime | None = None
    end: EventDateTime | None = None
    attendees: list[AttendeeInput] | list[str] | None = None
    recurrence: list[str] | None = None
    color_id: str | None = Field(default=None, alias="colorId")
    status: str | None = Field(
        default=None,
        description="confirmed | tentative | cancelled",
    )
    send_updates: str = Field(default="none")


class EventResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: str | None = None
    html_link: str | None = Field(default=None, alias="htmlLink")
    created: str | None = None
    updated: str | None = None
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: dict[str, Any] | None = None
    end: dict[str, Any] | None = None
    attendees: list[dict[str, Any]] | None = None
    organizer: dict[str, Any] | None = None
    recurrence: list[str] | None = None
    hangout_link: str | None = Field(default=None, alias="hangoutLink")
    conference_data: dict[str, Any] | None = Field(default=None, alias="conferenceData")


class EventListResponse(BaseModel):
    calendar_id: str
    items: list[dict[str, Any]]
    next_page_token: str | None = Field(default=None, alias="nextPageToken")
    time_min: str | None = None
    time_max: str | None = None


class CalendarListResponse(BaseModel):
    items: list[dict[str, Any]]


class AuthUrlResponse(BaseModel):
    authorization_url: str
    redirect_uri: str
    scopes: list[str]


class AuthExchangeRequest(BaseModel):
    code: str


class AuthStatusResponse(BaseModel):
    configured: bool
    credentials_file_exists: bool
    token_file_exists: bool
    authorized: bool
    calendar_id: str
    redirect_uri: str
    scopes: list[str]


class CalendarStatusResponse(BaseModel):
    google_calendar: AuthStatusResponse
