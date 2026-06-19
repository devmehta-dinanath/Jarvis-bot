import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import CALENDAR_DEFAULT_TIMEZONE, WHATSAPP_DEFAULT_MEETING_MINUTES

_MEETING_KEYWORDS = (
    "meet",
    "meeting",
    "connect",
    "call",
    "schedule",
    "catch up",
    "catch-up",
    "available",
    "free to talk",
    "discuss",
)

_TIME_PATTERNS = (
    (re.compile(r"\btomorrow\b", re.I), 1),
    (re.compile(r"\btoday\b", re.I), 0),
    (re.compile(r"\bnext week\b", re.I), 7),
)


def _parse_time_hint(message: str, now: datetime) -> datetime | None:
    lower = message.lower()
    day_offset = 0
    for pattern, offset in _TIME_PATTERNS:
        if pattern.search(lower):
            day_offset = offset
            break

    hour = 15
    minute = 0
    match = re.search(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        elif not meridiem and 1 <= hour <= 6:
            hour += 12

    target = (now + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if day_offset == 0 and target <= now and "today" not in lower:
        target += timedelta(days=1)
    return target


def classify_message(message: str) -> dict[str, Any] | None:
    """Rule-based triage when OpenAI is unavailable."""
    text = (message or "").strip()
    if not text:
        return {
            "is_important": False,
            "category": "greeting",
            "language": None,
            "summary": None,
        }

    lower = text.lower()
    if any(keyword in lower for keyword in _MEETING_KEYWORDS):
        return {
            "is_important": True,
            "category": "meeting",
            "language": "English",
            "summary": "Client wants to schedule a meeting or call.",
        }

    return None


def extract_meeting(message: str) -> dict[str, Any]:
    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    start = _parse_time_hint(message, now)
    end = start + timedelta(minutes=WHATSAPP_DEFAULT_MEETING_MINUTES) if start else None
    return {
        "title": "Meeting with client",
        "agenda": message.strip(),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def draft_reply(category: str) -> str:
    if category == "meeting":
        return (
            "Thanks for reaching out. Happy to connect — I'll confirm the time shortly."
        )
    return "Thanks for your message. I'll get back to you shortly."
