import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app import models
from app.config import OPENAI_MODEL, SUMMARY_MAX_BATCHES_PER_HOUR
from app.services.google_calendar.auth import is_authorized
from app.services.google_calendar.service import google_calendar_service
from app.services.summary import client as openai_client
from app.services.summary.client import SummaryOpenAIError
from app.services.summary import repository as repo
from app.services.summary.compressor import (
    chunk_has_summarizable_text,
    chunk_batches_as_text,
)
from app.services.summary.timezone_utils import (
    day_start_utc_naive,
    get_tz,
    local_today,
    naive_utc_to_local,
    next_day_start_utc_naive,
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


def _format_tomorrow_calendar(local_day: date) -> str | None:
    if not is_authorized():
        return None

    tomorrow = local_day + timedelta(days=1)
    tz = get_tz()
    start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)
    end = start + timedelta(days=1)

    try:
        result = google_calendar_service.list_events(
            time_min=start.isoformat(),
            time_max=end.isoformat(),
            max_results=20,
        )
    except PermissionError:
        return None
    except Exception:
        logger.warning(
            "[SUMMARY] Failed to fetch calendar for %s",
            tomorrow.isoformat(),
            exc_info=True,
        )
        return None

    items = result.get("items", [])
    if not items:
        return "No events scheduled."

    lines = []
    for event in items:
        title = event.get("summary", "(No title)")
        start_data = event.get("start", {})
        time_str = start_data.get("dateTime") or start_data.get("date", "")
        lines.append(f"- {time_str}: {title}")
    return "\n".join(lines)


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

    unsummarized = _unsummarized_chunks(db)
    day_chunks = _chunks_in_range(unsummarized, period_start, period_end)
    if not day_chunks:
        return None

    compressed_batches = chunk_batches_as_text(
        day_chunks,
        max_batches=SUMMARY_MAX_BATCHES_PER_HOUR,
    )
    if not compressed_batches:
        return None

    day_label = local_day.isoformat()
    tomorrow_label = (local_day + timedelta(days=1)).isoformat()
    period_label = day_label

    logger.info(
        "[SUMMARY] Generating daily insight for %s (%s chunks, %s OCR batch(es))",
        day_label,
        len(day_chunks),
        len(compressed_batches),
    )

    try:
        activity_digest, digest_prompt, digest_completion = (
            openai_client.summarize_activity_multi_pass(
                period_label=period_label,
                ocr_batches=compressed_batches,
            )
        )
        calendar_context = _format_tomorrow_calendar(local_day)
        summary_text, predictions_text, insight_prompt, insight_completion = (
            openai_client.summarize_daily_insight(
                day_label=day_label,
                tomorrow_label=tomorrow_label,
                activity_digest=activity_digest,
                calendar_context=calendar_context,
            )
        )
    except SummaryOpenAIError:
        raise
    except Exception:
        logger.exception("[SUMMARY] Daily insight failed for %s", day_label)
        return None

    prompt_tokens = digest_prompt + insight_prompt
    completion_tokens = digest_completion + insight_completion
    repo.record_token_usage(
        db, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )

    summary = models.ActivitySummary(
        period_type="daily",
        period_start=period_start,
        period_end=period_end,
        status="complete",
        summary_text=summary_text,
        predictions_text=predictions_text or None,
        chunk_count=len(day_chunks),
        model=OPENAI_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    db.add(summary)
    db.flush()

    for chunk in day_chunks:
        db.add(models.SummaryChunk(summary_id=summary.id, chunk_id=chunk.id))

    db.commit()
    db.refresh(summary)
    logger.info(
        "[SUMMARY] Saved daily insight for %s (%s chunks, tokens in=%s out=%s)",
        day_label,
        len(day_chunks),
        prompt_tokens,
        completion_tokens,
    )
    return summary


def find_pending_days(db: Session) -> list[tuple[date, datetime, datetime, int]]:
    unsummarized = _unsummarized_chunks(db)
    if not unsummarized:
        return []

    today = local_today()
    by_day: dict[date, list[models.ActivityChunk]] = defaultdict(list)
    for chunk in unsummarized:
        chunk_day = naive_utc_to_local(chunk.timestamp).date()
        if chunk_day < today:
            by_day[chunk_day].append(chunk)

    pending: list[tuple[date, datetime, datetime, int]] = []
    for local_day, chunks in sorted(by_day.items()):
        period_start = day_start_utc_naive(local_day)
        existing = repo.get_summary_by_period(
            db, period_type="daily", period_start=period_start
        )
        if existing:
            continue
        period_end = next_day_start_utc_naive(local_day)
        pending.append((local_day, period_start, period_end, len(chunks)))
    return pending


def catch_up_pending_days(db: Session) -> list[models.ActivitySummary]:
    pending = find_pending_days(db)
    if not pending:
        logger.info("[SUMMARY] Catch-up: no pending days to summarize")
        return []

    logger.info(
        "[SUMMARY] Catch-up: %s pending day(s) (oldest=%s)",
        len(pending),
        pending[0][0].isoformat(),
    )

    created: list[models.ActivitySummary] = []
    for index, (local_day, _, _, chunk_count) in enumerate(pending, start=1):
        logger.info(
            "[SUMMARY] Catch-up %s/%s: %s (%s unsummarized chunks)",
            index,
            len(pending),
            local_day.isoformat(),
            chunk_count,
        )
        try:
            summary = generate_daily_summary(db, local_day=local_day)
        except SummaryOpenAIError as exc:
            if exc.quota_exceeded:
                logger.error(
                    "[SUMMARY] Catch-up aborted at %s/%s — OpenAI quota exceeded "
                    "(%s day(s) completed before failure)",
                    index,
                    len(pending),
                    len(created),
                )
            else:
                logger.error(
                    "[SUMMARY] Catch-up stopped at %s/%s — OpenAI error: %s",
                    index,
                    len(pending),
                    exc,
                )
            break

        if summary:
            created.append(summary)
        else:
            logger.warning(
                "[SUMMARY] Catch-up %s/%s: no summary produced for %s",
                index,
                len(pending),
                local_day.isoformat(),
            )

    logger.info(
        "[SUMMARY] Catch-up finished: %s/%s day(s) summarized",
        len(created),
        len(pending),
    )
    return created


def process_daily_rollover(db: Session) -> models.ActivitySummary | None:
    yesterday = local_today() - timedelta(days=1)
    return generate_daily_summary(db, local_day=yesterday)


def handle_date_change_on_startup(db: Session) -> models.ActivitySummary | None:
    state = repo.get_or_create_summary_state(db)
    today = local_today()
    daily_summary = None

    if state.last_seen_date is not None and state.last_seen_date < today:
        day = state.last_seen_date
        while day < today:
            summary = generate_daily_summary(db, local_day=day)
            if summary:
                daily_summary = summary
            day += timedelta(days=1)

    state.last_seen_date = today
    db.add(state)
    db.commit()
    return daily_summary
