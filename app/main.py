import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"

_PIPELINE_TAGS = ("[CAPTURE]", "[OCR]", "[GROUP]", "[CLEANUP]", "[SUMMARY]")


class _PipelineFocusFilter(logging.Filter):
    """Hide noisy INFO logs; keep capture/OCR pipeline work and all warnings+."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        if record.name == "app.main":
            return True
        msg = record.getMessage()
        return any(tag in msg for tag in _PIPELINE_TAGS)


def _configure_app_logging() -> None:
    """Route app.* INFO logs to stdout. 

    Uvicorn replaces the root logger on startup, so app loggers otherwise only
    emit WARNING+ via Python's lastResort handler — hiding [CAPTURE]/[OCR]/etc.
    """
    from app.config import PIPELINE_VERBOSE_LOGS

    app_logger = logging.getLogger("app")   
    app_logger.setLevel(logging.INFO) 
    if not app_logger.handlers:
        handler = logging.StreamHandler()  
        handler.setFormatter(logging.Formatter(_LOG_FORMAT)) 
        if not PIPELINE_VERBOSE_LOGS: 
            handler.addFilter(_PipelineFocusFilter())
        app_logger.addHandler(handler) 
    app_logger.propagate = False
 

from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.bootstrap import bootstrap_database
from app.config import (
    APP_ROLE,
    AUTO_START_SERVICES,
    JARVIS_SERVER_URL,
    MEDIA_ROOT,
    SCREENPIPE_API_URL,
    SCREENPIPE_CLI_COMMAND,
    SCREENPIPE_ENABLED,
    SCREENPIPE_START_CLI,
    SYNC_ENABLED,
    WHATSAPP_ONLY_MODE,
    is_client_role,
    is_server_role,
)
from app.services.screenpipe.auth import get_api_token
from app.services.screenpipe.client import get_health
from app.database import get_db
from app.services import service_manager
from app.services.meetings.routes import router as meetings_router
from app.services.vector.store import get_vector_stats
from app.services.google_calendar.service import google_calendar_service


def _summary_status() -> dict:
    from app.database import SessionLocal
    from app.services.summary.repository import get_summary_stats

    db = SessionLocal()
    try:
        stats = get_summary_stats(db, worker_running=service_manager.summary.is_running)
        return stats.model_dump()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_app_logging()
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")
app.include_router(meetings_router)
if is_server_role():
    from app.services.google_calendar.routes import router as google_calendar_router
    from app.services.summary.routes import router as summary_router
    from app.services.vector.routes import router as vector_router
    from app.services.whatsapp.client import is_configured as wa_client_is_configured
    from app.services.whatsapp.routes import router as whatsapp_router

    app.include_router(google_calendar_router)
    app.include_router(summary_router)
    app.include_router(whatsapp_router)
    app.include_router(vector_router)
    from app.services.sync.routes import router as sync_router

    app.include_router(sync_router)
else:
    from app.services.whatsapp.client import is_configured as wa_client_is_configured


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/services/status")
def services_status() -> dict:
    hint = None
    if is_server_role():
        hint = (
            "Server role — WhatsApp, summaries, and sync ingestion API are active. "
            "Screen capture runs on desktop clients."
        )
    elif not AUTO_START_SERVICES:
        hint = (
            "AUTO_START_SERVICES is false — no frames are captured. "
            "Set AUTO_START_SERVICES=true and restart."
        )
    elif SCREENPIPE_ENABLED and not service_manager.screenpipe.cli_running and SCREENPIPE_START_CLI:
        hint = (
            f"Waiting for Screenpipe CLI ({SCREENPIPE_CLI_COMMAND}). "
            f"API: {SCREENPIPE_API_URL}"
        )
    elif service_manager.screenpipe.is_running and service_manager.screenpipe.recording_id:
        hint = (
            f"Event-driven frames from screenpipe record → "
            f"media/recording_{service_manager.screenpipe.recording_id}/frames/"
        )
    elif service_manager.screenpipe.is_running:
        hint = "Syncing frames from Screenpipe API; interact with your screen to trigger captures."
    elif is_client_role():
        hint = f"Client role — capturing locally and syncing to {JARVIS_SERVER_URL}"

    capture_running = service_manager.screenpipe.is_running if is_client_role() else False
    ocr_running = service_manager.paddle_ocr.is_running if is_client_role() else False

    return {
        "app_role": APP_ROLE,
        "jarvis_server_url": JARVIS_SERVER_URL if is_client_role() else None,
        "sync_enabled": SYNC_ENABLED if is_client_role() else None,
        "sync_uploader_running": service_manager.sync_uploader.is_running
        if is_client_role()
        else None,
        "auto_start_services": AUTO_START_SERVICES,
        "whatsapp_only_mode": WHATSAPP_ONLY_MODE,
        "screenpipe_enabled": SCREENPIPE_ENABLED if is_client_role() else False,
        "screenpipe_cli_command": SCREENPIPE_CLI_COMMAND if is_client_role() else None,
        "screenpipe_api_url": SCREENPIPE_API_URL if is_client_role() else None,
        "screenpipe_api_token_configured": get_api_token() is not None
        if is_client_role()
        else None,
        "screenpipe_start_cli": SCREENPIPE_START_CLI if is_client_role() else False,
        "media_root": str(MEDIA_ROOT),
        "manager_started": service_manager.is_started,
        "screenpipe": {
            "running": capture_running,
            "cli_running": service_manager.screenpipe.cli_running if is_client_role() else False,
            "live_recording_id": service_manager.screenpipe.recording_id
            if is_client_role()
            else None,
            "health": get_health(SCREENPIPE_API_URL) if is_client_role() else None,
        },
        "paddle_ocr": {
            "running": ocr_running,
        },
        "activity": {
            "running": service_manager.activity.is_running if is_client_role() else False,
        },
        "summary": _summary_status() if is_server_role() else {"worker_running": False},
        "whatsapp": {
            "running": service_manager.whatsapp.is_running if is_server_role() else False,
            "enabled": service_manager.whatsapp.is_enabled if is_server_role() else False,
            "configured": wa_client_is_configured() if is_server_role() else False,
        },
        "google_calendar": google_calendar_service.auth_status().model_dump()
        if is_server_role()
        else {"authorized": False},
        "vector": get_vector_stats() if is_server_role() else {"enabled": False, "count": 0},
        "hint": hint,
    }


@app.get("/api/v1/pipeline/stats", response_model=schemas.PipelineStatsResponse)
def pipeline_stats(
    recent_frames: int = 5,
    db: Session = Depends(get_db),
) -> schemas.PipelineStatsResponse:
    from app.config import CHROMA_PATH, JARVIS_DATABASE_FILENAME
    from app.database import DATABASE_URL, sqlite_database_path
    from app.services.vector.store import get_vector_stats

    vector = get_vector_stats()
    chroma_count = vector.get("count", 0)
    if DATABASE_URL.startswith("sqlite"):
        db_label = JARVIS_DATABASE_FILENAME
    elif DATABASE_URL.startswith("postgresql"):
        db_label = "postgresql"
    elif DATABASE_URL.startswith("mysql"):
        db_label = "mysql"
    else:
        db_label = "external"
    db_path = sqlite_database_path()

    recordings_stats: list[schemas.RecordingPipelineStatsResponse] = []
    for recording in crud.list_recordings(db):
        stats = crud.get_recording_pipeline_stats(
            db,
            recording.id,
            recent_frames=recent_frames,
            chroma_embeddings=chroma_count,
        )
        if stats is not None:
            recordings_stats.append(stats)

    return schemas.PipelineStatsResponse(
        database=db_label,
        database_path=str(db_path) if db_path is not None else None,
        chroma_path=str(CHROMA_PATH),
        chroma_embeddings=chroma_count,
        recordings=recordings_stats,
    ) 

 
@app.get(
    "/api/v1/recordings/{recording_id}/pipeline/stats",
    response_model=schemas.RecordingPipelineStatsResponse,
)
def recording_pipeline_stats(
    recording_id: int, 
    recent_frames: int = 5,
    db: Session = Depends(get_db), 
) -> schemas.RecordingPipelineStatsResponse:
    from app.services.vector.store import get_vector_stats 

    chroma_count = get_vector_stats().get("count", 0)
    stats = crud.get_recording_pipeline_stats(
        db,
        recording_id,
        recent_frames=recent_frames,
        chroma_embeddings=chroma_count,
    )
    if stats is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recording not found",
        )
    return stats


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
    if is_server_role():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Batch recording pipeline runs on desktop clients only. "
                "Use desktop-client-setup.sh on a machine with screen access."
            ),
        )
    if not payload.source_video_path and not payload.capture_command:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide source_video_path or capture_command.",
        )

    from app.services.pipeline import process_recording_job

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
