from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
