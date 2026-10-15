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
    r"(?i)^\s*(ok|okay|ok+|sure|haan|han|yes|yep|done|noted|thik|theek|👍|👌)"
    r"(?:\s+(?:sir|ji|bhai))?"
    r"\s*[.!]*\s*$"
)

_CLIENT_ACTION_REQUEST_RE = re.compile(
    r"(?i)\b("
    r"please\s+(?:send|share|provide|forward|mail|email)|"
    r"(?:please\s+)?(?:just\s+)?make\b.{0,40}\bcall\b|"
    r"\bcall\s+to\s+[a-z]|"
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


# A request to create or send a draft/proposal. An invoice copy is not this request.
_MATERIAL_PREP_REQUEST_RE = re.compile(
    r"(?i)("
    r"\b(?:make|prepare|create|revise|update|redo)\b.{0,60}\b(?:draft|proposals?)\b"
    r"|\b(?:new|revised|updated)\s+(?:draft|proposals?)\b"
    r"|\binclud(?:e|es|ing)\s+(?:the\s+)?(?:new\s+)?proposals?\b"
    r"|\b(?:send|share)\b.{0,50}\b(?:draft|proposals?)\b"
    r"|\b(?:draft|proposals?)\b.{0,50}\b(?:send|share)\b"
    r")"
)
_MATERIAL_FOLLOWUP_RE = re.compile(
    r"(?i)^\s*(?:yes|yeah|yep|ok|okay|sure)(?:\s+please)?[.!]?\s*$"
)
# Owner-side evidence only. "I will finalize the hotel" is not agreement to send a draft.
_OWNER_MATERIAL_READY_RE = re.compile(
    r"(?i)("
    r"\b(?:draft|proposals?)\b.{0,50}\b(?:ready|attached|available|prepared|done)\b"
    r"|\b(?:ready|attached|available|prepared)\b.{0,50}\b(?:draft|proposals?)\b"
    r"|\b(?:i(?:'ll| will)|we(?:'ll| will))\s+"
    r"(?:prepare|make|send|share|include|revise)\b.{0,50}\b(?:draft|proposals?)\b"
    r"|\bhere (?:is|are) the (?:new\s+)?(?:draft|proposals?)\b"
    r")"
)


def _history_body(item) -> str:
    if isinstance(item, dict):
        return str(item.get("body") or item.get("content") or "")
    return str(item or "")


def _is_owner_turn(item) -> bool:
    if not isinstance(item, dict):
        return False
    return (item.get("direction") or "").strip().lower() in {"outbound", "owner", "me"}


def _is_client_turn(item) -> bool:
    if not isinstance(item, dict):
        return False
    return (item.get("direction") or "").strip().lower() == "inbound"


def owner_has_material_ready(history: list | None) -> bool:
    """True when an owner message shows the draft or proposals are ready to send."""
    for item in history or []:
        if _is_owner_turn(item) and _OWNER_MATERIAL_READY_RE.search(_history_body(item)):
            return True
    return False


def asks_for_unprepared_material(message: str | None, history: list | None) -> bool:
    """True when this inbound asks to prepare or send a draft/proposal."""
    body = (message or "").strip()
    if _MATERIAL_PREP_REQUEST_RE.search(body):
        return True
    if body and _MATERIAL_FOLLOWUP_RE.match(body):
        return any(
            _is_client_turn(item) and _MATERIAL_PREP_REQUEST_RE.search(_history_body(item))
            for item in history or []
        )
    return False


def should_clarify_missing_material_readiness(message: str | None, history: list | None) -> bool:
    """True when a draft/proposal request lacks owner agreement and available material.

    A request does not prove the owner agreed to fulfil it. An invoice resend is not
    this check.
    """
    if not asks_for_unprepared_material(message, history):
        return False
    return not owner_has_material_ready(history)


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
    if (
        is_greeting_or_festival(body)
        or is_bare_ack(body)
        or is_closing_thanks(body)
        or is_bare_please(body)
    ):
        return False
    # Pure social check-ins are not action requests.
    if re.match(r"(?i)^\s*(how\s+are\s+you|how's\s+it\s+going|whats?\s+up|kya\s+haal)\b", body):
        return False
    return bool(_CLIENT_ACTION_REQUEST_RE.search(body))


_CLOSING_THANKS_RE = re.compile(
    r"(?i)^\s*(?:"
    r"thanks|thank\s+you|thankyou|thx|ty|"
    r"dhanyavaad|dhanyavad|shukriya"
    r")"
    r"(?:\s+(?:so\s+much|sir|ji|bhai|bro))?"
    r"\s*[!.]*\s*$"
)
# "if weekend ... I'll update you a date prior" is not a promise to update today.
_CONDITIONAL_UPDATE_RE = re.compile(
    r"(?i)("
    r"\bif\b.{0,80}\b(?:weekend|anything|needed|required|possible)\b"
    r"|\b(?:weekend|anything)\b.{0,60}\bi(?:'ll| will)\s+update\b"
    r"|\bupdate you\b.{0,40}\bdate\b"
    r"|\ba date prior\b"
    r"|\btaak\b|\btak anything\b"
    r")"
)
_FUTURE_PROMISE_RE = re.compile(
    r"(?i)\b("
    r"i(?:'ll| will)|we(?:'ll| will)|"
    r"will\s+(?:send|share|confirm|update|call|revert|do)|"
    r"let me\b|going to\b|"
    r"bhej(?:ta|unga|enge|doonga)|"
    r"\brevert\b|\bkal\b"
    r")"
)
_QUESTION_ONLY_RE = re.compile(
    r"(?i)(?:"
    r"^\s*any\s+updates?\b"
    r"|^\s*(?:what(?:'s| is)|when|how)\b.*\?\s*$"
    r"|^\s*.+\?\s*$"
    r")"
)


def is_status_question(text: str | None) -> bool:
    """A question, including 'Any updates on the payment' with no question mark."""
    return bool(_QUESTION_ONLY_RE.match((text or "").strip()))


_BARE_PLEASE_RE = re.compile(r"(?i)^\s*(?:pls|plz|please)\s*[!.]*\s*$")


def is_bare_please(text: str | None) -> bool:
    """A one-word please. It is not an owner promise."""
    return bool(_BARE_PLEASE_RE.match((text or "").strip()))


def is_closing_thanks(text: str | None) -> bool:
    """A bare thank-you. It does not ask for anything and is not a new promise."""
    return bool(_CLOSING_THANKS_RE.match((text or "").strip()))


def is_conditional_update(text: str | None) -> bool:
    """True when an update is only promised if some later condition happens."""
    return bool(_CONDITIONAL_UPDATE_RE.search((text or "").strip()))


def states_future_promise(text: str | None) -> bool:
    """True when this message itself promises a later action."""
    return bool(_FUTURE_PROMISE_RE.search((text or "").strip()))


def owner_reminder_still_supported(message: str | None, label: str | None) -> bool:
    """False when a stored owner promise came from thanks, please, or a conditional update.

    An empty source is left alone. A real promise such as 'I'll send it by Friday' stays.
    """
    body = (message or "").strip()
    if not body:
        return True
    if (
        is_conditional_update(body)
        or is_closing_thanks(body)
        or is_bare_please(body)
        or is_greeting_or_festival(body)
        or is_status_question(body)
    ):
        return False
    if label_infers_unstated_payment_update(body, label):
        return False
    return True


def label_infers_unstated_payment_update(message: str | None, label: str | None) -> bool:
    """True when a label adds a payment-status update the message never stated."""
    label_l = (label or "").lower()
    msg = (message or "").lower()
    if not label_l:
        return False
    if re.search(r"\b(?:update|status)\b", label_l) and not re.search(
        r"\b(?:update|status)\b", msg
    ):
        return True
    if re.search(r"\bpay(?:ment)?\b", label_l) and not re.search(r"\bpay(?:ment|ing)?\b", msg):
        return True
    return False


def supported_commitment_deadline(
    message: str | None,
    deadline: str | None,
    *,
    also: str | None = None,
) -> str | None:
    """Keep a model deadline only when this message, or the accepted request, states it."""
    from app.services.whatsapp.decision import stated_deadline_date

    raw = (deadline or "").strip()
    if not raw:
        return None
    day = raw[:10]
    hour_phrase = re.compile(
        r"(?i)\b(?:in\s+\d+\s+hours?|\d+\s+hours?|a\s+couple\s+of\s+hours?|"
        r"in\s+a\s+couple\s+of\s+hours?|end of day|eod)\b"
    )
    for text in (message, also):
        if not text:
            continue
        if stated_deadline_date(text, day):
            return raw
        if re.search(r"(?i)\bkal\b", text):
            return raw
        if hour_phrase.search(text):
            return raw
    return None


def accepted_action_label(request: str | None, history: list | None) -> str | None:
    """Action the owner accepted, including who it is for and the prior topic.

    The contact who asked is not the call target. "Call Rahul" stays Rahul even
    when Shiv Kumar Tiwari sent the request.
    """
    body = (request or "").strip()
    call = re.search(
        r"(?i)\bcall\s+to\s+([A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,3})",
        body,
    )
    if not call:
        return None
    target = re.sub(r"(?i)\s+sir$", "", call.group(1)).strip()
    topic = None
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        if (item.get("direction") or "").strip().lower() not in {"outbound", "owner", "me"}:
            continue
        text = str(item.get("body") or item.get("content") or "")
        found = re.search(r"(?i)\babout\s+(?:the\s+)?(.+?)[.!]*\s*$", text)
        if found:
            topic = found.group(1).strip(" .")
            break
    if topic:
        return f"call {target} about the {topic}"
    return f"call {target}"


def outbound_may_complete_commitment(label: str | None, message: str | None) -> bool:
    """A later owner message completes a reminder only when it relates to that action."""
    body = (message or "").strip()
    if not body or is_greeting_or_festival(body) or is_closing_thanks(body) or is_bare_please(body):
        return False
    if is_bare_ack(body) or is_small_talk_message(body):
        return False
    evidence = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", body)
    evidence = evidence.replace("_", " ").replace("-", " ").lower()
    skip = {
        "call",
        "about",
        "send",
        "share",
        "this",
        "that",
        "with",
        "your",
        "from",
        "have",
        "will",
        "just",
        "please",
        "file",
        "document",
    }
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", label or ""):
        if len(word) < 4 and not word.isupper():
            continue
        if word.lower() in skip:
            continue
        if word.lower() in evidence:
            return True
    return False


def keyword_payment_status(message: str | None) -> str | None:
    """Status for a payment keyword hit when the model is unavailable.

    A bare mention of payment, including incoming or pending, is unknown.
    Received and overdue are kept only when the message says so.
    """
    lower = (message or "").strip().lower()
    if not lower:
        return None
    if re.search(r"\b(?:overdue|outstanding|unpaid)\b", lower):
        return "overdue"
    if "when will you pay" in lower or ("not received" in lower and "payment" in lower):
        return "overdue"
    if re.search(r"\bnot\s+paid\b", lower):
        return "overdue"
    received = (
        "payment done",
        "paid",
        "transferred",
        "amount transferred",
        "sent the payment",
        "made the payment",
        "payment sent",
        "payment received",
        "received the payment",
        "received your payment",
    )
    if any(marker in lower for marker in received):
        return "received"
    return None


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
