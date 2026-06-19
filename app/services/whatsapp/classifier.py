import json
import logging
from typing import Any

from openai import APIError, RateLimitError

from app.config import CALENDAR_DEFAULT_TIMEZONE, OPENAI_MODEL
from app.services.summary.client import get_openai_client

logger = logging.getLogger(__name__)


class WhatsAppAIError(Exception):
    """OpenAI call failed for WhatsApp classification/drafting."""


# Single source of truth for the category taxonomy. Extend here to add categories.
CATEGORIES = ("meeting", "budget", "scope", "timeline", "follow_up", "other")
# Categories that produce a drafted reply suggestion (everything except meeting).
REPLY_CATEGORIES = ("budget", "scope", "timeline", "follow_up", "other")

_CATEGORY_GUIDE = (
    "- meeting: the client wants to schedule/confirm a call or meeting, or proposes a time.\n"
    "- budget: pricing, cost, quotation, payment, or any amount the client asks about.\n"
    "- scope: questions about project scope, features, deliverables, requirements, or details.\n"
    "- timeline: deadlines, delivery dates, ETAs, or 'when will it be ready'.\n"
    "- follow_up: the client is chasing a pending reply/deliverable ('any update?', reminders).\n"
    "- other: any other important client query that does not fit the above."
)

_CLASSIFY_SYSTEM = (
    "You triage inbound WhatsApp messages from clients for a freelancer/agency. "
    "Detect the message language. Decide whether the message is important and needs action. "
    "Greetings, small-talk, thanks, emojis-only, and acknowledgements are NOT important "
    "(set is_important=false, category=greeting). "
    "Important messages MUST be classified into exactly one category:\n"
    f"{_CATEGORY_GUIDE}\n"
    "Use the recent conversation history for context (e.g. a vague 'any update?' is follow_up "
    "on the prior thread). "
    "Respond ONLY with a JSON object with keys: "
    "is_important (boolean), category (one of "
    f"{', '.join(CATEGORIES)}, or 'greeting'), "
    "language (the detected language name), summary (one short sentence describing the ask)."
)

_REPLY_SYSTEM = (
    "You draft a concise, professional WhatsApp reply on behalf of a freelancer/agency to a "
    "client. Reply in the SAME language as the client's message. Be warm but to the point. "
    "Do not invent facts, prices, or commitments; if information is needed, ask for it or say "
    "you will confirm shortly. Return ONLY the reply text, no preamble."
)

_MEETING_SYSTEM = (
    "You extract meeting details from a client's WhatsApp message and conversation history. "
    "Use the provided current date/time to resolve relative phrases like 'tomorrow', 'next Monday', "
    "or 'at 3pm' into concrete ISO 8601 datetimes in the given timezone. "
    "Respond ONLY with a JSON object with keys: title (short meeting title), agenda (1-3 lines), "
    "start (ISO 8601 datetime with timezone offset if resolved, else null), "
    "end (ISO 8601 datetime if given or inferable, else null). "
    "If the client wants to meet but gives no specific time, set start and end to null."
)


def _history_block(history: list[dict[str, str]]) -> str:
    if not history:
        return "(no prior messages)"
    lines = []
    for item in history:
        who = "Client" if item.get("direction") == "inbound" else "Me"
        text = (item.get("body") or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines) if lines else "(no prior messages)"


def _chat_json(system: str, user_content: str, *, max_tokens: int) -> dict[str, Any]:
    raw = _chat(system, user_content, max_tokens=max_tokens, json_mode=True)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        logger.warning("[WHATSAPP] Non-JSON model response: %s", raw[:200])
    return {}


def classify_message(history: list[dict[str, str]], message: str) -> dict[str, Any]:
    user_content = (
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"New client message:\n{message}\n\n"
        "Classify this new message as JSON:"
    )
    data = _chat_json(_CLASSIFY_SYSTEM, user_content, max_tokens=200)

    is_important = bool(data.get("is_important", False))
    category = str(data.get("category") or "greeting").strip().lower()
    if not is_important:
        category = "greeting"
    elif category not in CATEGORIES:
        category = "other"
    return {
        "is_important": is_important,
        "category": category,
        "language": (data.get("language") or "").strip() or None,
        "summary": (data.get("summary") or "").strip() or None,
    }


def draft_reply(history: list[dict[str, str]], message: str, category: str) -> str:
    category_hint = {
        "budget": "The client is asking about budget/pricing. Acknowledge and indicate next step.",
        "scope": "The client is asking about scope/requirements. Clarify or confirm details.",
        "timeline": "The client is asking about timeline. Confirm or propose dates.",
        "follow_up": "The client is following up. Acknowledge and give a status or next step.",
        "other": "Respond helpfully to the client's query.",
        "meeting": "The client wants to meet. Acknowledge and confirm a time or propose options.",
    }.get(category, "Respond helpfully to the client's query.")

    user_content = (
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"Client's latest message:\n{message}\n\n"
        f"Context: {category_hint}\n\n"
        "Write the reply text:"
    )
    return _chat(_REPLY_SYSTEM, user_content, max_tokens=400, json_mode=False).strip()


def extract_meeting(history: list[dict[str, str]], message: str) -> dict[str, Any]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    user_content = (
        f"Current date/time: {now.isoformat()} ({CALENDAR_DEFAULT_TIMEZONE})\n\n"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"Client's latest message:\n{message}\n\n"
        "Extract meeting details as JSON:"
    )
    data = _chat_json(_MEETING_SYSTEM, user_content, max_tokens=300)
    return {
        "title": (data.get("title") or "").strip() or "Meeting with client",
        "agenda": (data.get("agenda") or "").strip() or None,
        "start": (data.get("start") or None),
        "end": (data.get("end") or None),
    }


def _chat(system: str, user_content: str, *, max_tokens: int, json_mode: bool) -> str:
    client = get_openai_client()
    if client is None:
        raise WhatsAppAIError("OPENAI_API_KEY is not configured")

    kwargs: dict[str, Any] = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs)
    except RateLimitError as exc:
        logger.error("[WHATSAPP] OpenAI rate limited / quota: %s", exc)
        raise WhatsAppAIError(str(exc)) from exc
    except APIError as exc:
        logger.error("[WHATSAPP] OpenAI API error: %s", exc)
        raise WhatsAppAIError(str(exc)) from exc

    return (response.choices[0].message.content or "").strip()
