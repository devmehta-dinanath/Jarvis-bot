"""Step 1 Inbox scope: meeting/call chips only (client product request)."""

from __future__ import annotations

import os

# Default on — Inbox shows meeting/call chips only. Set
# WHATSAPP_MEETINGS_REMINDERS_ONLY=false to restore the full action inbox.
MEETINGS_REMINDERS_ONLY = os.getenv("WHATSAPP_MEETINGS_REMINDERS_ONLY", "true").lower() in {
    "1",
    "true",
    "yes",
}

# Strict allowlist: only WhatsApp messages about scheduling a call/meeting.
MEETING_REMINDER_CATEGORIES = frozenset(
    {
        "meeting",
    }
)

MEETING_REMINDER_KINDS = frozenset(
    {
        "meeting",
    }
)

# Always keep these even in meetings-only mode (never hide critical distress).
ALWAYS_SURFACE_DETAIL_FLAGS = frozenset({"safety_concern"})


def is_meeting_or_reminder(*, category: str | None = None, kind: str | None = None) -> bool:
    if kind and kind in MEETING_REMINDER_KINDS:
        return True
    if category and category in MEETING_REMINDER_CATEGORIES:
        return True
    return False


def should_surface_chip(
    *,
    category: str | None = None,
    kind: str | None = None,
    details: dict | None = None,
) -> bool:
    """When meetings-only mode is off, everything may surface; otherwise allowlist only."""
    if not MEETINGS_REMINDERS_ONLY:
        return True
    if details:
        for flag in ALWAYS_SURFACE_DETAIL_FLAGS:
            if details.get(flag):
                return True
    return is_meeting_or_reminder(category=category, kind=kind)
