from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import APP_ROLE
from app.database import get_db
from app.services.sync import ingest, schemas
from app.services.sync.auth import require_sync_auth

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.get("/health", response_model=schemas.SyncHealthResponse)
def sync_health(_auth: None = Depends(require_sync_auth)) -> schemas.SyncHealthResponse:
    return schemas.SyncHealthResponse(status="ok", app_role=APP_ROLE)


@router.post("/register", response_model=schemas.SyncRegisterResponse)
def sync_register(
    payload: schemas.SyncRegisterRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_sync_auth),
) -> schemas.SyncRegisterResponse:
    device = ingest.register_device(db, payload)
    return schemas.SyncRegisterResponse(device_id=device.device_id, registered=True)


@router.post("/recordings", response_model=schemas.SyncRecordingsResponse)
def sync_recordings(
    payload: schemas.SyncRecordingsRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_sync_auth),
) -> schemas.SyncRecordingsResponse:
    mappings = ingest.ingest_recordings(db, payload)
    return schemas.SyncRecordingsResponse(mappings=mappings)


@router.post("/frames", response_model=schemas.SyncFramesResponse)
def sync_frames(
    payload: schemas.SyncFramesRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_sync_auth),
) -> schemas.SyncFramesResponse:
    count = ingest.ingest_frames(db, payload)
    return schemas.SyncFramesResponse(synced=count)


@router.post("/activity-chunks", response_model=schemas.SyncActivityChunksResponse)
def sync_activity_chunks(
    payload: schemas.SyncActivityChunksRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_sync_auth),
) -> schemas.SyncActivityChunksResponse:
    count, server_ids = ingest.ingest_activity_chunks(db, payload)
    return schemas.SyncActivityChunksResponse(synced=count, server_chunk_ids=server_ids)


@router.post("/meeting-transcripts", response_model=schemas.SyncMeetingTranscriptsResponse)
def sync_meeting_transcripts(
    payload: schemas.SyncMeetingTranscriptsRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_sync_auth),
) -> schemas.SyncMeetingTranscriptsResponse:
    count = ingest.ingest_meeting_transcripts(db, payload)
    return schemas.SyncMeetingTranscriptsResponse(synced=count)
