from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --- Outbound / action request bodies ---


class SendMessageRequest(BaseModel):
    """Send a free-form text or a pre-approved template message to a contact."""

    to: str = Field(..., description="Recipient phone number in international format (wa_id)")
    mode: str = Field(
        default="text",
        description="text | template",
    )
    body: str | None = Field(default=None, description="Text body when mode=text")
    template_name: str | None = Field(default=None, description="Template name when mode=template")
    template_language: str | None = Field(
        default=None,
        description="Template language code, e.g. en_US",
    )
    template_components: list[dict[str, Any]] | None = Field(
        default=None,
        description="Raw Graph API template components array",
    )


class SendReplyRequest(BaseModel):
    """One-click send of an AI-drafted reply suggestion."""

    text: str | None = Field(
        default=None,
        description="Override the drafted text; defaults to the suggestion's draft_text",
    )
    mode: str = Field(
        default="auto",
        description="auto | text | template. auto = free-form within window, else template",
    )
    template_name: str | None = None
    template_language: str | None = None
    template_components: list[dict[str, Any]] | None = None


class AddToCalendarRequest(BaseModel):
    """Create a Google Calendar event from a meeting suggestion."""

    title: str | None = Field(default=None, description="Override the extracted meeting title")
    agenda: str | None = Field(default=None, description="Override the extracted agenda")
    start: str | None = Field(default=None, description="ISO start datetime override")
    end: str | None = Field(default=None, description="ISO end datetime override")
    calendar_id: str | None = None
    conference: bool = Field(default=False, description="Request a Google Meet link")


# --- Responses ---


class WhatsAppContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wa_id: str
    profile_name: str | None = None
    last_inbound_at: datetime | None = None
    last_message_at: datetime | None = None
    created_at: datetime
    within_customer_window: bool = False


class WhatsAppContactListResponse(BaseModel):
    items: list[WhatsAppContactResponse] = Field(default_factory=list)
    total: int


class WhatsAppMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    wa_message_id: str | None = None
    direction: str
    msg_type: str
    body: str | None = None
    media_id: str | None = None
    status: str | None = None
    timestamp: datetime
    classified_at: datetime | None = None
    is_important: bool | None = None
    category: str | None = None
    language: str | None = None
    summary: str | None = None


class WhatsAppMessageListResponse(BaseModel):
    items: list[WhatsAppMessageResponse] = Field(default_factory=list)
    total: int


class WhatsAppSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    message_id: int | None = None
    kind: str
    category: str | None = None
    status: str
    draft_text: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    sent_message_id: int | None = None
    contact_name: str | None = None
    wa_id: str | None = None
    message_body: str | None = None
    message_summary: str | None = None


class WhatsAppSuggestionListResponse(BaseModel):
    items: list[WhatsAppSuggestionResponse] = Field(default_factory=list)
    total: int


class WhatsAppSendResult(BaseModel):
    ok: bool
    wa_message_id: str | None = None
    message_id: int | None = None
    detail: str | None = None
