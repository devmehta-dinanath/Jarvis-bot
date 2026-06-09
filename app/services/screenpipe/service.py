import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session
from subprocess import Popen

from app import models
from app.config import (
    DEFAULT_FRAME_CAPTURE_COMMAND,
    FRAME_INTERVAL_SECONDS,
    LIVE_RECORDING_TITLE,
    RUNNING_IN_DOCKER,
    SCREENPIPE_API_URL,
    SCREENPIPE_CLI_COMMAND,
    SCREENPIPE_ENABLED,
    SCREENPIPE_POLL_INTERVAL_SECONDS,
    SCREENPIPE_START_CLI,
    SCREEN_CHANGE_CHECK_INTERVAL_SECONDS,
    SCREEN_CHANGE_MIN_CAPTURE_SECONDS,
    SCREENPIPE_SYNC_BATCH_LIMIT,
    SCREENPIPE_SYNC_OVERLAP_SECONDS,
    use_screenpipe_frame_sync,
    use_x11_change_capture,
)
from app.database import SessionLocal
from app.services.media.storage import ensure_recording_dirs
from app.services.screenpipe.capture import capture_single_frame
from app.services.screenpipe.x11_change import X11ScreenChangeCapture
from app.services.screenpipe.cli import (
    ScreenpipeCliError,
    log_screenpipe_output,
    start_screenpipe_record,
    stop_screenpipe_record,
)
from app.services.screenpipe.auth import get_api_token
from app.services.screenpipe.client import (
    ScreenpipeApiError,
    check_api_health,
    download_frame_image,
    ensure_audio_capture,
    extract_frame_id,
    extract_frame_metadata,
    is_healthy,
    list_frames_since,
)

logger = logging.getLogger(__name__)


class ScreenpipeService:
    """
    Uses the official Screenpipe CLI (`screenpipe record`) for event-driven capture.
    New frames are pulled from the Screenpipe API when the screen changes (not on a fixed timer).
    """

    def __init__(
        self,
        api_url: str = SCREENPIPE_API_URL,
        cli_command: str = SCREENPIPE_CLI_COMMAND,
        start_cli: bool = SCREENPIPE_START_CLI,
        poll_interval_seconds: float = SCREENPIPE_POLL_INTERVAL_SECONDS,
        use_screenpipe_cli: bool = SCREENPIPE_ENABLED,
        frame_interval_seconds: float = FRAME_INTERVAL_SECONDS,
        fallback_capture_command: str = DEFAULT_FRAME_CAPTURE_COMMAND,
        live_title: str = LIVE_RECORDING_TITLE,
    ) -> None:
        self.api_url = api_url
        self.cli_command = cli_command
        self.start_cli = start_cli
        self.poll_interval_seconds = poll_interval_seconds
        self.use_screenpipe_cli = use_screenpipe_cli
        self.frame_interval_seconds = frame_interval_seconds
        self.fallback_capture_command = fallback_capture_command
        self.live_title = live_title
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cli_process: Popen | None = None
        self._recording_id: int | None = None
        self._last_poll_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        self._poll_count = 0
        self._using_external_api = False
        self._audio_bootstrap_attempted = False
        self._unhealthy_polls = 0
        self._low_capture_fps_warned = False
        self._x11_capture: X11ScreenChangeCapture | None = None
        self._x11_thread: threading.Thread | None = None
        self._sync_screenpipe_frames = use_screenpipe_frame_sync()
        self._capture_ready_logged = False

    @property
    def recording_id(self) -> int | None:
        return self._recording_id

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def cli_running(self) -> bool:
        return self._cli_process is not None and self._cli_process.poll() is None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        target = self._run_screenpipe_loop if self.use_screenpipe_cli else self._run_fallback_loop
        name = "screenpipe-sync" if self.use_screenpipe_cli else "screenpipe-capture"
        self._thread = threading.Thread(target=target, name=name, daemon=True)
        self._thread.start()
        if self.use_screenpipe_cli and use_x11_change_capture():
            self._x11_capture = X11ScreenChangeCapture(
                probe_interval_seconds=SCREEN_CHANGE_CHECK_INTERVAL_SECONDS,
                min_capture_seconds=SCREEN_CHANGE_MIN_CAPTURE_SECONDS,
                capture_command=self.fallback_capture_command,
            )
            self._x11_thread = threading.Thread(
                target=self._run_x11_change_loop,
                name="x11-screen-change",
                daemon=True,
            )
            self._x11_thread.start()
        mode = "screenpipe record + API" if self.use_screenpipe_cli else "ffmpeg interval"
        if use_x11_change_capture():
            mode += " + X11 screen-change capture"
        logger.info("Screenpipe service started (%s)", mode)

    def stop(self) -> None:
        self._stop_event.set()
        stop_screenpipe_record(self._cli_process)
        self._cli_process = None
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Screenpipe service stopped")

    def _run_screenpipe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._screenpipe_loop_once()
            except Exception:
                logger.exception("Screenpipe sync loop error")
                self._stop_event.wait(self.poll_interval_seconds)

    def _screenpipe_loop_once(self) -> None:
        if self.start_cli and self._cli_process is None and not self._using_external_api:
            use_host_api = (
                not RUNNING_IN_DOCKER
                and is_healthy(self.api_url)
                and get_api_token()
            )
            if use_host_api:
                self._using_external_api = True
                logger.info(
                    "Screenpipe API already running at %s — using it (not starting a second CLI)",
                    self.api_url,
                )
            else:
                try:
                    self._cli_process = start_screenpipe_record(
                        self.cli_command,
                        self.api_url,
                        wait_for_api=False,
                    )
                except ScreenpipeCliError:
                    logger.warning(
                        "Screenpipe CLI not ready yet (%s). Retrying in %ss…",
                        self.cli_command,
                        self.poll_interval_seconds,
                    )
                    self._stop_event.wait(self.poll_interval_seconds)
                    return

        if self._cli_process is not None and self._cli_process.poll() is not None:
            logger.error("Screenpipe CLI exited (code=%s)", self._cli_process.returncode)
            log_screenpipe_output(self._cli_process)
            self._cli_process = None
            self._stop_event.wait(self.poll_interval_seconds)
            return

        api_up, api_reason = check_api_health(self.api_url)
        if not api_up:
            self._unhealthy_polls += 1
            if self._unhealthy_polls <= 3 or self._unhealthy_polls % 30 == 0:
                logger.warning(
                    "Screenpipe API not ready at %s (%s). "
                    "First Docker start can take 5+ min while models download. Retrying…",
                    self.api_url,
                    api_reason or "no response",
                )
            self._stop_event.wait(self.poll_interval_seconds)
            return
        if self._unhealthy_polls:
            logger.info(
                "Screenpipe API is up at %s (status=%s)",
                self.api_url,
                api_reason or "ok",
            )
            self._unhealthy_polls = 0

        if not get_api_token():
            logger.warning(
                "Screenpipe API token missing. Set SCREENPIPE_API_TOKEN in .env. Retrying…"
            )
            self._stop_event.wait(self.poll_interval_seconds)
            return

        if not self._audio_bootstrap_attempted:
            self._audio_bootstrap_attempted = True
            audio_result = ensure_audio_capture(self.api_url)
            if audio_result.get("enabled"):
                logger.info("[CAPTURE] %s", audio_result.get("message"))
            else:
                logger.warning("[CAPTURE] %s", audio_result.get("message"))

        if not self._capture_ready_logged:
            self._capture_ready_logged = True
            if self._sync_screenpipe_frames:
                logger.info(
                    "[CAPTURE] Screenpipe API ready at %s — syncing frames on screen change…",
                    self.api_url,
                )
            elif use_x11_change_capture():
                logger.info(
                    "[CAPTURE] Screenpipe API ready at %s — audio/API only; "
                    "frames captured via X11 screen-change watcher",
                    self.api_url,
                )
        if self._sync_screenpipe_frames:
            self._warn_if_capture_rate_low()
            db = SessionLocal()
            try:
                self._sync_new_frames(db)
            except ScreenpipeApiError as exc:
                logger.warning("%s", exc)
                db.rollback()
            except Exception:
                logger.exception("Screenpipe frame sync failed")
                db.rollback()
            finally:
                db.close()

        self._stop_event.wait(self.poll_interval_seconds)

    def _run_x11_change_loop(self) -> None:
        assert self._x11_capture is not None
        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                recording = self._ensure_live_recording(db)
                paths = ensure_recording_dirs(recording.id)
                if self._x11_capture.check_and_capture(db, recording, paths):
                    self._recording_id = recording.id
            except Exception:
                logger.exception("[CAPTURE] X11 screen-change loop error")
                db.rollback()
            finally:
                db.close()
            self._stop_event.wait(SCREEN_CHANGE_CHECK_INTERVAL_SECONDS)

    def _warn_if_capture_rate_low(self) -> None:
        if self._low_capture_fps_warned or self._poll_count % 15 != 0:
            return
        from app.services.screenpipe.client import get_health

        health = get_health(self.api_url)
        pipeline = health.get("pipeline") if isinstance(health.get("pipeline"), dict) else {}
        fps = pipeline.get("capture_fps_actual")
        ui = health.get("ui_recorder") if isinstance(health.get("ui_recorder"), dict) else {}
        if not isinstance(fps, (int, float)) or fps >= 0.08:
            return
        self._low_capture_fps_warned = True
        logger.warning(
            "[CAPTURE] Screenpipe capture rate is low (%.3f fps). "
            "UI recorder mode=%s — using frame-diff on screen change. "
            "Tune SCREENPIPE_VISUAL_CHECK_INTERVAL_MS / SCREENPIPE_VISUAL_CHANGE_THRESHOLD if needed.",
            fps,
            ui.get("mode", "unknown"),
        )

    def _max_screenpipe_frame_id(self, db: Session, recording_id: int) -> int:
        value = (
            db.query(func.max(models.Frame.screenpipe_frame_id))
            .filter(models.Frame.recording_id == recording_id)
            .scalar()
        )
        return int(value or 0)

    def _sync_since_for_recording(self, db: Session, recording_id: int) -> datetime:
        row = (
            db.query(models.Frame.captured_at)
            .filter(
                models.Frame.recording_id == recording_id,
                models.Frame.captured_at.isnot(None),
            )
            .order_by(models.Frame.screenpipe_frame_id.desc())
            .first()
        )
        overlap = timedelta(seconds=SCREENPIPE_SYNC_OVERLAP_SECONDS)
        if row and row[0]:
            captured = row[0]
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            return captured - overlap
        return datetime.now(timezone.utc) - timedelta(minutes=5)

    def _sync_new_frames(self, db: Session) -> None:
        if not is_healthy(self.api_url):
            return

        recording = self._ensure_live_recording(db)
        paths = ensure_recording_dirs(recording.id)
        max_sp_id = self._max_screenpipe_frame_id(db, recording.id)
        since = self._sync_since_for_recording(db, recording.id)
        self._last_poll_at = datetime.now(timezone.utc)
        self._poll_count += 1

        imported = 0
        skipped_existing = 0
        batches = 0
        while True:
            remote_frames = list_frames_since(
                self.api_url,
                since,
                limit=SCREENPIPE_SYNC_BATCH_LIMIT,
            )
            remote_frames = [
                remote
                for remote in remote_frames
                if (extract_frame_id(remote) or 0) > max_sp_id
            ]
            remote_frames.sort(key=lambda item: extract_frame_id(item) or 0)
            if not remote_frames:
                break

            batches += 1
            sp_ids = [extract_frame_id(remote) for remote in remote_frames]
            sp_ids_preview = ", ".join(str(fid) for fid in sp_ids[:8] if fid is not None)
            if len(remote_frames) > 8:
                sp_ids_preview = f"{sp_ids_preview}, …" if sp_ids_preview else "…"
            logger.info(
                "[CAPTURE] Sync #%s batch %s: %s new frame(s) (screenpipe_ids=%s)",
                self._poll_count,
                batches,
                len(remote_frames),
                sp_ids_preview or "unknown",
            )

            batch_imported, batch_skipped = self._import_remote_frames(
                db,
                recording,
                paths,
                remote_frames,
            )
            imported += batch_imported
            skipped_existing += batch_skipped
            if batch_imported:
                max_sp_id = self._max_screenpipe_frame_id(db, recording.id)
            if len(remote_frames) < SCREENPIPE_SYNC_BATCH_LIMIT:
                break

        if not imported and not skipped_existing:
            logger.debug(
                "[CAPTURE] Sync #%s: no new frames (change app/window or type to trigger capture)",
                self._poll_count,
            )
            return

        if imported or skipped_existing:
            logger.info(
                "[CAPTURE] Sync #%s complete: imported=%s skipped_existing=%s | "
                "recording=%s total_frames=%s last_screenpipe_id=%s",
                self._poll_count,
                imported,
                skipped_existing,
                recording.id,
                recording.total_frames,
                max_sp_id,
            )

    def _import_remote_frames(
        self,
        db: Session,
        recording: models.Recording,
        paths,
        remote_frames: list[dict],
    ) -> tuple[int, int]:
        imported = 0
        skipped_existing = 0
        for remote in remote_frames:
            sp_frame_id = extract_frame_id(remote)
            if sp_frame_id is None:
                logger.warning(
                    "Skipping API item without frame_id (keys=%s)",
                    list(remote.keys())[:8],
                )
                continue

            exists = (
                db.query(models.Frame.id)
                .filter(models.Frame.screenpipe_frame_id == sp_frame_id)
                .first()
            )
            if exists is not None:
                skipped_existing += 1
                logger.debug(
                    "[CAPTURE] Skip duplicate screenpipe_id=%s (db frame id=%s)",
                    sp_frame_id,
                    exists[0],
                )
                continue

            next_index = recording.total_frames + 1
            frame_path = paths.frames / f"frame_{next_index:06d}.jpg"
            metadata = extract_frame_metadata(remote)
            try:
                download_frame_image(self.api_url, sp_frame_id, str(frame_path))
            except ScreenpipeApiError as exc:
                logger.error(
                    "[CAPTURE] Download failed screenpipe_id=%s index=%s recording=%s: %s",
                    sp_frame_id,
                    next_index,
                    recording.id,
                    exc,
                )
                continue

            frame = models.Frame(
                recording_id=recording.id,
                frame_index=next_index,
                screenpipe_frame_id=sp_frame_id,
                app_name=metadata["app_name"],
                window_name=metadata["window_name"],
                browser_url=metadata["browser_url"],
                captured_at=metadata["captured_at"],
                file_path=str(frame_path),
                screenpipe_ocr_text=metadata.get("ocr_text"),
                ocr_status="queued",
                activity_status="pending",
            )
            db.add(frame)
            recording.total_frames = next_index
            recording.status = "capturing"
            if recording.started_at is None:
                recording.started_at = datetime.utcnow()
            db.commit()
            db.refresh(frame)
            self._recording_id = recording.id
            imported += 1
            logger.info(
                "[CAPTURE] Saved frame id=%s index=%s screenpipe_id=%s recording=%s "
                "app=%r window=%r | %s (queued for OCR)",
                frame.id,
                frame.frame_index,
                sp_frame_id,
                recording.id,
                metadata["app_name"],
                metadata["window_name"],
                frame_path.name,
            )

        return imported, skipped_existing

    def _run_fallback_loop(self) -> None:
        """Legacy fixed-interval capture when SCREENPIPE_ENABLED=false."""
        while not self._stop_event.is_set():
            db = SessionLocal()
            try:
                recording = self._ensure_live_recording(db)
                paths = ensure_recording_dirs(recording.id)
                next_index = recording.total_frames + 1
                frame_path = capture_single_frame(paths, next_index, self.fallback_capture_command)

                recording.total_frames = next_index
                recording.status = "capturing"
                if recording.started_at is None:
                    recording.started_at = datetime.utcnow()
                db.add(
                    models.Frame(
                        recording_id=recording.id,
                        frame_index=next_index,
                        file_path=str(frame_path),
                        ocr_status="queued",
                    )
                )
                db.commit()
                self._recording_id = recording.id
            except Exception:
                logger.exception("Fallback frame capture failed")
                db.rollback()
            finally:
                db.close()

            self._stop_event.wait(self.frame_interval_seconds)

    def _ensure_live_recording(self, db: Session) -> models.Recording:
        recording = (
            db.query(models.Recording)
            .filter(
                models.Recording.title == self.live_title,
                models.Recording.status.in_(("capturing", "running_ocr", "queued")),
            )
            .order_by(models.Recording.id.desc())
            .first()
        )
        if recording is not None:
            paths = ensure_recording_dirs(recording.id)
            recording.media_root = str(paths.root)
            recording.frames_dir = str(paths.frames)
            recording.source = "screenpipe"
            if not recording.capture_command:
                recording.capture_command = self.cli_command
            return recording

        recording = models.Recording(
            title=self.live_title,
            status="capturing",
            source="screenpipe",
            capture_command=self.cli_command,
            notes="Event-driven capture via screenpipe record",
        )
        db.add(recording)
        db.commit()
        db.refresh(recording)

        paths = ensure_recording_dirs(recording.id)
        recording.media_root = str(paths.root)
        recording.frames_dir = str(paths.frames)
        recording.started_at = datetime.utcnow()
        db.commit()
        self._recording_id = recording.id
        return recording
