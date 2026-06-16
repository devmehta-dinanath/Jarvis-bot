import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app import models
from app.config import OPENAI_MODEL, SUMMARY_MAX_BATCHES_PER_HOUR, SUMMARY_PERIOD_MINUTES
from app.services.summary import client as openai_client
from app.services.summary.client import SummaryOpenAIError
from app.services.summary import repository as repo
from app.services.summary.compressor import (
    chunk_end,
    chunk_has_summarizable_text,
    chunk_batches_as_text,
)
from app.services.summary.timezone_utils import (
    day_start_utc_naive,
    format_local_range,
    hour_start_utc_naive,
    local_today,
    naive_utc_to_local,
    next_day_start_utc_naive,
    next_hour_start_utc_naive,
    utc_now,
)

logger = logging.getLogger(__name__)


def _unsummarized_chunks(db: Session) -> list[models.ActivityChunk]:
    summarized = repo.get_summarized_chunk_ids(db)
    query = db.query(models.ActivityChunk).order_by(models.ActivityChunk.timestamp.asc())
    if summarized:
        query = query.filter(~models.ActivityChunk.id.in_(summarized))
    chunks = query.all()
    return [chunk for chunk in chunks if chunk_has_summarizable_text(chunk)]


def _chunks_in_range(
    chunks: list[models.ActivityChunk],                                                            
    start: datetime,
    end: datetime,
) -> list[models.ActivityChunk]:
    return [chunk for chunk in chunks if start <= chunk.timestamp < end]


def _period_end_for_chunks(
    chunks: list[models.ActivityChunk],
    hour_end: datetime,
) -> datetime:
    if not chunks:
        return hour_end
    max_end = max(chunk_end(chunk) for chunk in chunks)
    return min(max_end, hour_end)


def generate_hourly_summary(
    db: Session,
    *,
    hour_start: datetime,
    hour_end: datetime,
    allow_partial: bool = False,
) -> models.ActivitySummary | None:
    existing = repo.get_summary_by_period(
        db, period_type="hourly", period_start=hour_start
    )
    if existing and existing.status == "complete":
        return existing

    unsummarized = _unsummarized_chunks(db)
    hour_chunks = _chunks_in_range(unsummarized, hour_start, hour_end)
    if not hour_chunks:
        return existing

    now = utc_now()
    natural_end = _period_end_for_chunks(hour_chunks, hour_end)
    is_partial = allow_partial or natural_end < hour_end or now < hour_end
    if is_partial and existing and existing.status == "partial" and not hour_chunks:
        return existing

    compressed_batches = chunk_batches_as_text(
        hour_chunks,
        max_batches=SUMMARY_MAX_BATCHES_PER_HOUR,
    )
    if not compressed_batches:
        return None

    if (
        SUMMARY_MAX_BATCHES_PER_HOUR > 0
        and len(compressed_batches) > SUMMARY_MAX_BATCHES_PER_HOUR
    ):
        logger.warning(
            "[SUMMARY] Hour %s still needs %s batches after expanding batch size (max=%s)",
            format_local_range(hour_start, natural_end if is_partial else hour_end),
            len(compressed_batches),
            SUMMARY_MAX_BATCHES_PER_HOUR,
        )

    period_label = format_local_range(hour_start, natural_end if is_partial else hour_end)
    previous_partial = existing.summary_text if existing and existing.status == "partial" else None
    status = "partial" if is_partial else "complete"

    logger.info(
        "[SUMMARY] Generating %s summary for %s (%s chunks, %s OCR batch(es))",
        status,
        period_label,
        len(hour_chunks),
        len(compressed_batches),
    )

    try:
        if len(compressed_batches) > 1:
            logger.info(
                "[SUMMARY] Period %s split into %s OCR batches (all chunks included)",
                period_label,
                len(compressed_batches),
            )
        summary_text, prompt_tokens, completion_tokens = openai_client.summarize_hourly_multi_pass(
            period_label=period_label,
            ocr_batches=compressed_batches,
            previous_partial=previous_partial,
        )
    except SummaryOpenAIError:
        raise
    except Exception:
        logger.exception("[SUMMARY] Hourly summary failed for %s", period_label)
        return None

    repo.record_token_usage(
        db, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )

    period_end = natural_end if is_partial else hour_end

    if existing:
        existing.period_end = period_end
        existing.status = status
        existing.summary_text = summary_text
        existing.chunk_count += len(hour_chunks)
        existing.prompt_tokens += prompt_tokens
        existing.completion_tokens += completion_tokens
        summary = existing
    else:
        summary = models.ActivitySummary(
            period_type="hourly",
            period_start=hour_start,
            period_end=period_end,
            status=status,
            summary_text=summary_text,
            chunk_count=len(hour_chunks),
            model=OPENAI_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        db.add(summary)
        db.flush()

    for chunk in hour_chunks:
        db.add(models.SummaryChunk(summary_id=summary.id, chunk_id=chunk.id))

    db.commit()
    db.refresh(summary)
    logger.info(
        "[SUMMARY] Saved %s summary for %s (%s chunks, tokens in=%s out=%s)",
        status,
        period_label,
        len(hour_chunks),
        prompt_tokens,
        completion_tokens,
    )
    return summary


def generate_daily_summary(
    db: Session,
    *,
    local_day: date,
) -> models.ActivitySummary | None:
    period_start = day_start_utc_naive(local_day)
    period_end = next_day_start_utc_naive(local_day)

    existing = repo.get_summary_by_period(
        db, period_type="daily", period_start=period_start
    )
    if existing:
        return existing

    hourly_summaries = (
        db.query(models.ActivitySummary)
        .filter(
            models.ActivitySummary.period_type == "hourly",
            models.ActivitySummary.period_start >= period_start,
            models.ActivitySummary.period_start < period_end,
        )
        .order_by(models.ActivitySummary.period_start.asc())
        .all()
    )
    if not hourly_summaries:
        return None

    lines = []
    for hourly in hourly_summaries:
        local_hour = naive_utc_to_local(hourly.period_start).strftime("%H:%M")
        lines.append(f"Hour {local_hour}: {hourly.summary_text}")

    day_label = local_day.isoformat()
    logger.info(
        "[SUMMARY] Generating daily summary for %s (%s hourly summaries)",
        day_label,
        len(hourly_summaries),
    )
    try:
        summary_text, prompt_tokens, completion_tokens = openai_client.summarize_daily(
            day_label=day_label,
            hourly_summaries="\n\n".join(lines),
        )
    except SummaryOpenAIError:
        raise
    except Exception:
        logger.exception("[SUMMARY] Daily summary failed for %s", day_label)
        return None

    repo.record_token_usage(
        db, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )

    summary = models.ActivitySummary(
        period_type="daily",
        period_start=period_start,
        period_end=period_end,
        status="complete",
        summary_text=summary_text,
        chunk_count=sum(h.chunk_count for h in hourly_summaries),
        model=OPENAI_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)
    logger.info(
        "[SUMMARY] Saved daily summary for %s (tokens in=%s out=%s)",
        day_label,
        prompt_tokens,
        completion_tokens,
    )
    return summary


def find_pending_hour_periods(db: Session) -> list[tuple[datetime, datetime, int]]:
    unsummarized = _unsummarized_chunks(db)
    if not unsummarized:
        return []

    by_hour: dict[datetime, list[models.ActivityChunk]] = defaultdict(list)
    for chunk in unsummarized:
        by_hour[hour_start_utc_naive(chunk.timestamp)].append(chunk)

    now = utc_now()
    pending: list[tuple[datetime, datetime, int]] = []
    for hour_start, chunks in sorted(by_hour.items()):
        hour_end = hour_start + timedelta(minutes=SUMMARY_PERIOD_MINUTES)
        if hour_start <= now:
            pending.append((hour_start, hour_end, len(chunks)))
    return pending


def catch_up_past_hours(db: Session) -> list[models.ActivitySummary]:
    now = utc_now()
    pending = find_pending_hour_periods(db)
    current_period = hour_start_utc_naive(now)

    eligible: list[tuple[datetime, datetime, int]] = []
    for hour_start, hour_end, chunk_count in pending:
        if hour_start >= current_period:
            continue
        existing = repo.get_summary_by_period(
            db, period_type="hourly", period_start=hour_start
        )
        if existing and existing.status == "complete":
            continue
        eligible.append((hour_start, hour_end, chunk_count))

    if not eligible:
        logger.info("[SUMMARY] Catch-up: no pending periods to summarize")
        return []

    logger.info(
        "[SUMMARY] Catch-up: %s pending period(s) (window=%s min, oldest=%s)",
        len(eligible),
        SUMMARY_PERIOD_MINUTES,
        format_local_range(eligible[0][0], eligible[0][1]),
    )

    created: list[models.ActivitySummary] = []
    for index, (hour_start, hour_end, chunk_count) in enumerate(eligible, start=1):
        period_label = format_local_range(hour_start, hour_end)
        logger.info(
            "[SUMMARY] Catch-up %s/%s: %s (%s unsummarized chunks)",
            index,
            len(eligible),
            period_label,
            chunk_count,
        )

        unsummarized = _unsummarized_chunks(db)
        hour_chunks = _chunks_in_range(unsummarized, hour_start, hour_end)
        if not hour_chunks:
            logger.info("[SUMMARY] Catch-up %s/%s: skipped — no chunks left", index, len(eligible))
            continue

        natural_end = _period_end_for_chunks(hour_chunks, hour_end)
        is_partial = natural_end < hour_end

        try:
            summary = generate_hourly_summary(
                db,
                hour_start=hour_start,
                hour_end=hour_end,
                allow_partial=is_partial,
            )
        except SummaryOpenAIError as exc:
            if exc.quota_exceeded:
                logger.error(
                    "[SUMMARY] Catch-up aborted at %s/%s — OpenAI quota exceeded "
                    "(%s period(s) completed before failure)",
                    index,
                    len(eligible),
                    len(created),
                )
            else:
                logger.error(
                    "[SUMMARY] Catch-up stopped at %s/%s — OpenAI error: %s",
                    index,
                    len(eligible),
                    exc,
                )
            break

        if summary:
            created.append(summary)
        else:
            logger.warning(
                "[SUMMARY] Catch-up %s/%s: no summary produced for %s",
                index,
                len(eligible),
                period_label,
            )

    logger.info(
        "[SUMMARY] Catch-up finished: %s/%s period(s) summarized",
        len(created),
        len(eligible),
    )
    return created


def finalize_current_hour(db: Session) -> models.ActivitySummary | None:
    now = utc_now()
    hour_start = hour_start_utc_naive(now)
    hour_end = next_hour_start_utc_naive(now)
    return generate_hourly_summary(
        db,
        hour_start=hour_start,
        hour_end=hour_end,
        allow_partial=True,
    )


def process_hour_rollover(db: Session) -> models.ActivitySummary | None:
    now = utc_now()
    previous_hour_start = hour_start_utc_naive(now) - timedelta(minutes=SUMMARY_PERIOD_MINUTES)
    previous_hour_end = hour_start_utc_naive(now)
    return generate_hourly_summary(
        db,
        hour_start=previous_hour_start,
        hour_end=previous_hour_end,
        allow_partial=False,
    )


def process_daily_rollover(db: Session) -> models.ActivitySummary | None:
    yesterday = local_today() - timedelta(days=1)
    return generate_daily_summary(db, local_day=yesterday)


def handle_date_change_on_startup(db: Session) -> models.ActivitySummary | None:
    state = repo.get_or_create_summary_state(db)
    today = local_today()
    daily_summary = None

    if state.last_seen_date is not None and state.last_seen_date < today:
        yesterday = today - timedelta(days=1)
        daily_summary = generate_daily_summary(db, local_day=yesterday)

    state.last_seen_date = today
    db.add(state)
    db.commit()
    return daily_summary
