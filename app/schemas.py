from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RecordingBase(BaseModel):
    title: str
    status: str = "queued"
    source: str = "screen"
    notes: str | None = None


class RecordingCreate(RecordingBase):
    source_video_path: str | None = None
    capture_command: str | None = None


class RecordingStartRequest(BaseModel):
    title: str
    source: str = "screen"
    notes: str | None = None
    source_video_path: str | None = None
    capture_command: str | None = None
    frame_interval_seconds: float = Field(default=1.0, gt=0)


class RecordingUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    source: str | None = None
    notes: str | None = None
    source_video_path: str | None = None
    capture_command: str | None = None
    error_message: str | None = None


class FrameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recording_id: int
    frame_index: int
    screenpipe_frame_id: int | None = None
    app_name: str | None = None
    window_name: str | None = None
    browser_url: str | None = None
    file_path: str
    ocr_status: str
    ocr_text: str | None
    screenpipe_ocr_text: str | None = None
    activity_status: str
    error_message: str | None
    created_at: datetime
    captured_at: datetime | None
    processed_at: datetime | None


class ActivityChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recording_id: int
    app_name: str | None
    window_name: str | None
    browser_url: str | None
    category: str
    timestamp: datetime
    end_timestamp: datetime | None
    cleaned_text: str | None
    transcript_status: str = "pending"
    transcript_text: str | None = None
    transcript_error: str | None = None
    frame_ids: str
    frame_count: int
    created_at: datetime


class ActivityChunkListResponse(BaseModel):
    items: list[ActivityChunkResponse] = Field(default_factory=list)
    total: int


class RecordingResponse(RecordingBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_video_path: str | None
    capture_command: str | None
    media_root: str | None
    frames_dir: str | None
    error_message: str | None
    total_frames: int
    processed_frames: int
    ocr_completed_frames: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RecordingDetailResponse(RecordingResponse):
    frames: list[FrameResponse] = Field(default_factory=list)


class VectorSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    recording_id: int | None = None
    category: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class VectorSearchHit(BaseModel):
    id: str
    document: str
    metadata: dict = Field(default_factory=dict)
    distance: float | None = None
    similarity: float | None = None


class VectorSearchResponse(BaseModel):
    enabled: bool
    count: int
    results: list[VectorSearchHit] = Field(default_factory=list)


class VectorStatsSample(BaseModel):
    id: str
    category: str | None = None
    app_name: str | None = None
    preview: str = ""


class VectorStatsResponse(BaseModel):
    enabled: bool
    ready: bool = False
    path: str
    collection: str
    count: int
    sample: list[VectorStatsSample] = Field(default_factory=list)


class VectorEmbeddingItem(BaseModel):
    id: str
    chunk_id: int | None = None
    recording_id: int | None = None
    category: str | None = None
    app_name: str | None = None
    window_name: str | None = None
    browser_url: str | None = None
    timestamp: str | None = None
    ocr_sources: str | None = None
    paddle_chars: int | None = None
    screenpipe_chars: int | None = None
    merged_chars: int | None = None
    document: str = ""
    preview: str = ""


class VectorEmbeddingListResponse(BaseModel):
    enabled: bool
    ready: bool = False
    collection: str
    total: int
    items: list[VectorEmbeddingItem] = Field(default_factory=list)


class MeetingTranscriptSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    segment_index: int
    text: str
    speaker: str | None
    started_at: datetime
    screenpipe_chunk_id: int | None


class MeetingHighlightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_chunk_id: int
    recording_id: int
    title: str
    notes: str | None
    started_at: datetime
    ended_at: datetime
    transcript_excerpt: str | None
    calendar_event_id: str | None
    calendar_html_link: str | None
    status: str
    can_add_to_calendar: bool = True
    calendar_added: bool = False
    message: str | None = None
    created_at: datetime
    updated_at: datetime


class MeetingHighlightCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    notes: str | None = None
    started_at: datetime | None = Field(
        default=None,
        description="Specific moment in the meeting (defaults to chunk start)",
    )
    ended_at: datetime | None = None
    duration_minutes: int = Field(default=30, ge=5, le=480)
    add_to_calendar: bool = Field(
        default=False,
        description="When true (user pressed Yes), create the highlight and add it to Google Calendar in one step.",
    )
    calendar_id: str | None = None
    conference: bool = False


class MeetingHighlightCalendarRequest(BaseModel):
    calendar_id: str | None = None
    conference: bool = False


class MeetingDetailResponse(BaseModel):
    chunk: ActivityChunkResponse
    transcript_segments: list[MeetingTranscriptSegmentResponse] = Field(default_factory=list)
    highlights: list[MeetingHighlightResponse] = Field(default_factory=list)
    can_add_to_calendar: bool = True


class FrameOcrSummary(BaseModel):
    id: int
    frame_index: int
    screenpipe_frame_id: int | None = None
    app_name: str | None = None
    window_name: str | None = None
    ocr_status: str
    activity_status: str
    paddle_chars: int = 0
    screenpipe_chars: int = 0
    image_on_disk: bool = False
    paddle_preview: str = ""
    screenpipe_preview: str = ""
    captured_at: datetime | None = None
    processed_at: datetime | None = None

                                                                                              
class CategoryCount(BaseModel):
    category: str
    count: int


class RecordingPipelineStatsResponse(BaseModel):
    recording_id: int
    title: str
    status: str
    database: str = "jarvis.db"
    database_path: str | None = None
    total_frames: int
    ocr_done: int
    ocr_queued: int
    ocr_processing: int
    ocr_failed: int
    activity_processed: int
    activity_pending: int
    images_on_disk: int
    activity_chunks_total: int
    activity_chunks_by_category: list[CategoryCount] = Field(default_factory=list)
    chroma_embeddings: int = 0
    recent_frames: list[FrameOcrSummary] = Field(default_factory=list)


class PipelineStatsResponse(BaseModel):
    database: str
    database_path: str | None = None
    chroma_path: str
    chroma_embeddings: int
    recordings: list[RecordingPipelineStatsResponse] = Field(default_factory=list)


class ActivitySummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_type: str
    period_start: datetime
    period_end: datetime
    status: str
    summary_text: str
    predictions_text: str | None = None
    chunk_count: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    created_at: datetime


class ActivitySummaryListResponse(BaseModel):
    items: list[ActivitySummaryResponse] = Field(default_factory=list)
    total: int


class SummaryPendingPeriod(BaseModel):
    period_start: datetime
    period_end: datetime
    unsummarized_chunk_count: int


class SummaryPendingResponse(BaseModel):
    items: list[SummaryPendingPeriod] = Field(default_factory=list)


class SummaryStatsResponse(BaseModel):
    enabled: bool
    model: str
    daily_prompt_tokens: int
    daily_completion_tokens: int
    tokens_date: str | None = None
    last_seen_date: str | None = None
    worker_running: bool = False
