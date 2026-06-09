"""Capture frames when the X11 desktop pixels change (Linux Docker fallback).

Screenpipe's UI recorder needs accessibility permissions that Docker cannot get,
so Screenpipe falls back to ~30s idle snapshots. This watcher probes the display
with a small ffmpeg grab and saves a full frame only when the image hash changes.
"""

import hashlib
import logging
import os
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.config import (
    DEFAULT_FRAME_CAPTURE_COMMAND,
    SCREEN_CHANGE_MIN_CAPTURE_SECONDS,
    SCREEN_CHANGE_PROBE_COMMAND,
)
from app.services.screenpipe.capture import capture_single_frame

logger = logging.getLogger(__name__)


class X11ScreenChangeCapture:
    def __init__(
        self,
        *,
        probe_interval_seconds: float,
        min_capture_seconds: float,
        capture_command: str = DEFAULT_FRAME_CAPTURE_COMMAND,
        probe_command: str = SCREEN_CHANGE_PROBE_COMMAND,
    ) -> None:
        self.probe_interval_seconds = probe_interval_seconds
        self.min_capture_seconds = min_capture_seconds
        self.capture_command = capture_command
        self.probe_command = probe_command
        self._probe_path = Path("/tmp/jarvis_screen_probe.jpg")
        self._last_hash: str | None = None
        self._last_probe_at = 0.0
        self._last_capture_at = 0.0
        self._baseline_set = False

    @staticmethod
    def _display_target() -> str:
        display = os.getenv("DISPLAY", ":0")
        if display.startswith(":") and "." not in display:
            return f"{display}.0"
        return display

    def _probe_hash(self) -> str | None:
        cmd = self.probe_command.format(
            output=str(self._probe_path),
            display=self._display_target(),
        )
        try:
            subprocess.run(
                shlex.split(cmd),
                check=True,
                capture_output=True,
                timeout=10,
            )
        except Exception:
            logger.debug("[CAPTURE] X11 screen probe failed", exc_info=True)
            return None
        if not self._probe_path.is_file():
            return None
        return hashlib.md5(self._probe_path.read_bytes()).hexdigest()

    def check_and_capture(
        self,
        db: Session,
        recording: models.Recording,
        paths,
    ) -> bool:
        now = time.monotonic()
        if now - self._last_probe_at < self.probe_interval_seconds:
            return False
        self._last_probe_at = now

        current_hash = self._probe_hash()
        if current_hash is None:
            return False

        if not self._baseline_set:
            self._last_hash = current_hash
            self._baseline_set = True
            logger.info("[CAPTURE] X11 screen-change watcher ready (display=%s)", self._display_target())
            return False

        if current_hash == self._last_hash:
            return False

        if now - self._last_capture_at < self.min_capture_seconds:
            self._last_hash = current_hash
            return False

        next_index = recording.total_frames + 1
        try:
            frame_path = capture_single_frame(paths, next_index, self.capture_command)
        except Exception:
            logger.exception("[CAPTURE] X11 capture failed index=%s", next_index)
            return False

        self._last_hash = current_hash
        self._last_capture_at = now

        frame = models.Frame(
            recording_id=recording.id,
            frame_index=next_index,
            file_path=str(frame_path),
            captured_at=datetime.utcnow(),
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
        logger.info(
            "[CAPTURE] Screen changed → frame id=%s index=%s recording=%s | %s (queued for OCR)",
            frame.id,
            frame.frame_index,
            recording.id,
            frame_path.name,
        )
        return True
