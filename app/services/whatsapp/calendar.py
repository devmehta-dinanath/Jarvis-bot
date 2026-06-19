import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    GOOGLE_CALENDAR_ID,
    WHATSAPP_DEFAULT_MEETING_MINUTES,
)
from app.services.google_calendar.service import google_calendar_service

logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        return value.replace(microsecond=0).isoformat() + "Z"
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def is_slot_available(
    start_dt: datetime,
    end_dt: datetime,
    *,
    calendar_id: str | None = None,
) -> bool:
    """Return True when the calendar has no overlapping busy blocks in the slot."""
    if not google_calendar_service.auth_status().authorized:
        return False

    cal_id = calendar_id or GOOGLE_CALENDAR_ID
    try:
        result = google_calendar_service.query_freebusy(
            time_min=_to_rfc3339(start_dt),
            time_max=_to_rfc3339(end_dt),
            calendar_ids=[cal_id],
        )
    except PermissionError:
        return False
    except Exception:
        logger.exception("[WHATSAPP] Calendar freebusy check failed")
        return False

    calendars = result.get("calendars") or {}
    busy_blocks = (calendars.get(cal_id) or {}).get("busy") or []
    return len(busy_blocks) == 0


def enrich_meeting_details(meeting: dict[str, Any]) -> dict[str, Any]:
    """Add availability metadata and normalize start/end on extracted meeting details."""
    enriched = dict(meeting)
    start_dt = _parse_iso(enriched.get("start"))
    if start_dt is None:
        enriched["time_available"] = None
        enriched["calendar_status"] = "no_time_specified"
        return enriched

    end_dt = _parse_iso(enriched.get("end"))
    if end_dt is None or end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=WHATSAPP_DEFAULT_MEETING_MINUTES)
        enriched["end"] = _to_rfc3339(end_dt)

    if not google_calendar_service.auth_status().authorized:
        enriched["time_available"] = None
        enriched["calendar_status"] = "calendar_not_connected"
        enriched["timezone"] = CALENDAR_DEFAULT_TIMEZONE
        return enriched

    available = is_slot_available(start_dt, end_dt)
    enriched["time_available"] = available
    enriched["calendar_status"] = "available" if available else "busy"
    enriched["timezone"] = CALENDAR_DEFAULT_TIMEZONE
    return enriched
