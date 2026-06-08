import json
import logging
import time
from datetime import datetime, timezone
from subprocess import Popen
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.services.activity.metadata import merge_metadata
from app.services.screenpipe.auth import authorization_header, get_api_token

logger = logging.getLogger(__name__)


class ScreenpipeApiError(RuntimeError):
    pass


def wait_until_healthy(
    api_url: str,
    timeout_seconds: float = 120,
    poll_interval: float = 2,
    process: Popen | None = None,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            logger.error(
                "Screenpipe CLI exited before API was healthy (code=%s)",
                process.returncode,
            )
            return False
        if is_healthy(api_url):
            return True
        time.sleep(poll_interval)
    return False


def is_healthy(api_url: str) -> bool:
    return check_api_health(api_url)[0]


def check_api_health(api_url: str) -> tuple[bool, str | None]:
    """Return whether the Screenpipe HTTP API is up, plus a short reason when it is not."""
    try:
        payload = _request_json(f"{api_url}/health", timeout=8, auth=False)
    except HTTPError as exc:
        payload = _read_error_json(exc)
        if payload is None:
            return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)

    if not isinstance(payload, dict):
        return False, "invalid health payload"

    status = payload.get("status")
    # Screenpipe returns "degraded" (e.g. vision stale) while audio/API still work.
    if status in {"healthy", "ok", "degraded", None} or "version" in payload:
        return True, status if isinstance(status, str) else None
    return False, f"status={status!r}"


def get_health(api_url: str) -> dict[str, Any]:
    try:
        payload = _request_json(f"{api_url}/health", timeout=5, auth=False)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)}


def ensure_audio_capture(api_url: str) -> dict[str, Any]:
    """Try to enable Screenpipe audio when it is disabled."""
    health = get_health(api_url)
    audio_status = str(health.get("audio_status", "")).casefold()
    if audio_status in {"ok", "healthy"}:
        return {"enabled": True, "message": "Audio already active", "health": health}

    try:
        _ensure_api_token()
        _request_json(
            f"{api_url}/audio/start",
            timeout=10,
            method="POST",
            body=b"{}",
        )
        health = get_health(api_url)
        audio_status = str(health.get("audio_status", "")).casefold()
        if audio_status in {"ok", "healthy"}:
            return {"enabled": True, "message": "Audio capture started", "health": health}
        return {
            "enabled": False,
            "message": (
                "Audio still disabled. Ensure PulseAudio is running on the host "
                "(screenpipe needs mic/system audio access)."
            ),
            "health": health,
        }
    except Exception as exc:
        return {
            "enabled": False,
            "message": f"Could not start audio capture: {exc}",
            "health": health,
        }


def list_audio_transcripts(
    api_url: str,
    *,
    start_time: datetime,
    end_time: datetime | None = None,
    app_name: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fetch audio transcription segments from Screenpipe /search."""
    _ensure_api_token()

    end = end_time or datetime.now(timezone.utc)
    start_utc = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    params: dict[str, Any] = {
        "content_type": "audio",
        "start_time": _to_iso(start_utc),
        "end_time": _to_iso(end_utc),
        "limit": min(max(limit, 1), 20),
        "offset": max(offset, 0),
    }
    if app_name and app_name.strip():
        params["app_name"] = app_name.strip()

    payload = _request_json(
        f"{api_url}/search?{urlencode(params)}",
        timeout=30,
    )
    return _normalize_audio_list(payload)


def list_frames_since(
    api_url: str,
    since: datetime,
    limit: int = 50,
) -> list[dict[str, Any]]:
    _ensure_api_token()

    end = datetime.now(timezone.utc)
    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    params = urlencode(
        {
            "start_time": _to_iso(since_utc),
            "end_time": _to_iso(end),
            "limit": limit,
        }
    )

    search_params = urlencode(
        {
            "q": "",
            "content_type": "all",
            "start_time": _to_iso(since_utc),
            "end_time": _to_iso(end),
            "limit": limit,
        }
    )
    payload = _request_json(f"{api_url}/search?{search_params}", timeout=15)
    frames = _normalize_frame_list(payload)
    if frames:
        return frames

    try:
        payload = _request_json(f"{api_url}/frames?{params}", timeout=15)
        return _normalize_frame_list(payload)
    except HTTPError as exc:
        if exc.code not in (403, 404):
            raise
        return []


def download_frame_image(api_url: str, frame_id: int, destination: str) -> None:
    _ensure_api_token()
    headers = {"Accept": "image/jpeg,image/png,image/*,*/*", **authorization_header()}
    paths = (
        f"/frames/{frame_id}/image",
        f"/frames/{frame_id}",
        f"/frames/{frame_id}?image=true",
    )
    errors: list[str] = []
    for path in paths:
        request = Request(f"{api_url}{path}", headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read()
            if data:
                with open(destination, "wb") as file:
                    file.write(data)
                return
            errors.append(f"{path}: empty body")
        except HTTPError as exc:
            if exc.code == 403:
                raise ScreenpipeApiError(_auth_help_message()) from exc
            errors.append(f"{path}: HTTP {exc.code}")
    raise ScreenpipeApiError(
        f"Could not download frame {frame_id} ({'; '.join(errors)})"
    )


def extract_frame_id(item: dict[str, Any]) -> int | None:
    """Resolve frame id from /frames rows or /search hits (id often under content)."""
    for key in ("frame_id", "frameId", "id"):
        parsed = _parse_int(item.get(key))
        if parsed is not None:
            return parsed

    content = item.get("content")
    if isinstance(content, dict):
        parsed = extract_frame_id(content)
        if parsed is not None:
            return parsed

    frame = item.get("frame")
    if isinstance(frame, dict):
        parsed = extract_frame_id(frame)
        if parsed is not None:
            return parsed

    return None


def extract_frame_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Pull app/window/url/timestamp fields from Screenpipe API payloads."""
    sources: list[dict[str, Any]] = [item]
    for key in ("content", "frame", "metadata"):
        nested = item.get(key)
        if isinstance(nested, dict):
            sources.append(nested)

    app_name = _first_str(sources, "app_name", "appName", "application_name")
    window_name = _first_str(sources, "window_name", "windowName", "window_title", "title")
    browser_url = _first_str(sources, "browser_url", "browserUrl", "url")
    captured_at = _first_timestamp(sources, "timestamp", "created_at", "createdAt", "captured_at")
    accessibility_text = _first_str(sources, "text", "transcription", "ocr_text")

    merged = merge_metadata(
        app_name=app_name,
        window_name=window_name,
        browser_url=browser_url,
        text=accessibility_text,
    )

    return {
        "app_name": merged["app_name"],
        "window_name": merged["window_name"],
        "browser_url": merged["browser_url"],
        "captured_at": captured_at,
    }


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_str(sources: list[dict[str, Any]], *keys: str) -> str | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _first_timestamp(sources: list[dict[str, Any]], *keys: str) -> datetime | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, datetime):
                return value.replace(tzinfo=None) if value.tzinfo else value
            if isinstance(value, str) and value.strip():
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo:
                        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    return parsed
                except ValueError:
                    continue
            if isinstance(value, (int, float)):
                seconds = value / 1000 if value > 1_000_000_000_000 else value
                return datetime.utcfromtimestamp(seconds)
    return None


def _ensure_api_token() -> None:
    if not get_api_token():
        raise ScreenpipeApiError(_auth_help_message())


def _auth_help_message() -> str:
    return (
        "Screenpipe API requires a Bearer token (HTTP 403 without it). "
        "Set SCREENPIPE_API_TOKEN or SCREENPIPE_LOCAL_API_KEY in .env, "
        "or run `screenpipe auth token` and mount ~/.screenpipe into Docker."
    )


def extract_audio_segment(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a Screenpipe search hit into transcript fields."""
    sources: list[dict[str, Any]] = [item]
    content = item.get("content")
    if isinstance(content, dict):
        sources.append(content)

    text = _first_str(
        sources,
        "transcription",
        "text",
        "transcript",
        "content",
    )
    if not text:
        return None

    speaker = None
    for source in sources:
        speaker_value = source.get("speaker")
        if isinstance(speaker_value, dict):
            speaker = _first_str([speaker_value], "name", "id")
        elif isinstance(speaker_value, str) and speaker_value.strip():
            speaker = speaker_value.strip()
        if speaker:
            break

    started_at = _first_timestamp(sources, "timestamp", "created_at", "createdAt", "start_time")
    if started_at is None:
        return None

    chunk_id = None
    for source in sources:
        parsed = _parse_int(source.get("chunk_id") or source.get("chunkId") or source.get("id"))
        if parsed is not None:
            chunk_id = parsed
            break

    return {
        "text": text,
        "speaker": speaker,
        "started_at": started_at,
        "screenpipe_chunk_id": chunk_id,
    }


def _normalize_audio_list(payload: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(payload, list):
        items = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in ("data", "results", "audio"):
            value = payload.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                break

    segments: list[dict[str, Any]] = []
    for item in items:
        parsed = extract_audio_segment(item)
        if parsed is not None:
            segments.append(parsed)
    return segments


def _normalize_frame_list(payload: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(payload, list):
        items = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in ("data", "frames", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                break
    return [item for item in items if extract_frame_id(item) is not None]


def _to_iso(value: datetime) -> str:
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def _read_error_json(exc: HTTPError) -> Any:
    try:
        raw = exc.read().decode("utf-8")
    except Exception:
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _request_json(
    url: str,
    timeout: float,
    auth: bool = True,
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> Any:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if auth:
        headers.update(authorization_header())
        if "Authorization" not in headers:
            raise ScreenpipeApiError(_auth_help_message())

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        if exc.code == 403 and auth:
            raise ScreenpipeApiError(_auth_help_message()) from exc
        raise
    if not raw.strip():
        return {}
    return json.loads(raw)
