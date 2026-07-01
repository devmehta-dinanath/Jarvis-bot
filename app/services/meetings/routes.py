import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.config import is_server_role
from app.database import get_db
from app.services.google_calendar.service import google_calendar_service
from app.services.activity.reclassify import reclassify_activity_chunk
from app.services.meetings.highlights import add_highlight_to_calendar, create_meeting_highlight
from app.services.meetings.transcript import sync_meeting_transcript

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


def _require_meeting_chunk(chunk: models.ActivityChunk | None) -> models.ActivityChunk:
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity chunk not found")
    if chunk.category != "meetings":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint only applies to meeting activity chunks.",
        )
    return chunk


def _highlight_response(
    highlight: models.MeetingHighlight,
    *,
    message: str | None = None,
    calendar_added: bool = False,
) -> schemas.MeetingHighlightResponse:
    data = schemas.MeetingHighlightResponse.model_validate(highlight)
    data.can_add_to_calendar = highlight.status == "draft"
    data.calendar_added = calendar_added or highlight.status == "scheduled"
    data.message = message
    if data.calendar_added and not data.message:
        data.message = "Added to Google Calendar."
    return data


@router.get("/chunks/{chunk_id}", response_model=schemas.MeetingDetailResponse)
def get_meeting_detail(chunk_id: int, db: Session = Depends(get_db)) -> schemas.MeetingDetailResponse:
    chunk = _require_meeting_chunk(crud.get_activity_chunk(db, chunk_id))
    segments = crud.list_meeting_transcript_segments(db, chunk.id)
    highlights = crud.list_meeting_highlights(db, chunk.id)
    calendar_ready = google_calendar_service.auth_status().authorized
    return schemas.MeetingDetailResponse(
        chunk=schemas.ActivityChunkResponse.model_validate(chunk),
        transcript_segments=[
            schemas.MeetingTranscriptSegmentResponse.model_validate(segment)
            for segment in segments
        ],
        highlights=[_highlight_response(item) for item in highlights],
        can_add_to_calendar=calendar_ready,
    )


@router.post(
    "/chunks/{chunk_id}/reclassify",
    response_model=schemas.ActivityChunkResponse,
)
def reclassify_chunk(chunk_id: int, db: Session = Depends(get_db)) -> models.ActivityChunk:
    chunk = crud.get_activity_chunk(db, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity chunk not found")
    return reclassify_activity_chunk(chunk, db)


@router.post(
    "/chunks/{chunk_id}/sync-transcript",
    response_model=schemas.ActivityChunkResponse,
)
def sync_chunk_transcript(
    chunk_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> models.ActivityChunk:
    if is_server_role():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Meeting transcript sync from Screenpipe runs on desktop clients. "
                "Transcripts are uploaded to the server automatically."
            ),
        )
    chunk = _require_meeting_chunk(crud.get_activity_chunk(db, chunk_id))
    return sync_meeting_transcript(chunk, db, force=force)


@router.get(
    "/chunks/{chunk_id}/highlights",
    response_model=list[schemas.MeetingHighlightResponse],
)
def list_chunk_highlights(
    chunk_id: int,
    db: Session = Depends(get_db),
) -> list[schemas.MeetingHighlightResponse]:
    _require_meeting_chunk(crud.get_activity_chunk(db, chunk_id))
    highlights = crud.list_meeting_highlights(db, chunk_id)
    return [_highlight_response(item) for item in highlights]


@router.post(
    "/chunks/{chunk_id}/highlights",
    response_model=schemas.MeetingHighlightResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_chunk_highlight(
    chunk_id: int,
    payload: schemas.MeetingHighlightCreate,
    db: Session = Depends(get_db),
) -> schemas.MeetingHighlightResponse:
    chunk = _require_meeting_chunk(crud.get_activity_chunk(db, chunk_id))
    try:
        highlight = create_meeting_highlight(
            db,
            chunk,
            title=payload.title,
            notes=payload.notes,
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            duration_minutes=payload.duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not payload.add_to_calendar:
        return _highlight_response(highlight)

    try:
        highlight = add_highlight_to_calendar(
            db,
            highlight,
            calendar_id=payload.calendar_id,
            conference=payload.conference,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"{exc} Connect Google Calendar first: "
                "GET /api/v1/calendar/auth/url"
            ),
        ) from exc
    except HttpError as exc:
        detail = google_calendar_service.google_error_message(exc)
        code = exc.resp.status if exc.resp else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=detail) from exc

    return _highlight_response(
        highlight,
        calendar_added=True,
        message="Added to Google Calendar.",
    )


@router.get(
    "/highlights/{highlight_id}",
    response_model=schemas.MeetingHighlightResponse,
)
def get_highlight(
    highlight_id: int,
    db: Session = Depends(get_db),
) -> schemas.MeetingHighlightResponse:
    highlight = crud.get_meeting_highlight(db, highlight_id)
    if highlight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Highlight not found")
    return _highlight_response(highlight)


@router.post(
    "/highlights/{highlight_id}/calendar",
    response_model=schemas.MeetingHighlightResponse,
)
def add_highlight_to_google_calendar(
    highlight_id: int,
    payload: schemas.MeetingHighlightCalendarRequest | None = None,
    db: Session = Depends(get_db),
) -> schemas.MeetingHighlightResponse:
    highlight = crud.get_meeting_highlight(db, highlight_id)
    if highlight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Highlight not found")
    if highlight.status == "scheduled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This highlight is already on Google Calendar.",
        )

    body = payload or schemas.MeetingHighlightCalendarRequest()
    try:
        highlight = add_highlight_to_calendar(
            db,
            highlight,
            calendar_id=body.calendar_id,
            conference=body.conference,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        detail = google_calendar_service.google_error_message(exc)
        code = exc.resp.status if exc.resp else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=detail) from exc

    return _highlight_response(
        highlight,
        calendar_added=True,
        message="Added to Google Calendar.",
    )
