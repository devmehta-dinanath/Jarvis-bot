"""Structured decision contract for one WhatsApp classification.

The classify prompt is unchanged. These fields are validated when the model
includes them, and filled from the existing category result when it does not.
A true reply_allowed value cannot override a safety or clarification block.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_MISSING = object()

_CLOSED = {
    "greeting",
    "spam",
    "forwarded",
    "instruction_skip",
    "group",
}

_APPROXIMATE_RE = re.compile(
    r"\b(?:around|about|approx(?:imately)?|late|early|mid(?:dle)?|roughly|sometime)\b",
    re.IGNORECASE,
)
_EXPLICIT_DAY_RE = re.compile(
    r"\b([1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(
    r"\b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|end of (?:the )?month|in \d+ days?|next week)\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def stated_deadline_date(message: str | None, candidate: str | None) -> str | None:
    """Keep a model date only when the message states a day or a relative phrase.

    "around late December" does not confirm the last day of that month.
    "by the 20th" can confirm that day. "by Friday" and "in 3 days" stay
    resolvable, matching the existing deadline prompt.
    """
    text = (message or "").strip()
    raw = (candidate or "").strip()
    if not _ISO_DATE_RE.fullmatch(raw):
        return None
    year_s, month_s, day_s = raw.split("-")
    year, month, day = int(year_s), int(month_s), int(day_s)
    try:
        date(year, month, day)
    except ValueError:
        return None

    explicit_days = [int(match.group(1)) for match in _EXPLICIT_DAY_RE.finditer(text)]
    if explicit_days and day not in explicit_days:
        return None
    if day in explicit_days:
        return raw
    if _APPROXIMATE_RE.search(text) and not _RELATIVE_RE.search(text):
        return None
    if not _RELATIVE_RE.search(text):
        return None
    return raw


def closed_decision(intent: str) -> dict[str, Any]:
    """A decision that must not produce a draft."""
    return {
        "intent": intent,
        "evidence": [],
        "missing_facts": [],
        "reply_allowed": False,
        "confidence": None,
        "commitment": None,
        "schema_valid": True,
    }


def build_decision(
    raw: Any,
    *,
    intent: str,
    is_important: bool,
    safety_concern: bool,
    needs_clarification: bool,
    confidence: int | None,
) -> dict[str, Any]:
    """Validate optional model fields. Missing fields keep today's behavior."""
    invalid = not isinstance(raw, dict)
    data = raw if isinstance(raw, dict) else {}

    evidence, bad_evidence = _string_list(data.get("evidence", _MISSING))
    missing_facts, bad_missing = _string_list(data.get("missing_facts", _MISSING))
    commitment, bad_commitment = _commitment(data.get("commitment", _MISSING))
    model_allowed, bad_allowed = _bool_field(data.get("reply_allowed", _MISSING))
    invalid = invalid or bad_evidence or bad_missing or bad_commitment or bad_allowed

    blocked = (
        invalid
        or safety_concern
        or needs_clarification
        or not is_important
        or intent in _CLOSED
        or bool(missing_facts)
        or model_allowed is False
    )
    return {
        "intent": intent,
        "evidence": evidence,
        "missing_facts": missing_facts,
        "reply_allowed": False if blocked else True,
        "confidence": confidence,
        "commitment": commitment,
        "schema_valid": not invalid,
    }


def _string_list(value: Any) -> tuple[list[str], bool]:
    if value is _MISSING:
        return [], False
    if not isinstance(value, list):
        return [], True
    cleaned: list[str] = []
    invalid = False
    for item in value:
        if not isinstance(item, str):
            invalid = True
            continue
        text = item.strip()
        if text:
            cleaned.append(text[:240])
    return cleaned[:8], invalid


def _bool_field(value: Any) -> tuple[bool | None, bool]:
    if value is _MISSING:
        return None, False
    if isinstance(value, bool):
        return value, False
    return None, True


def _commitment(value: Any) -> tuple[dict[str, Any] | None, bool]:
    if value is _MISSING or value is None:
        return None, False
    if not isinstance(value, dict):
        return None, True
    label = value.get("label")
    if not isinstance(label, str) or not label.strip():
        return None, True
    deadline = value.get("deadline_at")
    if deadline is not None and not isinstance(deadline, str):
        return None, True
    direction = value.get("direction", "contact")
    if direction not in ("owner", "contact"):
        return None, True
    deadline_text = deadline.strip() if isinstance(deadline, str) else None
    return {
        "label": label.strip()[:160],
        "deadline_at": deadline_text or None,
        "direction": direction,
    }, False
