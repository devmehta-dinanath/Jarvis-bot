"""Deterministic product gates for drafts, reminders, and follow-ups.

These helpers are intentionally free of DB/LLM so unit tests can pin the rules
without network or OpenAI.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Questions that need a private owner fact unless the thread already states it.
_OWNER_FACT_QUESTION_RE = re.compile(
    r"(?i)\b("
    r"when\s+will\s+(?:the\s+)?(?:stock|shipment|goods|order|delivery)\b|"
    r"(?:stock|shipment|goods|delivery).{0,40}\b(?:arriv|eta|ready|come)\b|"
    r"\b(?:eta|arrive|arrival)\b|"
    r"when\s+(?:can|will)\s+you\s+(?:send|ship|deliver)\b|"
    # Availability — inventing "yes we have stock" is as bad as inventing an ETA.
    r"(?:do\s+you\s+have|have\s+you\s+got).{0,40}\bstock\b|"
    r"is\s+(?:this|it|the\s+\w+)\s+in\s+stock\b|"
    r"\bin\s+stock\b|"
    r"stock\s+(?:available|availability|hai|hoga)\b|"
    r"(?:what|what's|whats)\s+(?:the\s+)?(?:price|rate|cost|quote)\b|"
    r"how\s+much\b|"
    r"kab\s+(?:aayega|ayega|milega|ready)|"
    r"kitna\s+(?:hai|hoga|price)"
    r")",
)

# Evidence that the thread already answered an ETA / price / stock question.
_OWNER_FACT_ANSWER_RE = re.compile(
    r"(?i)\b("
    r"arriv(?:e|es|ing|al)?\s+(?:on|by|this|next)|"
    r"stock\s+(?:will|is)\s+(?:arriv|ready|in|available)|"
    r"(?:in\s+stock|out\s+of\s+stock|available\s+(?:now|in)|not\s+available)\b|"
    r"(?:friday|monday|tuesday|wednesday|thursday|saturday|sunday|tomorrow|"
    r"next\s+week|by\s+\d)|"
    r"(?:rs\.?|inr|usd|\$|₹)\s*\d|"
    r"\d+\s*(?:rs|inr|usd|₹)|"
    r"price\s+(?:is|will\s+be)|"
    r"quote\s+(?:is|attached|sent)"
    r")",
)

_IMMEDIATE_REQUEST_RE = re.compile(
    r"(?i)\b("
    r"abhi|"
    r"right\s+now|"
    r"send\s+(?:this\s+)?now|"
    r"now\s+please|"
    r"asap|"
    r"immediately|"
    r"turant|"
    r"jaldi\s+(?:bhej|send)"
    r")\b",
)

_ACK_ONLY_RE = re.compile(
    r"(?i)^\s*(ok|okay|ok+|sure|haan|han|yes|yep|done|noted|thik|theek|👍|👌)\s*[.!]*\s*$"
)

_CLIENT_ACTION_REQUEST_RE = re.compile(
    r"(?i)\b("
    r"please\s+(?:send|share|provide|forward|mail|email)|"
    r"(?:can|could)\s+you\s+(?:send|share|provide|forward|check|call)|"
    r"send\s+(?:me|us|the|a)\b|"
    r"need\s+(?:the|a|you\s+to)\b|"
    r"share\s+(?:the|a)\b|"
    r"\bcoa\b|\binvoice\b|\bdocuments?\b|\bquote\b|"
    r"by\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow)|"
    r"kal\s+(?:bhej|bhejo|send)"
    r")"
)

_GREETING_OR_FESTIVAL_RE = re.compile(
    r"(?i)^\s*("
    r"hi|hello|hey|hola|namaste|namaskar|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"happy\s+\w+|diwali|eid|holi|christmas|new\s+year"
    r").{0,40}$"
)

# Pure small-talk — never becomes an owner→them Waiting-on follow-up.
_SMALL_TALK_RE = re.compile(
    r"(?i)^\s*("
    r"hi|hello|hey|hola|namaste|namaskar|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"how\s+are\s+you\??|"
    r"how's\s+it\s+going\??|"
    r"kaise\s+ho\??|"
    r"kya\s+haal\s+hai\??"
    r")\s*[!.]*\s*$"
)


def history_text_blob(history: list | None, extra: str | None = None) -> str:
    parts: list[str] = []
    for item in history or []:
        if isinstance(item, dict):
            parts.append(str(item.get("content") or item.get("body") or ""))
        else:
            parts.append(str(item))
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def looks_like_owner_fact_question(text: str | None) -> bool:
    body = (text or "").strip()
    if not body or len(body) > 500:
        return False
    return bool(_OWNER_FACT_QUESTION_RE.search(body))


def history_has_owner_fact_answer(history: list | None, message: str | None = None) -> bool:
    blob = history_text_blob(history)
    # Current inbound itself rarely contains the answer; search prior history only.
    return bool(_OWNER_FACT_ANSWER_RE.search(blob))


def should_clarify_missing_owner_fact(message: str | None, history: list | None) -> bool:
    """True when the inbound asks for ETA/price/etc. and the thread does not already say it."""
    if not looks_like_owner_fact_question(message):
        return False
    return not history_has_owner_fact_answer(history, message)


def is_greeting_or_festival(text: str | None) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    return bool(_GREETING_OR_FESTIVAL_RE.match(body))


def is_bare_ack(text: str | None) -> bool:
    return bool(_ACK_ONLY_RE.match((text or "").strip()))


def is_small_talk_message(text: str | None) -> bool:
    """Hi / How are you? / Good morning — not worth a 48h Waiting-on follow-up."""
    body = (text or "").strip()
    if not body:
        return False
    if is_greeting_or_festival(body) or is_bare_ack(body):
        return True
    return bool(_SMALL_TALK_RE.match(body))


def last_inbound_is_action_request(text: str | None) -> bool:
    """True when the client's last message asks the owner to do something.

    Used so owner 'Ok' only becomes a Reminder after a real request — not after
    small talk like 'How are you?'.
    """
    body = (text or "").strip()
    if not body:
        return False
    if is_greeting_or_festival(body) or is_bare_ack(body):
        return False
    # Pure social check-ins are not action requests.
    if re.match(r"(?i)^\s*(how\s+are\s+you|how's\s+it\s+going|whats?\s+up|kya\s+haal)\b", body):
        return False
    return bool(_CLIENT_ACTION_REQUEST_RE.search(body))


def owner_ack_creates_commitment(outbound: str | None, last_inbound: str | None) -> bool | None:
    """Return False to hard-block, True if ack+request, None if not an ack (let LLM decide)."""
    if not is_bare_ack(outbound):
        return None
    return last_inbound_is_action_request(last_inbound)


def resolve_owner_reminder_schedule(
    explicit_deadline: datetime | None,
    *,
    morning_hour: int = 9,
    tz_name: str = "Asia/Kolkata",
    now_utc: datetime | None = None,
) -> tuple[datetime, bool]:
    """Map a detected deadline to (deadline_at, create_immediate_inbox_chip).

    No explicit timeframe → next Morning Brief; do not use a 24h fallback.
    """
    if explicit_deadline is not None:
        return explicit_deadline, True
    return (
        next_morning_brief_utc(hour=morning_hour, tz_name=tz_name, now_utc=now_utc),
        False,
    )


def is_immediate_request(text: str | None) -> bool:
    """Abhi / now / ASAP — not a future commitment for follow-up tracking."""
    return bool(_IMMEDIATE_REQUEST_RE.search((text or "").strip()))


def deadline_is_near(deadline_at: datetime | None, *, within_hours: float = 4.0) -> bool:
    if deadline_at is None:
        return False
    now = datetime.utcnow()
    delta_h = (deadline_at - now).total_seconds() / 3600
    return 0 <= delta_h <= within_hours


def next_morning_brief_utc(
    *,
    hour: int = 9,
    tz_name: str = "Asia/Kolkata",
    now_utc: datetime | None = None,
) -> datetime:
    """Next local Morning Brief wall-clock, returned as naive UTC."""
    now_utc = now_utc or datetime.utcnow()
    tz = ZoneInfo(tz_name)
    # Treat naive now_utc as UTC.
    from datetime import timezone

    aware = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    candidate = aware.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= aware:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def format_due_when(deadline_at: datetime | None, *, tz_name: str = "Asia/Kolkata") -> str:
    if deadline_at is None:
        return "soon"
    from datetime import timezone

    tz = ZoneInfo(tz_name)
    local = deadline_at.replace(tzinfo=timezone.utc).astimezone(tz)
    today = datetime.now(tz).date()
    if local.date() == today:
        return f"today {local.strftime('%I:%M %p').lstrip('0').lower()}"
    if local.date() == today + timedelta(days=1):
        return "tomorrow"
    return local.strftime("%A")


def format_owner_reminder_chip(
    *,
    label: str,
    contact_name: str,
    deadline_at: datetime | None,
    tz_name: str = "Asia/Kolkata",
) -> str:
    when = format_due_when(deadline_at, tz_name=tz_name)
    clean_label = (label or "follow up").strip()
    # Normalize "Send documents" → "send documents"
    if clean_label and clean_label[0].isupper():
        clean_label = clean_label[0].lower() + clean_label[1:]
    return f"You promised: {clean_label} to {contact_name}, due {when}."


def owner_followup_subject(body: str | None) -> str:
    """Label what the owner is waiting for — only when the outbound text supports it."""
    text = (body or "").strip().lower()
    if re.search(r"\b(quote|quotation)\b", text):
        return "quotation"
    if re.search(r"\b(invoice)\b", text):
        return "invoice"
    if re.search(r"\b(proposal)\b", text):
        return "proposal"
    if re.search(r"\b(price\s*list|pricing)\b", text):
        return "pricing"
    if re.search(r"\b(payment|pay)\b", text) and re.search(
        r"\b(confirm|confirmation|received|proof)\b", text
    ):
        return "payment confirmation"
    if re.search(r"\b(document|documents|docs?|coa|file|attachment)\b", text):
        return "document"
    if re.search(r"\b(approv(?:e|al)|sign\s*off|go[\s-]?ahead)\b", text):
        return "approval"
    if text.endswith("?"):
        return "question"
    return "message"


def usable_contact_display_name(name: str | None) -> str | None:
    """Human name safe to put in a drafted greeting, or None if only a phone/JID.

    Chip labels may still show the raw wa_id; drafts should not invent placeholders.
    """
    text = (name or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"the client", "client", "[client's name]", "client's name"}:
        return None
    # WhatsApp JIDs / bare phone numbers are not greetable names.
    if "@" in text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    non_digit = "".join(ch for ch in text if ch.isalnum() and not ch.isdigit())
    if len(digits) >= 8 and not non_digit:
        return None
    return text


def format_waiting_on_silence(
    *,
    contact_name: str,
    subject: str,
    hours_waiting: float,
) -> str:
    days = max(1, int(hours_waiting / 24))
    day_word = "day" if days == 1 else "days"
    return f"Waiting on {contact_name}: reply to your {subject}, {days} {day_word}."


def format_waiting_on_promise(
    *,
    contact_name: str,
    label: str,
    hours_waiting: float,
) -> str:
    days = max(1, int(hours_waiting / 24))
    day_word = "day" if days == 1 else "days"
    clean = (label or "update").strip()
    if clean and clean[0].isupper():
        clean = clean[0].lower() + clean[1:]
    return f"Waiting on {contact_name}: {clean}, {days} {day_word}."
