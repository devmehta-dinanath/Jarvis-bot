from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import schemas
from app.config import SUMMARY_PERIOD_MINUTES
from app.database import get_db
from app.services import service_manager
from app.services.summary import generator
from app.services.summary import repository as repo
from app.services.summary.timezone_utils import (
    hour_start_utc_naive,
    local_today,
    next_hour_start_utc_naive,
    utc_now,
)

router = APIRouter(prefix="/api/v1/summaries", tags=["summaries"])


@router.get("", response_model=schemas.ActivitySummaryListResponse)
def list_summaries(
    period_type: str | None = Query(default=None, pattern="^(hourly|daily)$"),
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


@router.get("/latest", response_model=schemas.ActivitySummaryResponse)
def get_latest_summary(
    period_type: str = Query(..., pattern="^(hourly|daily)$"),
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
    pending = generator.find_pending_hour_periods(db)
    items = [
        schemas.SummaryPendingPeriod(
            period_start=start,
            period_end=end,
            unsummarized_chunk_count=count,
        )
        for start, end, count in pending
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
    period_type: str = Query(..., pattern="^(hourly|daily)$"),
    period_start: datetime | None = Query(
        default=None,
        description="Hour or day start (UTC naive). Defaults to previous hour or yesterday.",
    ),
    db: Session = Depends(get_db),
) -> schemas.ActivitySummaryResponse:
    if not service_manager.summary.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Summary service disabled — set OPENAI_API_KEY and SUMMARY_ENABLED=true",
        )

    if period_type == "hourly":
        if period_start is None:
            now = utc_now()
            hour_start = hour_start_utc_naive(now) - timedelta(minutes=SUMMARY_PERIOD_MINUTES)
        else:
            hour_start = hour_start_utc_naive(period_start)
        hour_end = next_hour_start_utc_naive(hour_start)
        summary = generator.generate_hourly_summary(
            db,
            hour_start=hour_start, 
            hour_end=hour_end,
            allow_partial=False,
        )
    else:
        if period_start is None:
            local_day = local_today() - timedelta(days=1)
        else:
            local_day = period_start.date()
        summary = generator.generate_daily_summary(db, local_day=local_day)

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nothing to summarize for the requested period",
        )
    return summary
