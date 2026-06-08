import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.bootstrap import bootstrap_database
from app.config import (
    AUTO_START_SERVICES,
    MEDIA_ROOT,
    SCREENPIPE_API_URL,
    SCREENPIPE_CLI_COMMAND,
    SCREENPIPE_ENABLED,
    SCREENPIPE_START_CLI,
)
from app.services.screenpipe.auth import get_api_token
from app.services.screenpipe.client import get_health
from app.database import get_db
from app.services import service_manager
from app.services.google_calendar.routes import router as google_calendar_router
from app.services.meetings.routes import router as meetings_router
from app.services.vector.routes import router as vector_router
from app.services.vector.store import get_vector_stats
from app.services.google_calendar.service import google_calendar_service
from app.services.pipeline import process_recording_job


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger("app.main")
    if AUTO_START_SERVICES:
        log.info(
            "Starting background services (screenpipe=%s, api=%s, token=%s)",
            SCREENPIPE_ENABLED,
            SCREENPIPE_API_URL,
            "yes" if get_api_token() else "NO — set SCREENPIPE_API_TOKEN in .env",
        )
    service_manager.start()
    yield
    service_manager.stop()


bootstrap_database()

app = FastAPI(
    title="Screenpipe Backend",
    description="Basic REST API backend for Screenpipe-style recording metadata.",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")
app.include_router(google_calendar_router)
app.include_router(meetings_router)
app.include_router(vector_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/services/status")
def services_status() -> dict:
    capture_running = service_manager.screenpipe.is_running
    ocr_running = service_manager.paddle_ocr.is_running
    hint = None
    if not AUTO_START_SERVICES:
        hint = (
            "AUTO_START_SERVICES is false — no frames are captured. "
            "Set AUTO_START_SERVICES=true and restart."
        )
    elif SCREENPIPE_ENABLED and not service_manager.screenpipe.cli_running and SCREENPIPE_START_CLI:
        hint = (
            f"Waiting for Screenpipe CLI ({SCREENPIPE_CLI_COMMAND}). "
            f"API: {SCREENPIPE_API_URL}"
        )
    elif capture_running and service_manager.screenpipe.recording_id:
        hint = (
            f"Event-driven frames from screenpipe record → "
            f"media/recording_{service_manager.screenpipe.recording_id}/frames/"
        )
    elif capture_running:
        hint = "Syncing frames from Screenpipe API; interact with your screen to trigger captures."
    return {
        "auto_start_services": AUTO_START_SERVICES,
        "screenpipe_enabled": SCREENPIPE_ENABLED,
        "screenpipe_cli_command": SCREENPIPE_CLI_COMMAND,
        "screenpipe_api_url": SCREENPIPE_API_URL,
        "screenpipe_api_token_configured": get_api_token() is not None,
        "screenpipe_start_cli": SCREENPIPE_START_CLI,
        "media_root": str(MEDIA_ROOT),
        "manager_started": service_manager.is_started,
        "screenpipe": {
            "running": capture_running,
            "cli_running": service_manager.screenpipe.cli_running,
            "live_recording_id": service_manager.screenpipe.recording_id,
            "health": get_health(SCREENPIPE_API_URL),
        },
        "paddle_ocr": {
            "running": ocr_running,
        },
        "activity": {
            "running": service_manager.activity.is_running,
        },
        "google_calendar": google_calendar_service.auth_status().model_dump(),
        "vector": get_vector_stats(),
        "hint": hint,
    }


@app.get("/api/v1/recordings", response_model=list[schemas.RecordingResponse])
def get_recordings(db: Session = Depends(get_db)) -> list[models.Recording]:
    return crud.list_recordings(db)


@app.post(
    "/api/v1/recordings",
    response_model=schemas.RecordingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recording(
    payload: schemas.RecordingCreate,
    db: Session = Depends(get_db),
) -> models.Recording:
    return crud.create_recording(db, payload)


@app.post(
    "/api/v1/recordings/start",
    response_model=schemas.RecordingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_recording_pipeline(
    payload: schemas.RecordingStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> models.Recording:
    if not payload.source_video_path and not payload.capture_command:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide source_video_path or capture_command.",
        )

    recording = crud.create_recording(
        db,
        schemas.RecordingCreate(
            title=payload.title,
            status="queued",
            source=payload.source,
            notes=payload.notes,
            source_video_path=payload.source_video_path,
            capture_command=payload.capture_command,
        ),
    )
    background_tasks.add_task(
        process_recording_job,
        recording.id,
        payload.frame_interval_seconds,
    )
    return recording


@app.get("/api/v1/recordings/{recording_id}", response_model=schemas.RecordingDetailResponse)
def get_recording(recording_id: int, db: Session = Depends(get_db)) -> models.Recording:
    recording = crud.get_recording_with_frames(db, recording_id)
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    return recording


@app.get(
    "/api/v1/recordings/{recording_id}/frames",
    response_model=list[schemas.FrameResponse],
)
def get_recording_frames(
    recording_id: int,
    db: Session = Depends(get_db),
) -> list[models.Frame]:
    recording = crud.get_recording(db, recording_id)
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    return crud.list_frames(db, recording_id)


@app.get(
    "/api/v1/activity/chunks",
    response_model=schemas.ActivityChunkListResponse,
)
def list_activity_chunks(
    recording_id: int | None = None,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> schemas.ActivityChunkListResponse:
    items, total = crud.list_activity_chunks(
        db,
        recording_id=recording_id,
        category=category,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    )
    return schemas.ActivityChunkListResponse(items=items, total=total)


@app.get(
    "/api/v1/activity/chunks/{chunk_id}",
    response_model=schemas.ActivityChunkResponse,
)
def get_activity_chunk(chunk_id: int, db: Session = Depends(get_db)) -> models.ActivityChunk:
    chunk = crud.get_activity_chunk(db, chunk_id)
    if chunk is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity chunk not found",
        )
    return chunk


@app.get(
    "/api/v1/recordings/{recording_id}/activity/chunks",
    response_model=schemas.ActivityChunkListResponse,
)
def get_recording_activity_chunks(
    recording_id: int,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> schemas.ActivityChunkListResponse:
    recording = crud.get_recording(db, recording_id)
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    items, total = crud.list_activity_chunks(
        db,
        recording_id=recording_id,
        category=category,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    )
    return schemas.ActivityChunkListResponse(items=items, total=total)


@app.post(
    "/api/v1/recordings/{recording_id}/activity/classify",
    response_model=schemas.ActivityChunkListResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def classify_recording_activity(
    recording_id: int,
    db: Session = Depends(get_db),
) -> schemas.ActivityChunkListResponse:
    from app.services.activity.processor import process_recording_activity

    recording = crud.get_recording(db, recording_id)
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    items = process_recording_activity(recording, db)
    return schemas.ActivityChunkListResponse(items=items, total=len(items))


@app.patch("/api/v1/recordings/{recording_id}", response_model=schemas.RecordingResponse)
def update_recording(
    recording_id: int,
    payload: schemas.RecordingUpdate,
    db: Session = Depends(get_db),
) -> models.Recording:
    recording = crud.get_recording(db, recording_id)
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    return crud.update_recording(db, recording, payload)


@app.delete("/api/v1/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording(recording_id: int, db: Session = Depends(get_db)) -> None:
    recording = crud.get_recording(db, recording_id)
    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    crud.delete_recording(db, recording)
