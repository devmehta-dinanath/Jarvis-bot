import logging
import threading

import httpx
from sqlalchemy.orm import Session

from app import models
from app.config import (
    JARVIS_SERVER_URL,
    SYNC_API_KEY,
    SYNC_BATCH_LIMIT,
    SYNC_ENABLED,
    SYNC_POLL_INTERVAL_SECONDS,
    is_client_role,
)
from app.database import SessionLocal
from app.services.sync import schemas
from app.services.sync.device import device_hostname, device_os, get_or_create_device_id

logger = logging.getLogger(__name__)


class SyncUploaderService:
    """Pushes local buffer DB rows to the central server API."""

    def __init__(
        self,
        poll_interval_seconds: float = SYNC_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._device_id = get_or_create_device_id()
        self._recording_map: dict[int, int] = {}

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_enabled(self) -> bool:
        return is_client_role() and SYNC_ENABLED and bool(SYNC_API_KEY)

    def start(self) -> None:
        if not self.is_enabled:
            logger.info(
                "[SYNC] Uploader disabled (client role + SYNC_ENABLED + SYNC_API_KEY required)"
            )
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="sync-uploader",
            daemon=True,
        )
        self._thread.start()
        logger.info("[SYNC] Uploader started → %s", JARVIS_SERVER_URL)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {SYNC_API_KEY}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> httpx.Response:
        url = f"{JARVIS_SERVER_URL}{path}"
        with httpx.Client(timeout=60.0) as client:
            return client.post(url, json=payload, headers=self._headers())

    def _register(self, db: Session) -> None:
        body = schemas.SyncRegisterRequest(
            device_id=self._device_id,
            hostname=device_hostname(),
            os_name=device_os(),
        )
        response = self._post("/api/v1/sync/register", body.model_dump())
        response.raise_for_status()

    def _sync_recordings(self, db: Session) -> None:
        pending = (
            db.query(models.Recording)
            .filter(models.Recording.sync_status == "pending")
            .limit(SYNC_BATCH_LIMIT)
            .all()
        )
        if not pending:
            return
        items = [
            schemas.SyncRecordingItem(
                client_recording_id=rec.id,
                title=rec.title,
                status=rec.status,
                source=rec.source,
                notes=rec.notes,
                started_at=rec.started_at,
            )
            for rec in pending
        ]
        body = schemas.SyncRecordingsRequest(device_id=self._device_id, recordings=items)
        response = self._post("/api/v1/sync/recordings", body.model_dump(mode="json"))
        response.raise_for_status()
        data = schemas.SyncRecordingsResponse.model_validate(response.json())
        for mapping in data.mappings:
            self._recording_map[mapping.client_recording_id] = mapping.server_recording_id
            rec = db.get(models.Recording, mapping.client_recording_id)
            if rec is not None:
                rec.sync_status = "synced"
                rec.device_id = self._device_id
                rec.client_recording_id = rec.id
        db.commit()

    def _sync_frames(self, db: Session) -> None:
        pending = (
            db.query(models.Frame)
            .filter(
                models.Frame.sync_status == "pending",
                models.Frame.ocr_status == "done",
            )
            .limit(SYNC_BATCH_LIMIT)
            .all()
        )
        if not pending:
            return
        items = []
        for frame in pending:
            items.append(
                schemas.SyncFrameItem(
                    client_frame_id=frame.id,
                    client_recording_id=frame.recording_id,
                    frame_index=frame.frame_index,
                    screenpipe_frame_id=frame.screenpipe_frame_id,
                    app_name=frame.app_name,
                    window_name=frame.window_name,
                    browser_url=frame.browser_url,
                    file_path=frame.file_path,
                    ocr_status=frame.ocr_status,
                    ocr_text=frame.ocr_text,
                    screenpipe_ocr_text=frame.screenpipe_ocr_text,
                    activity_status=frame.activity_status,
                    captured_at=frame.captured_at,                                                                                          
                )
            )
        body = schemas.SyncFramesRequest(device_id=self._device_id, frames=items)
        response = self._post("/api/v1/sync/frames", body.model_dump(mode="json"))
        response.raise_for_status()
        for frame in pending:
            frame.sync_status = "synced"
        db.commit()
        logger.info("[SYNC] Uploaded %s frame(s)", len(pending))

    def _sync_chunks(self, db: Session) -> None:
        pending = (
            db.query(models.ActivityChunk)
            .filter(models.ActivityChunk.sync_status == "pending")
            .limit(SYNC_BATCH_LIMIT)
            .all()
        )
        if not pending:
            return
        items = [
            schemas.SyncActivityChunkItem(
                client_chunk_id=chunk.id,
                client_recording_id=chunk.recording_id,
                app_name=chunk.app_name,
                window_name=chunk.window_name,
                browser_url=chunk.browser_url,
                category=chunk.category,
                timestamp=chunk.timestamp,
                end_timestamp=chunk.end_timestamp,
                cleaned_text=chunk.cleaned_text,
                frame_ids=chunk.frame_ids,
                frame_count=chunk.frame_count,
                transcript_status=chunk.transcript_status,
                transcript_text=chunk.transcript_text,
            )
            for chunk in pending
        ]
        body = schemas.SyncActivityChunksRequest(device_id=self._device_id, chunks=items)
        response = self._post("/api/v1/sync/activity-chunks", body.model_dump(mode="json"))
        response.raise_for_status()
        for chunk in pending:
            chunk.sync_status = "synced"
            chunk.client_chunk_id = chunk.id
        db.commit()
        logger.info("[SYNC] Uploaded %s activity chunk(s)", len(pending))

    def _sync_transcripts(self, db: Session) -> None:
        chunks = (
            db.query(models.ActivityChunk)
            .filter(
                models.ActivityChunk.category == "meetings",
                models.ActivityChunk.transcript_status.in_(["synced", "empty"]),
                models.ActivityChunk.sync_status == "synced",
            )
            .limit(20)
            .all()
        )
        segments_payload: list[schemas.SyncTranscriptSegmentItem] = []
        for chunk in chunks:
            if chunk.transcript_text:
                segments_payload.append(
                    schemas.SyncTranscriptSegmentItem(
                        client_chunk_id=chunk.client_chunk_id or chunk.id,
                        sequence=0,
                        text=chunk.transcript_text,
                    )
                )
            db_segments = (
                db.query(models.MeetingTranscriptSegment)
                .filter(models.MeetingTranscriptSegment.activity_chunk_id == chunk.id)
                .order_by(models.MeetingTranscriptSegment.segment_index.asc())
                .all()
            )
            for seg in db_segments:
                segments_payload.append(
                    schemas.SyncTranscriptSegmentItem(
                        client_chunk_id=chunk.client_chunk_id or chunk.id,
                        sequence=seg.segment_index,
                        speaker=seg.speaker,
                        text=seg.text,
                        start_offset_seconds=None,
                    )
                )
        if not segments_payload:
            return
        body = schemas.SyncMeetingTranscriptsRequest(
            device_id=self._device_id,
            segments=segments_payload,
        )
        response = self._post(
            "/api/v1/sync/meeting-transcripts",
            body.model_dump(mode="json"),
        )
        response.raise_for_status()

    def _tick(self) -> None:
        db = SessionLocal()
        try:
            self._register(db)
            self._sync_recordings(db)
            self._sync_frames(db)
            self._sync_chunks(db)
            self._sync_transcripts(db)
        except httpx.HTTPError as exc:
            logger.warning("[SYNC] Upload failed: %s", exc)
        except Exception:
            logger.exception("[SYNC] Unexpected upload error")
        finally:
            db.close()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._tick()
            self._stop_event.wait(self.poll_interval_seconds)
