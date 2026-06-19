import logging
import threading

from app.config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    SUMMARY_ENABLED,
    SUMMARY_POLL_INTERVAL_SECONDS,
)
from app.database import SessionLocal
from app.services.summary import generator
from app.services.summary.client import SummaryOpenAIError
from app.services.summary.timezone_utils import (
    local_today,
    naive_utc_to_local,
    utc_now,
)

logger = logging.getLogger(__name__)


class SummaryService:
    """Generates daily insights and tomorrow predictions from OCR activity via OpenAI."""

    def __init__(
        self,
        poll_interval_seconds: float = SUMMARY_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
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
            "[SUMMARY] Summary worker started (daily insights, poll=%s s, model=%s)",
            self.poll_interval_seconds,
            OPENAI_MODEL,
        )

    def stop(self) -> None:
        if not self.is_enabled:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[SUMMARY] Summary worker stopped")

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
            summaries = generator.catch_up_pending_days(db)
            for summary in summaries:
                self._log_summary(summary, prefix="Daily catch-up")

            daily = generator.handle_date_change_on_startup(db)
            if daily:
                self._log_summary(daily, prefix="Previous day")

            self._last_day_checked = local_today()
            logger.info(
                "[SUMMARY] Startup catch-up complete (%s daily insight(s))",
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
        today = local_today()

        if self._last_day_checked is not None and today > self._last_day_checked:
            logger.info("[SUMMARY] Day rollover detected — generating daily insight")
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

        self._last_day_checked = today

    def _log_summary(self, summary, *, prefix: str) -> None:
        day = naive_utc_to_local(summary.period_start).strftime("%Y-%m-%d")
        logger.info("[SUMMARY] %s (%s): %s", prefix, day, summary.summary_text)
        if summary.predictions_text:
            logger.info("[SUMMARY] %s tomorrow outlook: %s", prefix, summary.predictions_text)
