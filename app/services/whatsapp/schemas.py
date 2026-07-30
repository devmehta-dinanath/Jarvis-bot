from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    send_confirmation: bool = Field(
        default=True,
        description="Send a WhatsApp confirmation with the meeting link after scheduling",
    )


class RemindMeRequest(BaseModel):
    """Body for POST /suggestions/{id}/remind — a personal calendar reminder, independent
    of category and never requiring the other side of the conversation to confirm anything."""

    remind_at: str | None = Field(
        default=None,
        description="ISO datetime override; defaults to the message's own extracted "
        "date/time, or 24 hours from now if there isn't one",
    )
    title: str | None = Field(default=None, description="Override the reminder's calendar title")
    calendar_id: str | None = None


class ClarifyAnswerRequest(BaseModel):
    """Body for POST /suggestions/{id}/clarify — the tap option the user picked."""

    answer: str = Field(..., description="The exact tap-option text the user chose")


class TeamForwardingRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    trigger_category: str
    trigger_payment_status: str | None = None
    team_member_name: str | None = None
    team_member_wa_id: str | None = None
    is_active: bool
    created_at: datetime


class TeamForwardingRuleListResponse(BaseModel):
    items: list[TeamForwardingRuleResponse] = Field(default_factory=list)


class CreateForwardingRuleRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    trigger_category: str = Field(..., description="A WhatsApp category, e.g. 'payment', 'order'")
    trigger_payment_status: str | None = Field(
        default=None, description="Only for trigger_category='payment' — 'received' or 'overdue'"
    )
    team_member_name: str | None = None
    team_member_wa_id: str | None = Field(
        default=None, description="WhatsApp number to forward to, international format"
    )


class UpdateForwardingRuleRequest(BaseModel):
    label: str | None = None
    trigger_category: str | None = None
    trigger_payment_status: str | None = None
    team_member_name: str | None = None
    team_member_wa_id: str | None = None
    is_active: bool | None = None


class WhatsAppContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wa_id: str
    profile_name: str | None = None
    is_group: bool = False
    is_excluded: bool = False
    last_inbound_at: datetime | None = None
    last_message_at: datetime | None = None
    created_at: datetime
    within_customer_window: bool = False


class SetContactExcludedRequest(BaseModel):
    """Body for PATCH /contacts/{wa_id}/exclude — the settings toggle and the
    "Stop reading this group" button both call this."""

    excluded: bool


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
    priority: str | None = None
    language: str | None = None
    translation: str | None = None
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
    priority: str | None = None
    status: str
    lane: str | None = None
    confidence: int | None = None
    draft_text: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime
    resolved_at: datetime | None = None 
    sent_message_id: int | None = None  
    contact_name: str | None = None
    wa_id: str | None = None
    is_group: bool = False
    message_body: str | None = None
    message_summary: str | None = None
    message_translation: str | None = None
    message_language: str | None = None
    visible_after: datetime | None = None
    forward_label: str | None = None
    forwarded_to: str | None = None


class WhatsAppSuggestionListResponse(BaseModel):
    items: list[WhatsAppSuggestionResponse] = Field(default_factory=list)
    total: int


class DismissAllResponse(BaseModel):
    dismissed: int


class FeedbackRequest(BaseModel):
    """Body for POST /suggestions/{id}/feedback."""

    feedback_type: Literal["wrong", "helpful", "dismissed"] = Field(
        ...,
        description=(
            "wrong — chip shown for wrong category or wrong reason (triggers correction memory); "
            "helpful — chip was exactly right (positive signal); "
            "dismissed — chip ignored without acting on it (neutral)"
        ),
    )
    correct_response: str | None = Field(
        default=None,
        description="What the reply should have been — only meaningful when feedback_type='wrong'.",
    )


class FeedbackResponse(BaseModel):
    ok: bool
    feedback_id: int
    feedback_type: str


class WhatsAppSendResult(BaseModel):
    ok: bool
    wa_message_id: str | None = None
    message_id: int | None = None
    detail: str | None = None


class InboxStatusResponse(BaseModel):
    pending_count: int
    last_inbound_at: datetime | None = None
    last_inbound_preview: str | None = None
    last_inbound_contact_id: int | None = None


class RefreshPendingResponse(InboxStatusResponse):
    ok: bool
    reopened: int
    reclassified: int


class UserInstructionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserInstructionListResponse(BaseModel):
    items: list[UserInstructionResponse] = Field(default_factory=list)


class CreateInstructionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


class UpdateInstructionRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    is_active: bool | None = None


class WhatsAppCategoryInfo(BaseModel):
    id: str
    label: str
    lane: str
    section: str | None = None
    chip_label: str | None = None
    demo_messages: list[str] = Field(default_factory=list)


class WhatsAppSectionInfo(BaseModel):
    id: str
    title: str
    accent: str
    categories: list[str]


class WhatsAppTaxonomyResponse(BaseModel):
    sections: list[WhatsAppSectionInfo]
    categories: list[WhatsAppCategoryInfo]
    classifiable: list[str]
    filters: list[str]
