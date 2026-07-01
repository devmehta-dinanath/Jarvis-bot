from datetime import datetime

from pydantic import BaseModel, Field


class SyncRegisterRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
    hostname: str | None = None
    os_name: str | None = None


class SyncRegisterResponse(BaseModel):
    device_id: str
    registered: bool


class SyncHealthResponse(BaseModel):
    status: str
    app_role: str


class SyncRecordingItem(BaseModel):
    client_recording_id: int
    title: str
    status: str = "active"
    source: str = "screen"
    notes: str | None = None
    started_at: datetime | None = None


class SyncRecordingsRequest(BaseModel):
    device_id: str
    recordings: list[SyncRecordingItem]


class SyncRecordingMapping(BaseModel):
    client_recording_id: int
    server_recording_id: int


class SyncRecordingsResponse(BaseModel):
    mappings: list[SyncRecordingMapping]


class SyncFrameItem(BaseModel):
    client_frame_id: int
    client_recording_id: int
    frame_index: int
    screenpipe_frame_id: int | None = None
    app_name: str | None = None
    window_name: str | None = None
    browser_url: str | None = None
    file_path: str
    ocr_status: str = "done"
    ocr_text: str | None = None
    screenpipe_ocr_text: str | None = None
    activity_status: str = "pending"
    captured_at: datetime | None = None


class SyncFramesRequest(BaseModel):
    device_id: str
    frames: list[SyncFrameItem]


class SyncFramesResponse(BaseModel):
    synced: int


class SyncActivityChunkItem(BaseModel):
    client_chunk_id: int
    client_recording_id: int
    app_name: str | None = None
    window_name: str | None = None
    browser_url: str | None = None
    category: str
    timestamp: datetime
    end_timestamp: datetime | None = None
    cleaned_text: str | None = None
    frame_ids: str
    frame_count: int = 0
    transcript_status: str = "pending"
    transcript_text: str | None = None


class SyncActivityChunksRequest(BaseModel):
    device_id: str
    chunks: list[SyncActivityChunkItem]


class SyncActivityChunksResponse(BaseModel):
    synced: int
    server_chunk_ids: list[int]


class SyncTranscriptSegmentItem(BaseModel):
    client_chunk_id: int
    sequence: int
    speaker: str | None = None
    text: str
    start_offset_seconds: float | None = None
    end_offset_seconds: float | None = None


class SyncMeetingTranscriptsRequest(BaseModel):
    device_id: str
    segments: list[SyncTranscriptSegmentItem]


class SyncMeetingTranscriptsResponse(BaseModel):
    synced: int
