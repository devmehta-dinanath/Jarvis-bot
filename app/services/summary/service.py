import logging
import threading
from datetime import datetime

from app.config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    SUMMARY_ENABLED,
    SUMMARY_PERIOD_MINUTES,
    SUMMARY_POLL_INTERVAL_SECONDS,
)
from app.database import SessionLocal
from app.services.summary import generator
from app.services.summary.client import SummaryOpenAIError
from app.services.summary.timezone_utils import (
    format_local_range,
    hour_start_utc_naive,
    local_today,
    naive_utc_to_local,
    utc_now,
)

logger = logging.getLogger(__name__)


class SummaryService:
    """Generates hourly/daily summaries from OCR activity chunks via OpenAI."""

    def __init__(
        self,
        poll_interval_seconds: float = SUMMARY_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hour_checked: datetime | None = None
        self._last_day_checked = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_enabled(self) -> bool:
        return SUMMARY_ENABLED and OPENAI_API_KEY is not None

    def start(self) -> None:
        if not self.is_enabled:
            if SUMMARY_ENABLED and not OPENAI_API_KEY:
                logger.warning(
                    "[SUMMARY] Disabled — set OPENAI_API_KEY in .env to enable summaries"
                )
            else:
                logger.info("[SUMMARY] Summary worker disabled (SUMMARY_ENABLED=false)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="summary-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[SUMMARY] Summary worker started (period=%s min, poll=%s s, model=%s)",
            SUMMARY_PERIOD_MINUTES,
            self.poll_interval_seconds,
            OPENAI_MODEL,
        )

    def stop(self) -> None:
        if not self.is_enabled:
            return
        self._finalize_on_shutdown()
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[SUMMARY] Summary worker stopped")

    def _finalize_on_shutdown(self) -> None:
        db = SessionLocal()
        try:
            summary = generator.finalize_current_hour(db)
            if summary:
                self._log_summary(summary, prefix="Partial hour on shutdown")
        except SummaryOpenAIError as exc:
            if exc.quota_exceeded:
                logger.error("[SUMMARY] Shutdown finalize skipped — OpenAI quota exceeded")
            else:
                logger.error("[SUMMARY] Shutdown finalize failed — OpenAI error: %s", exc)
            db.rollback()
        except Exception:
            logger.exception("[SUMMARY] Shutdown finalize failed")
            db.rollback()
        finally:
            db.close()

    def _run_loop(self) -> None:
        self._startup_catch_up()
        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                self._poll(db)
            except Exception:
                logger.exception("[SUMMARY] Poll cycle failed")
                db.rollback()
            finally:
                db.close()
            self._stop_event.wait(self.poll_interval_seconds)

    def _startup_catch_up(self) -> None:
        db = SessionLocal()
        try:
            logger.info("[SUMMARY] Startup catch-up beginning")
            summaries = generator.catch_up_past_hours(db)
            for summary in summaries:
                label = "Partial hour" if summary.status == "partial" else "Hourly"
                self._log_summary(summary, prefix=f"{label} catch-up")

            daily = generator.handle_date_change_on_startup(db)
            if daily:
                self._log_summary(daily, prefix="Previous day")

            self._last_hour_checked = hour_start_utc_naive(utc_now())
            self._last_day_checked = local_today()
            logger.info(
                "[SUMMARY] Startup catch-up complete (%s period summary(ies))",
                len(summaries),
            )
        except SummaryOpenAIError as exc:
            if exc.quota_exceeded:
                logger.error(
                    "[SUMMARY] Startup catch-up halted — OpenAI quota exceeded; "
                    "fix billing then restart"
                )
            else:
                logger.error("[SUMMARY] Startup catch-up halted — OpenAI error: %s", exc)
            db.rollback()
        except Exception:
            logger.exception("[SUMMARY] Startup catch-up failed")
            db.rollback()
        finally:
            db.close()

    def _poll(self, db) -> None:
        now = utc_now()
        current_hour = hour_start_utc_naive(now)
        today = local_today()

        if self._last_hour_checked is not None and current_hour > self._last_hour_checked:
            previous = format_local_range(
                self._last_hour_checked,
                current_hour,
            )
            logger.info(
                "[SUMMARY] Period rollover detected (%s) — finalizing previous window",
                previous,
            )
            try:
                summary = generator.process_hour_rollover(db)
            except SummaryOpenAIError as exc:
                if exc.quota_exceeded:
                    logger.error(
                        "[SUMMARY] Period rollover skipped — OpenAI quota exceeded"
                    )
                else:
                    logger.error("[SUMMARY] Period rollover failed — OpenAI error: %s", exc)
                summary = None
            else:
                if summary:
                    self._log_summary(summary, prefix="Hourly")
                else:
                    logger.info("[SUMMARY] Period rollover: nothing to summarize")

        if self._last_day_checked is not None and today > self._last_day_checked:
            logger.info("[SUMMARY] Day rollover detected — generating daily summary")
            try:
                summary = generator.process_daily_rollover(db)
            except SummaryOpenAIError as exc:
                if exc.quota_exceeded:
                    logger.error("[SUMMARY] Daily rollover skipped — OpenAI quota exceeded")
                else:
                    logger.error("[SUMMARY] Daily rollover failed — OpenAI error: %s", exc)
                summary = None
            else:
                if summary:
                    self._log_summary(summary, prefix="Daily")
                else:
                    logger.info("[SUMMARY] Daily rollover: nothing to summarize")

        self._last_hour_checked = current_hour
        self._last_day_checked = today

    def _log_summary(self, summary, *, prefix: str) -> None:
        period = format_local_range(summary.period_start, summary.period_end)
        if summary.period_type == "daily":
            day = naive_utc_to_local(summary.period_start).strftime("%Y-%m-%d")
            logger.info("[SUMMARY] %s (%s): %s", prefix, day, summary.summary_text)
        else:
            logger.info("[SUMMARY] %s %s: %s", prefix, period, summary.summary_text)
