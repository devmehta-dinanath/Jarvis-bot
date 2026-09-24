"""Step 1 Inbox scope: meeting/call chips only (client product request)."""

from __future__ import annotations

import os
import re

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

# Deterministic call/meeting asks — catch phrases the LLM may miscategorize as
# follow_up/other, or drop in groups when the owner isn't @mentioned.
_CALL_OR_MEETING_REQUEST_RE = re.compile(
    r"""
    \b(
        please\s+call\s+me
        | call\s+me(?:\s+(?:now|back|please|asap))?
        | can\s+you\s+(?:please\s+)?call(?:\s+me)?
        | could\s+you\s+(?:please\s+)?call(?:\s+me)?
        | give\s+me\s+a\s+call
        | ring\s+me
        | phone\s+me
        | can\s+we\s+(?:talk|speak|connect|meet)(?:\s+now)?
        | let'?s\s+(?:talk|speak|call|connect|meet)
        | free\s+(?:for\s+a\s+)?(?:call|chat|meet(?:ing)?)
        | schedule\s+a?\s*(?:call|meeting)
        | (?:mujhe|mere\s+ko)\s+call
        | call\s+kar(?:o|na|oge|engi)?
        | baat\s+karni\s+hai
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_call_or_meeting_request(text: str | None) -> bool:
    """True when the body is clearly asking to call / meet / talk now."""
    body = (text or "").strip()
    if not body or len(body) > 400:
        return False
    return bool(_CALL_OR_MEETING_REQUEST_RE.search(body))


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
