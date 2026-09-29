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

# Meeting / family-plan chips that need mutual agreement before Inbox / Schedule / Remind.
PLAN_CATEGORIES_REQUIRING_CONFIRMATION = frozenset({"meeting", "family_plan"})


def _details_as_dict(details) -> dict:
    if isinstance(details, dict):
        return details
    if isinstance(details, str) and details.strip():
        try:
            import json

            parsed = json.loads(details)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def is_unconfirmed_plan(suggestion_or_details, *, category: str | None = None) -> bool:
    """True when a meeting/family plan is not mutually confirmed.

    Unconfirmed plans still appear in Inbox as cards; Schedule / Remind / auto-remind
    stay blocked until confirmed=true.
    """
    if hasattr(suggestion_or_details, "category"):
        category = category or getattr(suggestion_or_details, "category", None)
        details = _details_as_dict(getattr(suggestion_or_details, "details", None))
    else:
        details = _details_as_dict(suggestion_or_details)

    if category not in PLAN_CATEGORIES_REQUIRING_CONFIRMATION:
        return False
    if details.get("safety_concern"):
        return False
    if details.get("confirmed") is True:
        return False
    if details.get("confirmed") is False:
        return True
    chip = str(details.get("chip_label") or "").strip()
    if chip.lower().startswith("unconfirmed"):
        return True
    # Missing confirmed on a plan chip → treat as unconfirmed for Schedule/Remind.
    return True


def is_confirmed_plan(suggestion_or_details, *, category: str | None = None) -> bool:
    return not is_unconfirmed_plan(suggestion_or_details, category=category)


# Deterministic call/meeting asks — catch phrases the LLM may miscategorize as
# follow_up/other, or drop in groups when the owner isn't @mentioned.
# Expand meeting-ask detection so common phrasing / typos still recover chips.
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
        | can\s+we\s+(?:talk|speak|connect|meet)\b
        | hi+\s+can\s+we\s+(?:talk|speak|connect|meet)\b
        | let'?s\s+(?:talk|speak|call|connect|meet)
        | free\s+(?:for\s+a\s+)?(?:call|chat|meet(?:ing)?)
        | schedule\s+a?\s*(?:call|meeting)
        | connect\s+(?:todat|toaday|today|tomorrow|at)\b
        | (?:mujhe|mere\s+ko)\s+call
        | call\s+kar(?:o|na|oge|engi)?
        | baat\s+karni\s+hai
    )
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
