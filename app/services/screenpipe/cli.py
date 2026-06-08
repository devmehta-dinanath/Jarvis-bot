import logging
import shlex
import subprocess
import threading
from subprocess import Popen

from app.config import SCREENPIPE_CLI_COMMAND, SCREENPIPE_HEALTH_TIMEOUT_SECONDS
from app.services.screenpipe.client import wait_until_healthy

logger = logging.getLogger(__name__)

_DRAIN_THREADS: dict[int, threading.Thread] = {}


class ScreenpipeCliError(RuntimeError):
    pass


def start_screenpipe_record(
    command: str = SCREENPIPE_CLI_COMMAND,
    api_url: str | None = None,
    wait_for_api: bool = False,
) -> Popen:
    """Run `screenpipe record` as a background process."""
    args = shlex.split(command)
    if not args:
        raise ScreenpipeCliError("SCREENPIPE_CLI_COMMAND is empty")

    logger.info("Starting Screenpipe CLI: %s", command)
    process = Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
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
                    logger.info("screenpipe: %s", text)
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
