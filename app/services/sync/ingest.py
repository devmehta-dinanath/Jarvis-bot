import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import models
from app.config import CHROMA_ENABLED
from app.services.sync import schemas
from app.services.vector.store import upsert_activity_chunk

logger = logging.getLogger(__name__)


def register_device(db: Session, payload: schemas.SyncRegisterRequest) -> models.SyncDevice:
    device = (
        db.query(models.SyncDevice)
        .filter(models.SyncDevice.device_id == payload.device_id)
        .first()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if device is None:
        device = models.SyncDevice(
            device_id=payload.device_id,
            hostname=payload.hostname,
            os_name=payload.os_name,
            last_seen_at=now,
        )
        db.add(device)
    else:
        device.hostname = payload.hostname or device.hostname
        device.os_name = payload.os_name or device.os_name
        device.last_seen_at = now
    db.commit()
    db.refresh(device)
    return device


def _resolve_recording(
    db: Session,
    *,
    device_id: str,
    client_recording_id: int,
) -> models.Recording | None:
    return (
        db.query(models.Recording)
        .filter(
            models.Recording.device_id == device_id,
            models.Recording.client_recording_id == client_recording_id,
        )
        .first()
    )


def ingest_recordings(
    db: Session,
    payload: schemas.SyncRecordingsRequest,
) -> list[schemas.SyncRecordingMapping]:
    mappings: list[schemas.SyncRecordingMapping] = []
    for item in payload.recordings:
        existing = _resolve_recording(
            db,
            device_id=payload.device_id,
            client_recording_id=item.client_recording_id,
        )
        if existing is not None:
            mappings.append(
                schemas.SyncRecordingMapping(
                    client_recording_id=item.client_recording_id,
                    server_recording_id=existing.id,
                )
            )
            continue
        recording = models.Recording(
            title=item.title,
            status=item.status,
            source=item.source,
            notes=item.notes,
            started_at=item.started_at,
            device_id=payload.device_id,
            client_recording_id=item.client_recording_id,
            sync_status="synced",
        )
        db.add(recording)
        db.flush()
        mappings.append(
            schemas.SyncRecordingMapping(
                client_recording_id=item.client_recording_id,
                server_recording_id=recording.id,
            )
        )
    db.commit()
    return mappings


def ingest_frames(db: Session, payload: schemas.SyncFramesRequest) -> int:
    synced = 0
    for item in payload.frames:
        recording = _resolve_recording(
            db,
            device_id=payload.device_id,
            client_recording_id=item.client_recording_id,
        )
        if recording is None:
            logger.warning(
                "Skipping frame %s — unknown client_recording_id=%s",
                item.client_frame_id,
                item.client_recording_id,
            )
            continue
        existing = (
            db.query(models.Frame)
            .filter(
                models.Frame.recording_id == recording.id,
                models.Frame.frame_index == item.frame_index,
            )
            .first()
        )
        if existing is not None:
            synced += 1
            continue
        frame = models.Frame(
            recording_id=recording.id,
            frame_index=item.frame_index,
            screenpipe_frame_id=item.screenpipe_frame_id,
            app_name=item.app_name,
            window_name=item.window_name,
            browser_url=item.browser_url,
            file_path=item.file_path,
            ocr_status=item.ocr_status,
            ocr_text=item.ocr_text,
            screenpipe_ocr_text=item.screenpipe_ocr_text,
            activity_status=item.activity_status,
            captured_at=item.captured_at,
            sync_status="synced",
        )
        db.add(frame)
        synced += 1
    db.commit()
    return synced


def ingest_activity_chunks(
    db: Session,
    payload: schemas.SyncActivityChunksRequest,
) -> tuple[int, list[int]]:
    synced = 0
    server_ids: list[int] = []
    for item in payload.chunks:
        recording = _resolve_recording(
            db,
            device_id=payload.device_id,
            client_recording_id=item.client_recording_id,
        )
        if recording is None:
            continue
        existing = (
            db.query(models.ActivityChunk)
            .join(models.Recording)
            .filter(
                models.Recording.device_id == payload.device_id,
                models.ActivityChunk.client_chunk_id == item.client_chunk_id,
            )
            .first()
        )
        if existing is not None:
            server_ids.append(existing.id)
            synced += 1
            continue
        chunk = models.ActivityChunk(
            recording_id=recording.id,
            app_name=item.app_name,
            window_name=item.window_name,
            browser_url=item.browser_url,
            category=item.category,
            timestamp=item.timestamp,
            end_timestamp=item.end_timestamp,
            cleaned_text=item.cleaned_text,
            frame_ids=item.frame_ids,
            frame_count=item.frame_count,
            transcript_status=item.transcript_status,
            transcript_text=item.transcript_text,
            client_chunk_id=item.client_chunk_id,
            sync_status="synced",
        )
        db.add(chunk)
        db.flush()
        server_ids.append(chunk.id)
        if CHROMA_ENABLED and chunk.cleaned_text:
            try:
                frame_id_list = json.loads(item.frame_ids)
            except json.JSONDecodeError:
                frame_id_list = []
            upsert_activity_chunk(
                chunk_id=chunk.id,
                recording_id=recording.id,
                cleaned_text=chunk.cleaned_text or "",
                app_name=chunk.app_name,
                window_name=chunk.window_name,
                browser_url=chunk.browser_url,
                category=chunk.category,
                timestamp=chunk.timestamp.isoformat(),
                frame_ids=frame_id_list if isinstance(frame_id_list, list) else [],
            )
        synced += 1
    db.commit()
    return synced, server_ids


def _resolve_chunk_by_client_id(
    db: Session,
    *,
    device_id: str,
    client_chunk_id: int,
) -> models.ActivityChunk | None:
    return (
        db.query(models.ActivityChunk)
        .join(models.Recording)
        .filter(
            models.Recording.device_id == device_id,
            models.ActivityChunk.client_chunk_id == client_chunk_id,
        )
        .first()
    )


def ingest_meeting_transcripts(
    db: Session,
    payload: schemas.SyncMeetingTranscriptsRequest,
) -> int:
    synced = 0
    chunk_cache: dict[int, models.ActivityChunk | None] = {}
    for item in payload.segments:
        if item.client_chunk_id not in chunk_cache:
            chunk_cache[item.client_chunk_id] = _resolve_chunk_by_client_id(
                db,
                device_id=payload.device_id,
                client_chunk_id=item.client_chunk_id,
            )
        chunk = chunk_cache[item.client_chunk_id]
        if chunk is None:
            continue
        existing = (
            db.query(models.MeetingTranscriptSegment)
            .filter(
                models.MeetingTranscriptSegment.activity_chunk_id == chunk.id,
                models.MeetingTranscriptSegment.segment_index == item.sequence,
            )
            .first()
        )
        if existing is not None:
            synced += 1
            continue
        started_at = chunk.timestamp
        if item.start_offset_seconds is not None:
            from datetime import timedelta

            started_at = chunk.timestamp + timedelta(seconds=item.start_offset_seconds)
        segment = models.MeetingTranscriptSegment(
            activity_chunk_id=chunk.id,
            recording_id=chunk.recording_id,
            segment_index=item.sequence,
            text=item.text,
            speaker=item.speaker,
            started_at=started_at,
        )
        db.add(segment)
        synced += 1
    db.commit()
    return synced
