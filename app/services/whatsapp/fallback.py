import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import CALENDAR_DEFAULT_TIMEZONE, WHATSAPP_DEFAULT_MEETING_MINUTES

_SPAM_STRONG = (
    "you have won",
    "you've won",
    "congratulations you",
    "claim your prize",
    "claim your reward",
    "limited time offer",
    "click here to claim",
    "click the link",
    "free gift",
    "you are selected",
    "you have been selected",
    "winner selected",
    "lottery",
    "lucky draw",
    "100% free",
    "act now",
    "guaranteed",
    "no cost",
    "earn money from home",
    "work from home earn",
    "make money fast",
    "investment opportunity",
    "double your money",
    "100% profit",
    "bulk sms",
    "dear customer",
    "dear user",
    "otp",
    "verify your account",
    "your account has been",
    "update your kyc",
    "kyc update",
)

_URL_RE = re.compile(r"https?://", re.I)


def is_likely_spam(message: str, *, has_prior_history: bool) -> bool:
    """Fast, rule-based spam pre-screen (no LLM).

    Returns True when the message shows strong spam signals *and* the contact has no
    prior conversation history, OR when it shows multiple very strong signals regardless.
    The LLM is still used as a second pass for edge cases that reach it.
    """
    lower = (message or "").strip().lower()
    if not lower:
        return False

    strong_hit = any(phrase in lower for phrase in _SPAM_STRONG)
    has_url = bool(_URL_RE.search(lower))

    if strong_hit:
        return True
    if not has_prior_history and has_url and len(lower) < 200:
        return True
    return False

_PERSONAL_TEXT_SIGNALS_RE = re.compile(
    r"""
    \?               # question mark — direct inquiry
    | \bcan you\b
    | \bcould you\b
    | \bplease\b
    | \bdo you\b
    | \bwhat do you\b
    | \blet me know\b
    | \bthoughts\b
    | \bopinion\b
    """,
    re.I | re.VERBOSE,
)


def forwarded_has_personal_text(message: str) -> bool:
    """Heuristic: does a forwarded message contain personal text from the sender?

    Used when the LLM is unavailable. False positives are acceptable here because
    missing a genuine request is worse than letting a non-personal forward through.
    """
    return bool(_PERSONAL_TEXT_SIGNALS_RE.search(message or ""))


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
    "discuss" ,
)

_PAYMENT_RECEIVED_KEYWORDS = (
    "payment done",
    "paid",
    "transferred",
    "amount transferred",
    "sent the payment",
    "made the payment",
    "payment sent",
)

_PAYMENT_OVERDUE_KEYWORDS = (
    "invoice",
    "pending payment",
    "payment pending",
    "still pending",
    "when will you pay",
    "outstanding",
    "overdue",
    "not received payment",
)

_PAYMENT_KEYWORDS = ("payment", "invoice") + _PAYMENT_RECEIVED_KEYWORDS

_LEAD_KEYWORDS = (
    "supplier",
    "referred me",
    "someone referred",
    "bulk price",
    "bulk prices",
    "do you supply",
    "do you sell",
    "looking for a",
    "inquiry",
    "enquiry",
)

_DOCUMENT_VERBS = ("send", "share", "forward", "provide", "need the", "need a", "i need")
_DOCUMENT_NOUNS = (
    "invoice",
    "price list",
    "catalogue",
    "catalog",
    "brochure",
    "document",
    "documents",
    "report",
    "quotation",
    "receipt",
    "shipping",
)

_COMPLAINT_KEYWORDS = (
    "not arrived",
    "not received",
    "damaged",
    "defective",
    "broken",
    "not working",
    "unhappy",
    "disappointed",
    "worst",
    "terrible",
    "very bad",
    "poor service",
    "complaint",
    "refund",
    "third time",
    "again and again",
    "still waiting",
)


_PERSONAL_DATE_KEYWORDS = (
    "birthday",
    "bday",
    "anniversary",
    "wedding anniversary",
    "surgery",
    "appointment",
    "school concert",
    "exam",
    "graduation",
    "don't forget",
    "dont forget",
    "remember",
    "mark the date",
    "save the date",
)

_FAMILY_PLAN_KEYWORDS = (
    "let's go to dinner",
    "family lunch",
    "family dinner",
    "family breakfast",
    "movie tonight",
    "movie night",
    "movie saturday",
    "movie sunday",
    "sunday lunch",
    "saturday dinner",
    "mum's place",
    "mom's place",
    "grandma's place",
    "going out",
    "let's all go",
    "we're all going",
    "are you free saturday",
    "are you free sunday",
    "are you free this weekend",
    "this weekend",
    "family outing",
    "beach sunday",
    "picnic",
    "get together",
    "plan for the weekend",
)

_PERSONAL_TASK_KEYWORDS = (
    "pick up",
    "on the way home",
    "way home",
    "on your way",
    "get milk",
    "we need",
    "buy ",
    "book a table",
    "book the",
    "dry cleaning",
    "pharmacy",
    "grocery",
    "groceries",
    "electricity bill",
    "pay the",
    "don't forget to",
    "dont forget to",
    "reminder to",
)

_SHIPMENT_KEYWORDS = (
    "shipment",
    "shipped",
    "customs",
    "out for delivery",
    "delivered",
    "in transit",
    "dispatched",
    "dispatch",
    "consignment",
    "courier",
    "tracking",
    "package",
    "parcel",
    "goods received",
)

_SHIPMENT_DELAY_KEYWORDS = ("delay", "delayed", "held up", "stuck", "late")


def _is_document_request(lower: str) -> bool:
    return any(verb in lower for verb in _DOCUMENT_VERBS) and any(
        noun in lower for noun in _DOCUMENT_NOUNS
    )


def _detect_document_type(lower: str) -> str | None:
    for noun in _DOCUMENT_NOUNS:
        if noun in lower:
            return noun
    return None

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


def _result(
    *,
    is_important: bool,
    category: str,
    priority: str,
    summary: str | None,
    language: str | None = "English",
    payment_status: str | None = None,
    document_type: str | None = None,
    anger_level: str | None = None,
    shipment_status: str | None = None,
) -> dict[str, Any]:
    return {
        "is_important": is_important,
        "category": category,
        "priority": priority,
        "payment_status": payment_status,
        "document_type": document_type,
        "anger_level": anger_level,
        "shipment_status": shipment_status,
        "language": language,
        "translation": None,
        "summary": summary,
    }


def classify_message(message: str) -> dict[str, Any] | None:
    """Rule-based triage when OpenAI is unavailable."""
    text = (message or "").strip()
    if not text:
        return _result(
            is_important=False,
            category="greeting",
            priority="normal",
            summary=None,
            language=None,
        )

    lower = text.lower()

    if any(keyword in lower for keyword in _COMPLAINT_KEYWORDS):
        return _result(
            is_important=True,
            category="complaint",
            priority="critical",
            anger_level="high",
            summary="Client is unhappy or reporting a problem.",
        )

    if _is_document_request(lower):
        return _result(
            is_important=True,
            category="document",
            priority="high",
            document_type=_detect_document_type(lower),
            summary="Client requested a specific document.",
        )

    if any(keyword in lower for keyword in _PAYMENT_KEYWORDS) or any(
        keyword in lower for keyword in _PAYMENT_OVERDUE_KEYWORDS
    ):
        received = any(keyword in lower for keyword in _PAYMENT_RECEIVED_KEYWORDS)
        return _result(
            is_important=True,
            category="payment",
            priority="critical",
            payment_status="received" if received else "overdue",
            summary="Client sent a payment-related message.",
        )

    if any(keyword in lower for keyword in _SHIPMENT_KEYWORDS):
        delayed = any(keyword in lower for keyword in _SHIPMENT_DELAY_KEYWORDS)
        return _result(
            is_important=True,
            category="shipment",
            priority="medium",
            shipment_status="delayed" if delayed else "good",
            summary="Delivery/shipment status update.",
        )

    if any(keyword in lower for keyword in _LEAD_KEYWORDS):
        return _result(
            is_important=True,
            category="lead",
            priority="very_high",
            summary="Potential new lead or inquiry.",
        )

    if any(keyword in lower for keyword in _FAMILY_PLAN_KEYWORDS):
        return _result(
            is_important=True,
            category="family_plan",
            priority="low",
            summary="Personal family outing or social plan proposed.",
        )

    if any(keyword in lower for keyword in _PERSONAL_DATE_KEYWORDS):
        return _result(
            is_important=True,
            category="personal_date",
            priority="low",
            summary="Personal date or reminder mentioned.",
        )

    if any(keyword in lower for keyword in _PERSONAL_TASK_KEYWORDS):
        return _result(
            is_important=True,
            category="personal_task",
            priority="low",
            summary="Personal errand or task request.",
        )

    if any(keyword in lower for keyword in _MEETING_KEYWORDS):
        return _result(
            is_important=True,
            category="meeting",
            priority="high",
            summary="Client wants to schedule a meeting or call.",
        )

    return None


def extract_meeting(message: str) -> dict[str, Any]:
    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    start = _parse_time_hint(message, now)
    end = start + timedelta(minutes=WHATSAPP_DEFAULT_MEETING_MINUTES) if start else None
    return {
        "title": "Meeting with client",
        "agenda": message.strip(),
        "location": None,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "confirmed": start is not None,
    }


def extract_family_plan(message: str) -> dict[str, Any]:
    """Keyword-only fallback: best-effort family plan structure (date/time not resolved)."""
    return {
        "event_label": "Family plan",
        "date": None,
        "time": None,
        "place": None,
        "confirmed": False,
    }


def extract_personal_date(message: str) -> dict[str, Any]:
    """Keyword-only fallback: returns a best-effort personal date structure (no date resolved)."""
    return {
        "event_label": "Personal date",
        "date": None,
        "reminder_title": f"Remember: {message.strip()[:80]}",
        "notes": message.strip()[:200] or None,
    }


def extract_personal_task(message: str) -> dict[str, Any]:
    """Keyword-only fallback: returns a best-effort task structure."""
    return {
        "task_summary": message.strip()[:120],
        "items": [],
        "timing_hint": None,
    }


def draft_reply(
    category: str,
    *,
    payment_status: str | None = None,
    shipment_status: str | None = None,
) -> str:
    if category == "meeting":
        return (
            "Yes, happy to connect! I'll confirm the time shortly."
        )
    if category == "payment":
        if payment_status == "received":
            return (
                "Thank you! I'll verify the payment on our end and confirm as soon as it "
                "reflects."
            )
        return (
            "Thanks for the reminder — apologies for the delay. I'm looking into this and will "
            "share an update shortly."
        )
    if category == "lead":
        return (
            "Thank you for reaching out! I'd be glad to help. Could you share a few details "
            "about what you're looking for so I can send the right information?"
        )
    if category == "document":
        return (
            "Sure — I'll get that across to you shortly. Please give me a moment to prepare it."
        )
    if category == "complaint":
        return (
            "I'm really sorry about this and I completely understand your frustration. "
            "Thank you for flagging it — let me look into it right away and get back to you "
            "with a resolution as quickly as possible."
        )
    if category == "greeting":
        return "Thank you — lovely to hear from you! Hope you're doing well too."
    if category == "shipment":
        if shipment_status == "delayed":
            return (
                "Quick update on your shipment — there's a short delay in transit. Apologies for "
                "the inconvenience; I'm tracking it closely and will confirm the revised delivery "
                "as soon as I have it."
            )
        return (
            "Thanks for the update — good to know the shipment is progressing well. I'll keep an "
            "eye on it and let you know if anything changes."
        )
    return "Thanks for your message. I'll get back to you shortly."
