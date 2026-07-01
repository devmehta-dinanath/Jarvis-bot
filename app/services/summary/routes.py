from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import schemas
from app.database import get_db
from app.services import service_manager
from app.services.summary import generator
from app.services.summary import repository as repo
from app.services.summary.client import SummaryOpenAIError
from app.services.summary.timezone_utils import local_today

router = APIRouter(prefix="/api/v1/summaries", tags=["summaries"])


@router.get("", response_model=schemas.ActivitySummaryListResponse)
def list_summaries(
    period_type: str | None = Query(default="daily", pattern="^(daily|hourly)$"),
    date: date | None = Query(default=None, description="Filter by local calendar day"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> schemas.ActivitySummaryListResponse:
    items, total = repo.list_summaries(
        db,
        period_type=period_type,
        day=date,
        limit=limit,
        offset=offset,
    )
    return schemas.ActivitySummaryListResponse(items=items, total=total)


@router.get("/day/{day}", response_model=schemas.ActivitySummaryResponse)
def get_summary_for_day(
    day: date,
    period_type: str = Query(default="daily", pattern="^(daily|hourly)$"),
    db: Session = Depends(get_db),
) -> schemas.ActivitySummaryResponse:
    items, _ = repo.list_summaries(
        db,
        period_type=period_type,
        day=day,
        limit=1,
    )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {period_type} summary for {day.isoformat()}",
        )
    return items[0]


@router.get("/latest", response_model=schemas.ActivitySummaryResponse)
def get_latest_summary(
    period_type: str = Query(default="daily", pattern="^(daily|hourly)$"),
    db: Session = Depends(get_db),
) -> schemas.ActivitySummaryResponse:
    summary = repo.get_latest_summary(db, period_type=period_type)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {period_type} summary found",
        )
    return summary


@router.get("/pending", response_model=schemas.SummaryPendingResponse)
def list_pending_summaries(db: Session = Depends(get_db)) -> schemas.SummaryPendingResponse:
    pending = generator.find_pending_days(db)
    items = [
        schemas.SummaryPendingPeriod(
            period_start=period_start,
            period_end=period_end,
            unsummarized_chunk_count=count,
        )
        for _, period_start, period_end, count in pending
    ]
    return schemas.SummaryPendingResponse(items=items)


@router.get("/stats", response_model=schemas.SummaryStatsResponse)
def summary_stats(db: Session = Depends(get_db)) -> schemas.SummaryStatsResponse:
    return repo.get_summary_stats(
        db,
        worker_running=service_manager.summary.is_running,
    )


@router.post("/generate", response_model=schemas.ActivitySummaryResponse)
def generate_summary(
    day: date | None = Query(
        default=None,
        description="Local calendar day to summarize (YYYY-MM-DD)",
    ),
    period_start: datetime | None = Query(
        default=None,
        description="Day start (UTC naive). Ignored when day is set.",
    ),
    db: Session = Depends(get_db),
) -> schemas.ActivitySummaryResponse:
    if not service_manager.summary.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Summary service disabled — set OPENAI_API_KEY and SUMMARY_ENABLED=true",
        )

    if day is not None:
        local_day = day
    elif period_start is None:
        local_day = local_today() - timedelta(days=1)
    else:
        local_day = period_start.date()

    try:
        summary = generator.generate_daily_summary(db, local_day=local_day)
    except SummaryOpenAIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nothing to summarize for {local_day.isoformat()} — sync activity chunks first",
        )
    return summary


@router.post("/catch-up", response_model=schemas.ActivitySummaryListResponse)
def catch_up_summaries(db: Session = Depends(get_db)) -> schemas.ActivitySummaryListResponse:
    if not service_manager.summary.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Summary service disabled — set OPENAI_API_KEY and SUMMARY_ENABLED=true",
        )
    try:
        summaries = generator.catch_up_pending_days(db)
    except SummaryOpenAIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return schemas.ActivitySummaryListResponse(items=summaries, total=len(summaries))
