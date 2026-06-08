import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_ENV_KEYS = (
    "SCREENPIPE_API_TOKEN",
    "SCREENPIPE_LOCAL_API_KEY",
    "SCREENPIPE_API_KEY",
)


def get_api_token() -> str | None:
    for key in _TOKEN_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value and not _is_placeholder_token(value):
            return _normalize_token(value)

    for env_path in _screenpipe_env_paths():
        token = _read_token_from_dotenv(env_path)
        if token:
            logger.info("Loaded Screenpipe API token from %s", env_path)
            return token

    token = _read_token_from_cli()
    if token:
        logger.info("Loaded Screenpipe API token via `screenpipe auth token`")
        return token

    return None


def authorization_header() -> dict[str, str]:
    token = get_api_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _is_placeholder_token(value: str) -> bool:
    lowered = value.strip().lower()
    return "your-token" in lowered or lowered in {"changeme", "replace-me", "sp-placeholder"}


def _normalize_token(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def _screenpipe_env_paths() -> list[Path]:
    data_dir = os.getenv("SCREENPIPE_DATA_DIR", "").strip()
    roots = []
    if data_dir:
        roots.append(Path(data_dir))
    roots.append(Path.home() / ".screenpipe")
    return [root / ".env" for root in roots]


def _read_token_from_dotenv(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        if key.strip() not in _TOKEN_ENV_KEYS:
            continue
        token = _normalize_token(raw_value)
        if token:
            return token
    return None


def _read_token_from_cli() -> str | None:
    try:
        result = subprocess.run(
            ["screenpipe", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return None

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("sp-") or line.startswith("sk-"):
            return _normalize_token(line)
        match = re.search(r"(sp-[a-zA-Z0-9]+)", line)
        if match:
            return match.group(1)
    return _normalize_token(output.split()[-1]) if output else None
