import logging
import shlex
import subprocess
import threading
from collections import deque
from subprocess import Popen

from app.config import (
    MEETING_AUDIO_SYNC_ENABLED,
    SCREENPIPE_CLI_COMMAND,
    SCREENPIPE_HEALTH_TIMEOUT_SECONDS,
    SCREENPIPE_IDLE_CAPTURE_INTERVAL_MS,
    SCREENPIPE_LOG_CLI_VERBOSE,
    SCREENPIPE_MIN_CAPTURE_INTERVAL_MS,
    SCREENPIPE_VISUAL_CHANGE_THRESHOLD,
    SCREENPIPE_VISUAL_CHECK_INTERVAL_MS,
)
from app.services.screenpipe.client import wait_until_healthy

logger = logging.getLogger(__name__)

_DRAIN_THREADS: dict[int, threading.Thread] = {}
_RECENT_OUTPUT: dict[int, deque[str]] = {}
_RECENT_OUTPUT_MAX_LINES = 40

# Routine Screenpipe stderr noise (audio gaps, missing D-Bus in Docker, etc.).
_SCREENPIPE_NOISE_SUBSTRINGS = (
    "large gap on wired device",
    "screenpipe_audio::",
    "screenpipe_a11y::",
    "at-spi2",
    "tree walk failed",
    "d-bus session bus",
    "inserting ",
    "ms silence",
    "whisper_init",
    "whisper_model_load",
    "whisper_backend_init",
)

_SCREENPIPE_ALERT_SUBSTRINGS = (
    "panic",
    " panicked at ",
    "tesseract not found",
    "fatal",
    "failed to start",
    "exited with",
)


class ScreenpipeCliError(RuntimeError):
    pass


def _normalize_screenpipe_command(args: list[str]) -> list[str]:
    """Normalize CLI args to match official docs (docs.screenpipe.com).

    Official usage: `screenpipe record` — screen + audio capture on by default.
    Only pass `--disable-audio` when audio should be off. The legacy
    `record --audio-all` form is invalid and crashes with exit code 2.
    """
    if not args:
        return args

    if "--audio-all" in args:
        logger.info(
            "Removing invalid --audio-all flag; `screenpipe record` captures "
            "mic + system audio automatically (per Screenpipe docs)."
        )
        args = [arg for arg in args if arg != "--audio-all"]

    if MEETING_AUDIO_SYNC_ENABLED and "--disable-audio" in args:
        logger.info(
            "Removing --disable-audio because MEETING_AUDIO_SYNC_ENABLED=true; "
            "audio is required for meeting transcripts."
        )
        args = [arg for arg in args if arg != "--disable-audio"]

    if args[0].endswith("screenpipe") and "record" not in args:
        args.append("record")

    return args


def build_screenpipe_cli_args(command: str) -> list[str]:
    """Append visual change-detection flags when missing (needed on Linux Docker)."""
    args = _normalize_screenpipe_command(shlex.split(command))
    if not args:
        return args

    defaults: list[tuple[str, str]] = [
        ("--visual-check-interval-ms", str(SCREENPIPE_VISUAL_CHECK_INTERVAL_MS)),
        ("--min-capture-interval-ms", str(SCREENPIPE_MIN_CAPTURE_INTERVAL_MS)),
        ("--visual-change-threshold", str(SCREENPIPE_VISUAL_CHANGE_THRESHOLD)),
        ("--idle-capture-interval-ms", str(SCREENPIPE_IDLE_CAPTURE_INTERVAL_MS)),
    ]
    for flag, value in defaults:
        if flag not in args:
            args.extend([flag, value])
    return args


def start_screenpipe_record(
    command: str = SCREENPIPE_CLI_COMMAND,
    api_url: str | None = None,
    wait_for_api: bool = False,
) -> Popen:
    """Run `screenpipe record` as a background process."""
    args = build_screenpipe_cli_args(command)
    if not args:
        raise ScreenpipeCliError("SCREENPIPE_CLI_COMMAND is empty")

    effective_command = shlex.join(args)
    if effective_command != command.strip():
        logger.info(
            "Screenpipe CLI adjusted for runtime: %s (from %s)",
            effective_command,
            command.strip(),
        )
    else:
        logger.info("Starting Screenpipe CLI: %s", effective_command)
    process = Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    _RECENT_OUTPUT[process.pid] = deque(maxlen=_RECENT_OUTPUT_MAX_LINES)
    _start_output_drainer(process)
    logger.info("Screenpipe CLI pid=%s (waiting for API in background thread)", process.pid)

    if wait_for_api and api_url and not wait_until_healthy(
        api_url,
        timeout_seconds=SCREENPIPE_HEALTH_TIMEOUT_SECONDS,
        process=process,
    ):
        log_screenpipe_output(process)
        stop_screenpipe_record(process)
        raise ScreenpipeCliError(
            f"Screenpipe API did not become healthy at {api_url} within "
            f"{SCREENPIPE_HEALTH_TIMEOUT_SECONDS}s. Command: {command}"
        )

    return process


def _remember_screenpipe_line(process: Popen, text: str) -> None:
    buffer = _RECENT_OUTPUT.get(process.pid)
    if buffer is not None:
        buffer.append(text)


def recent_screenpipe_output(process: Popen | None) -> str:
    if process is None:
        return ""
    buffer = _RECENT_OUTPUT.pop(process.pid, None)
    if not buffer:
        return ""
    return "\n".join(buffer)


def _log_screenpipe_line(text: str) -> None:
    lower = text.lower()
    if any(substr in lower for substr in _SCREENPIPE_NOISE_SUBSTRINGS):
        return
    if "error" in lower or any(substr in lower for substr in _SCREENPIPE_ALERT_SUBSTRINGS):
        logger.warning("screenpipe: %s", text)
        return
    if SCREENPIPE_LOG_CLI_VERBOSE:
        logger.info("screenpipe: %s", text)
    else:
        logger.debug("screenpipe: %s", text)


def _start_output_drainer(process: Popen) -> None:
    """Read CLI stdout so a full pipe cannot block screenpipe during model downloads."""
    if process.stdout is None or process.pid in _DRAIN_THREADS:
        return

    def _drain() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                text = line.rstrip()
                if text:
                    _remember_screenpipe_line(process, text)
                    _log_screenpipe_line(text)
        except Exception:
            logger.debug("Screenpipe CLI output drainer stopped", exc_info=True)
        finally:
            _DRAIN_THREADS.pop(process.pid, None)

    thread = threading.Thread(
        target=_drain,
        name=f"screenpipe-cli-{process.pid}",
        daemon=True,
    )
    _DRAIN_THREADS[process.pid] = thread
    thread.start()


def log_screenpipe_output(process: Popen) -> None:
    if process.stdout is None:
        return
    try:
        output = process.stdout.read()
        if output.strip():
            logger.error("Screenpipe CLI output:\n%s", output.strip())
    except Exception:
        logger.exception("Could not read Screenpipe CLI output")


def stop_screenpipe_record(process: Popen | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return

    logger.info("Stopping Screenpipe CLI (pid=%s)", process.pid)
    try:
        process.terminate()
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
