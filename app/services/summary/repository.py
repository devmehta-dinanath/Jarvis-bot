from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import OPENAI_MODEL
from app.services.summary.timezone_utils import (
    day_start_utc_naive,
    local_today,
    next_day_start_utc_naive,
)


def get_or_create_summary_state(db: Session) -> models.SummaryState:
    state = db.query(models.SummaryState).filter(models.SummaryState.id == 1).first()
    if state is None:
        state = models.SummaryState(id=1)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _reset_daily_tokens_if_new_day(state: models.SummaryState, today: date) -> None:
    if state.budget_date != today:
        state.budget_date = today
        state.daily_prompt_tokens = 0
        state.daily_completion_tokens = 0


def get_summarized_chunk_ids(db: Session) -> set[int]:
    rows = db.query(models.SummaryChunk.chunk_id).all()
    return {row[0] for row in rows}


def get_summary_by_period(
    db: Session,
    *,
    period_type: str,
    period_start: datetime,
) -> models.ActivitySummary | None:
    return (
        db.query(models.ActivitySummary) 
        .filter( 
            models.ActivitySummary.period_type == period_type,
            models.ActivitySummary.period_start == period_start,
        )
        .first()
    )


def list_summaries(
    db: Session,
    *,
    period_type: str | None = None,
    day: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[models.ActivitySummary], int]:
    query = db.query(models.ActivitySummary)
    if period_type is not None:
        query = query.filter(models.ActivitySummary.period_type == period_type)
    if day is not None:
        day_start = day_start_utc_naive(day)
        day_end = next_day_start_utc_naive(day)
        query = query.filter(
            models.ActivitySummary.period_start >= day_start,
            models.ActivitySummary.period_start < day_end,
        )
    total = query.count()
    items = (
        query.order_by(models.ActivitySummary.period_start.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def get_latest_summary(
    db: Session,
    *,
    period_type: str,
) -> models.ActivitySummary | None:
    return (
        db.query(models.ActivitySummary)
        .filter(models.ActivitySummary.period_type == period_type)
        .order_by(models.ActivitySummary.period_start.desc())
        .first()
    )


def record_token_usage(
    db: Session,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    state = get_or_create_summary_state(db)
    today = local_today()
    _reset_daily_tokens_if_new_day(state, today)
    state.daily_prompt_tokens += prompt_tokens
    state.daily_completion_tokens += completion_tokens
    state.budget_date = today
    db.add(state)
    db.commit()


def get_summary_stats(db: Session, *, worker_running: bool = False) -> schemas.SummaryStatsResponse:
    from app.config import OPENAI_API_KEY, SUMMARY_ENABLED

    state = get_or_create_summary_state(db)
    today = local_today()
    _reset_daily_tokens_if_new_day(state, today)
    db.commit()

    return schemas.SummaryStatsResponse(
        enabled=SUMMARY_ENABLED and OPENAI_API_KEY is not None,
        model=OPENAI_MODEL,
        daily_prompt_tokens=state.daily_prompt_tokens,
        daily_completion_tokens=state.daily_completion_tokens,
        tokens_date=state.budget_date.isoformat() if state.budget_date else None,
        last_seen_date=state.last_seen_date.isoformat() if state.last_seen_date else None,
        worker_running=worker_running,
    )


def count_unsummarized_chunks(db: Session) -> int:
    summarized = get_summarized_chunk_ids(db)
    query = db.query(func.count(models.ActivityChunk.id))
    if summarized:
        query = query.filter(~models.ActivityChunk.id.in_(summarized))
    return query.scalar() or 0
