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
    """True when a meeting/family plan is not mutually confirmed — hide from Inbox."""
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
    return True


def is_confirmed_plan(suggestion_or_details, *, category: str | None = None) -> bool:
    return not is_unconfirmed_plan(suggestion_or_details, category=category)


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

_ACCEPTANCE_RE = re.compile(
    r"""
    \b(
        ok(?:ay)?
        | sure
        | yes
        | yeah
        | yep
        | perfect
        | done
        | confirmed
        | sounds\s+good
        | see\s+you
        | let'?s\s+(?:do\s+it|connect|meet|talk)
        | ok\s+sure
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TIME_HINT_RE = re.compile(
    r"""
    \b(
        \d{1,2}\s*[:.]\s*\d{2}\s*(?:am|pm)?
        | \d{1,2}\s*(?:am|pm)
        | \d{1,2}\s*(?:o'?clock)
    )\b
    |
    \b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_call_or_meeting_request(text: str | None) -> bool:
    """True when the body is clearly asking to call / meet / talk now."""
    body = (text or "").strip()
    if not body or len(body) > 400:
        return False
    return bool(_CALL_OR_MEETING_REQUEST_RE.search(body))


def _message_is_acceptance(text: str | None) -> bool:
    body = (text or "").strip()
    if not body or len(body) > 120:
        return False
    return bool(_ACCEPTANCE_RE.search(body))


def _message_has_time_or_meet_ask(text: str | None) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    return bool(_TIME_HINT_RE.search(body)) or looks_like_call_or_meeting_request(body)


def infer_mutual_meeting_confirmation(
    history: list | None,
    body: str | None,
    meeting: dict | None = None,
) -> bool:
    """One side proposed a meet/time and the other accepted → confirmed.

    Time is optional: "can we connect" + "okay" counts as confirmed intent;
    the owner then picks the clock time via Schedule.
    """
    meeting = meeting or {}
    if meeting.get("confirmed") is True:
        return True

    client_texts: list[str] = []
    owner_texts: list[str] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("body") or "").strip()
        if not text:
            continue
        if (item.get("direction") or "").lower() == "inbound":
            client_texts.append(text)
        else:
            owner_texts.append(text)
    current = (body or "").strip()
    if current:
        client_texts.append(current)

    client_proposed = any(_message_has_time_or_meet_ask(t) for t in client_texts)
    owner_proposed = any(_message_has_time_or_meet_ask(t) for t in owner_texts)
    client_accepted = any(_message_is_acceptance(t) for t in client_texts)
    owner_accepted = any(_message_is_acceptance(t) for t in owner_texts)

    if client_proposed and owner_accepted:
        return True
    if owner_proposed and client_accepted:
        return True
    return False


def latest_time_bearing_text(history: list | None, body: str | None = None) -> str | None:
    """Newest message that mentions a clock time / day (prefer over older times)."""
    ordered: list[str] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("body") or "").strip()
        if text:
            ordered.append(text)
    if (body or "").strip():
        ordered.append(body.strip())
    for text in reversed(ordered):
        if _TIME_HINT_RE.search(text):
            return text
    return None


_BARE_ACK_RE = re.compile(
    r"^(yes|yep|yeah|ok|okay|sure|haan|ha|ji|done|thanks|thank you)[.! ]*$",
    re.IGNORECASE,
)


def is_surfaceable_meeting_chip(
    *,
    category: str | None = None,
    kind: str | None = None,
    body: str | None = None,
    details: dict | None = None,
) -> bool:
    """Meetings-only Inbox: only mutually confirmed call/meet plans (plus safety)."""
    details = details or {}
    if details.get("safety_concern"):
        return True
    if not is_meeting_or_reminder(category=category, kind=kind):
        return False
    # Product rule: unconfirmed plans must not appear at all.
    if details.get("confirmed") is not True:
        return False
    text = (body or "").strip()
    if text and _BARE_ACK_RE.match(text) and not details.get("start"):
        return False
    return True


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
