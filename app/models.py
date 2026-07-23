import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="screen", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    capture_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_root: Mapped[str | None] = mapped_column(String(500), nullable=True)
    frames_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_completed_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    client_recording_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sync_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    frames: Mapped[list["Frame"]] = relationship(
        "Frame",
        back_populates="recording",
        cascade="all, delete-orphan",
        order_by="Frame.frame_index",
    )
    activity_chunks: Mapped[list["ActivityChunk"]] = relationship(
        "ActivityChunk",
        back_populates="recording",
        cascade="all, delete-orphan",
        order_by="ActivityChunk.timestamp",
    )


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), nullable=False, index=True)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    screenpipe_frame_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    app_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    window_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    browser_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenpipe_ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    activity_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)

    recording: Mapped[Recording] = relationship("Recording", back_populates="frames")


class ActivityChunk(Base):
    __tablename__ = "activity_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), nullable=False, index=True)
    app_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    window_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    browser_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    frame_ids: Mapped[str] = mapped_column(Text, nullable=False)
    frame_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    client_chunk_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sync_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    recording: Mapped[Recording] = relationship("Recording", back_populates="activity_chunks")
    transcript_segments: Mapped[list["MeetingTranscriptSegment"]] = relationship(
        "MeetingTranscriptSegment",
        back_populates="activity_chunk",
        cascade="all, delete-orphan",
        order_by="MeetingTranscriptSegment.segment_index",
    )
    highlights: Mapped[list["MeetingHighlight"]] = relationship(
        "MeetingHighlight",
        back_populates="activity_chunk",
        cascade="all, delete-orphan",
        order_by="MeetingHighlight.started_at",
    )


class MeetingTranscriptSegment(Base):
    __tablename__ = "meeting_transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    activity_chunk_id: Mapped[int] = mapped_column(
        ForeignKey("activity_chunks.id"),
        nullable=False,
        index=True,
    )
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), nullable=False, index=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    screenpipe_chunk_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    activity_chunk: Mapped[ActivityChunk] = relationship(
        "ActivityChunk",
        back_populates="transcript_segments",
    )


class MeetingHighlight(Base):
    __tablename__ = "meeting_highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    activity_chunk_id: Mapped[int] = mapped_column(
        ForeignKey("activity_chunks.id"),
        nullable=False,
        index=True,
    )
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    transcript_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_html_link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    activity_chunk: Mapped[ActivityChunk] = relationship(
        "ActivityChunk",
        back_populates="highlights",
    )


class SyncDevice(Base):
    __tablename__ = "sync_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class ActivitySummary(Base):
    __tablename__ = "activity_summaries"
    __table_args__ = (
        UniqueConstraint("period_type", "period_start", name="uq_summary_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="complete")
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    predictions_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    chunk_links: Mapped[list["SummaryChunk"]] = relationship(
        "SummaryChunk",
        back_populates="summary",
        cascade="all, delete-orphan",
    )


class SummaryChunk(Base):
    __tablename__ = "summary_chunks"
    __table_args__ = (UniqueConstraint("chunk_id", name="uq_summary_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    summary_id: Mapped[int] = mapped_column(
        ForeignKey("activity_summaries.id"),
        nullable=False,
        index=True,
    )
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("activity_chunks.id"),
        nullable=False,
        index=True,
    )

    summary: Mapped[ActivitySummary] = relationship(
        "ActivitySummary",
        back_populates="chunk_links",
    )


class SummaryState(Base):
    __tablename__ = "summary_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seen_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    daily_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class WhatsAppContact(Base):
    __tablename__ = "whatsapp_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    wa_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "personal" | "work" | None — set explicitly or auto-inferred from Life Lane messages.
    contact_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # User opted out via "Stop reading this group" or the settings toggle — no message from
    # this contact/group is sent to the classifier once set.
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    messages: Mapped[list["WhatsAppMessage"]] = relationship(
        "WhatsAppMessage",
        back_populates="contact",
        cascade="all, delete-orphan",
        order_by="WhatsAppMessage.timestamp",
    )


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_contacts.id"),
        nullable=False,
        index=True,
    )
    wa_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    msg_type: Mapped[str] = mapped_column(String(30), default="text", nullable=False)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_forwarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    classified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_important: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    translation: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    contact: Mapped["WhatsAppContact"] = relationship(
        "WhatsAppContact",
        back_populates="messages",
    )


class WhatsAppSuggestion(Base):
    __tablename__ = "whatsapp_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_contacts.id"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatsapp_messages.id"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # "work" | "life" — determines rendering lane in the UI.
    lane: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    # LLM self-reported confidence (0-100). Chips below WHATSAPP_CHIP_CONFIDENCE_MIN are not shown.
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Row exists (and can already be auto-dismissed by a reply) from creation, but is withheld
    # from the pending list until this time — null means visible immediately.
    visible_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatsapp_messages.id"),
        nullable=True,
    )

    contact: Mapped["WhatsAppContact"] = relationship("WhatsAppContact")


class WhatsAppFeedback(Base):
    """Stores every correction the user makes so the classifier can learn from them.

    feedback_type values:
      - "wrong"     — chip was shown for the wrong category / wrong reason
      - "helpful"   — chip was exactly right (positive signal)
      - "dismissed" — chip was not actioned (neutral, lower signal than "wrong")
    """

    __tablename__ = "whatsapp_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatsapp_suggestions.id"),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatsapp_contacts.id"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatsapp_messages.id"),
        nullable=True,
    )
    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    original_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    original_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # First 300 chars of the original message — stored so the LLM can learn from the exact text.
    message_snippet: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # What the user says the reply should have been — only set for feedback_type="wrong".
    # Fed back into future classification AND drafting prompts so this exact mistake is
    # never repeated for this user.
    correct_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class UserInstruction(Base):
    """A standing plain-language rule the user configured in Settings — e.g. 'Do not read
    group messages' or 'Always reply formally to clients'. Fed into the WhatsApp classifier
    and reply-drafter on every run so it is followed permanently until edited or deleted."""

    __tablename__ = "user_instructions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class AnandSandeshSubscription(Base):
    __tablename__ = "anand_sandesh_subscription"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    address_sr_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscriber_no: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    care_of: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_1: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_2: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    tehsil: Mapped[str | None] = mapped_column(Text, nullable=True)
    district: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    pincode: Mapped[str | None] = mapped_column(Text, nullable=True)
    through: Mapped[str | None] = mapped_column(Text, nullable=True)
    through_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    remarks2: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_sr_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subs_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    noofcopies: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    period: Mapped[str | None] = mapped_column(Text, nullable=True)
    whence_issued_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    whence_issued_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    upto_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upto_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscriberno: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscriberno_prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    mark: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    frommonth_byhand: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tomonth_byhand: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    payment_status: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default="pending",
        index=True,
    )
    transaction_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    razorpay_subscription_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=True,
    )
