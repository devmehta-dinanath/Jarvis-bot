import json
import logging
from typing import Any

from openai import APIError, RateLimitError

from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    OPENAI_MODEL,
)
from app.services.summary.client import get_openai_client
from app.services.whatsapp import decision as wa_decision
from app.services.whatsapp import meeting_scope
from app.services.whatsapp import prompts as wa_prompts

logger = logging.getLogger(__name__)


class WhatsAppAIError(Exception):
    """OpenAI call failed for WhatsApp classification/drafting."""


CATEGORIES = (
    "meeting",
    "payment",
    "lead",
    "document",
    "complaint",
    "shipment",
    "order",
    "personal_date",
    "personal_task",
    "family_plan",
    "budget",
    "scope",
    "timeline",
    "follow_up",
    "other",
)
REPLY_CATEGORIES = (
    "payment",
    "lead",
    "document",
    "complaint",
    "shipment",
    "order",
    "budget",
    "scope",
    "timeline",
    "follow_up",
    "other",
)

LIFE_LANE_CATEGORIES = ("personal_date", "personal_task", "family_plan")
WORK_LANE_CATEGORIES = tuple(c for c in CATEGORIES if c not in LIFE_LANE_CATEGORIES)
ALWAYS_IMPORTANT = ("payment", "complaint")
FILTER_LABELS = ("forwarded", "spam", "instruction_skip")
PRIORITIES = ("low", "normal", "medium", "high", "very_high", "critical")
_CATEGORY_PRIORITY = {
    "payment": "critical",
    "complaint": "critical",
    "lead": "very_high",
    "meeting": "high",
    "document": "high",
    "follow_up": "high",
    "order": "high",
    "shipment": "medium",
    "personal_date": "low",
    "personal_task": "low",
    "family_plan": "low",
}

wa_prompts.install_categories(CATEGORIES)

_CATEGORY_GUIDE = wa_prompts._CATEGORY_GUIDE
_CLASSIFY_SYSTEM = wa_prompts._CLASSIFY_SYSTEM
_REPLY_SYSTEM = wa_prompts._REPLY_SYSTEM
_REPLY_SYSTEM_PERSONAL = wa_prompts._REPLY_SYSTEM_PERSONAL
_COMPLAINT_SYSTEM = wa_prompts._COMPLAINT_SYSTEM
_FINALISATION_SIGNALS = wa_prompts._FINALISATION_SIGNALS
_MEETING_SYSTEM = wa_prompts._MEETING_SYSTEM
_DEADLINE_HINT_INSTRUCTION = wa_prompts._DEADLINE_HINT_INSTRUCTION
_COMMITMENT_SYSTEM = wa_prompts._COMMITMENT_SYSTEM
_FULFILLMENT_SYSTEM = wa_prompts._FULFILLMENT_SYSTEM
_CLIENT_COMMITMENT_SYSTEM = wa_prompts._CLIENT_COMMITMENT_SYSTEM
_CLIENT_FULFILLMENT_SYSTEM = wa_prompts._CLIENT_FULFILLMENT_SYSTEM
_CLIENT_NUDGE_SYSTEM = wa_prompts._CLIENT_NUDGE_SYSTEM
_OWNER_COMMITMENT_DRAFT_SYSTEM = wa_prompts._OWNER_COMMITMENT_DRAFT_SYSTEM
_PERSONAL_DATE_SYSTEM = wa_prompts._PERSONAL_DATE_SYSTEM
_DEADLINE_SYSTEM = wa_prompts._DEADLINE_SYSTEM
_PERSONAL_TASK_SYSTEM = wa_prompts._PERSONAL_TASK_SYSTEM
_FAMILY_PLAN_SYSTEM = wa_prompts._FAMILY_PLAN_SYSTEM
payment_reply_hint = wa_prompts.payment_reply_hint
shipment_reply_hint = wa_prompts.shipment_reply_hint
_corrections_block = wa_prompts._corrections_block
_reply_corrections_block = wa_prompts._reply_corrections_block
_user_instructions_block = wa_prompts._user_instructions_block
_forwarded_system = wa_prompts._forwarded_system
_group_system = wa_prompts._group_system
_now_context_line = wa_prompts._now_context_line
_language_context_block = wa_prompts._language_context_block
_history_block = wa_prompts._history_block
_personal_contact_block = wa_prompts._personal_contact_block
_tone_instructions_block = wa_prompts._tone_instructions_block
_voice_examples_block = wa_prompts._voice_examples_block
stated_deadline_date = wa_decision.stated_deadline_date


def base_priority(category: str) -> str:
    """Default priority for a category before any time-based escalation."""
    return _CATEGORY_PRIORITY.get(category, "normal")


def max_priority(a: str, b: str) -> str:
    """Return the more urgent of two priority levels."""
    order = {level: i for i, level in enumerate(PRIORITIES)}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def complaint_priority(anger_level: str | None) -> str:
    """A complaint is always urgent; the angrier the tone, the higher the level."""
    if anger_level == "low":
        return "very_high"
    return "critical"


def detect_commitment(
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    last_inbound: str | None = None,
) -> dict[str, Any]:
    """Does this outbound message promise the client something not yet delivered?

    Pass conversation context so 'Ok' after a client request can be detected.
    Isolated 'Ok' after small talk is hard-blocked without calling the model.
    """
    from app.services.whatsapp import product_gates

    ack_gate = product_gates.owner_ack_creates_commitment(message, last_inbound)
    if ack_gate is False:
        return {
            "is_commitment": False,
            "commitment_type": None,
            "label": None,
            "deadline_at": None,
        }

    context_bits: list[str] = []
    if history:
        context_bits.append("Recent conversation:\n" + _history_block(history))
    if last_inbound:
        context_bits.append(f"Client's last inbound message:\n{last_inbound}")
    context_bits.append(f"Message the account owner just sent:\n{message}")
    user_content = (
        f"{_now_context_line()}" + "\n\n".join(context_bits) + "\n\nAnalyze as JSON:"
    )
    data = _chat_json(_COMMITMENT_SYSTEM, user_content, max_tokens=160)
    result = {
        "is_commitment": bool(data.get("is_commitment", False)),
        "commitment_type": (data.get("commitment_type") or "").strip().lower() or None,
        "label": (data.get("label") or "").strip() or None,
        "deadline_at": (data.get("deadline_at") or "").strip() or None,
    }
    # Ack after a clear request: if the model hedges false, still treat as commitment
    # using the request text (label/deadline come from the model when present).
    if ack_gate is True and not result["is_commitment"]:
        result["is_commitment"] = True
        result["commitment_type"] = result["commitment_type"] or "document"
        result["label"] = result["label"] or "Follow through on their request"
    return result


def check_commitment_fulfilled(label: str, message: str) -> bool:
    """Does this new outbound message actually deliver on a previously promised commitment?"""
    user_content = (
        f"What was promised: {label}\n\n"
        f"New message the account owner just sent:\n{message}\n\n"
        "Analyze as JSON:"
    )
    data = _chat_json(_FULFILLMENT_SYSTEM, user_content, max_tokens=40)
    return bool(data.get("fulfilled", False))


def detect_client_commitment(message: str) -> dict[str, Any]:
    """Does this INBOUND message from a client promise the account owner something not
    yet delivered? Client-side counterpart to detect_commitment — see
    WhatsAppCommitment.direction."""
    user_content = (
        f"{_now_context_line()}Message from the client:\n{message}\n\nAnalyze as JSON:"
    )
    data = _chat_json(_CLIENT_COMMITMENT_SYSTEM, user_content, max_tokens=140)
    return {
        "is_commitment": bool(data.get("is_commitment", False)),
        "commitment_type": (data.get("commitment_type") or "").strip().lower() or None,
        "label": (data.get("label") or "").strip() or None,
        "deadline_at": (data.get("deadline_at") or "").strip() or None,
    }


def check_client_commitment_fulfilled(label: str, message: str) -> bool:
    """Does this new inbound message from the client actually deliver on a previously
    promised commitment of theirs?"""
    user_content = (
        f"What the client promised: {label}\n\n"
        f"New message from the client:\n{message}\n\n"
        "Analyze as JSON:"
    )
    data = _chat_json(_CLIENT_FULFILLMENT_SYSTEM, user_content, max_tokens=40)
    return bool(data.get("fulfilled", False))


def draft_commitment_nudge(
    label: str,
    *,
    contact_name: str | None = None,
    instructions: list[str] | None = None,
    voice_examples: list[str] | None = None,
) -> str:
    """A short, sendable nudge for a client who hasn't delivered on their own promise (see
    WhatsAppCommitment.direction == 'client') — used by
    WhatsAppService._check_pending_client_commitments so the reminder comes with an
    editable draft, not just a flag."""
    from app.services.whatsapp import product_gates

    who = product_gates.usable_contact_display_name(contact_name)
    if who:
        user_content = (
            f"{who} previously said: \"{label}\"\n\n"
            "They haven't followed through yet. Write the follow-up message matching "
            "the chat's tone and language:"
        )
    else:
        user_content = (
            f"The client previously said: \"{label}\"\n\n"
            "No real display name is available (only a phone id). "
            "They haven't followed through yet. Write the follow-up opening with a "
            "simple greeting and no name placeholder:"
        )
    system = (
        _CLIENT_NUDGE_SYSTEM
        + _tone_instructions_block(instructions)
        + _voice_examples_block(voice_examples)
    )
    return _chat(system, user_content, max_tokens=120, json_mode=False).strip()


def draft_owner_commitment_message(
    label: str,
    *,
    contact_name: str | None = None,
    instructions: list[str] | None = None,
    voice_examples: list[str] | None = None,
) -> str:
    """Draft the owner can send when reminded about their own open promise
    (WhatsAppCommitment.direction == 'owner').

    Must not claim the promise is already fulfilled — only that the owner will act.
    """
    who = contact_name or "the client"
    user_content = (
        f"You previously told {who} you'd handle: \"{label}\"\n\n"
        "No fulfillment evidence is provided. Draft a short message that you will "
        "fulfill this promise soon — do not claim it is already done:"
    )
    system = (
        _OWNER_COMMITMENT_DRAFT_SYSTEM
        + _tone_instructions_block(instructions)
        + _voice_examples_block(voice_examples)
    )
    return _chat(system, user_content, max_tokens=120, json_mode=False).strip()


def is_english(language: str | None) -> bool:
    if not language:
        return True
    return language.strip().lower() in ("english", "en", "en-us", "en-gb")


_is_english = is_english


def translate_to_english(text: str) -> str:
    """English reading of a draft. Already-English text comes back unchanged."""
    if not text or not text.strip():
        return text
    system = wa_prompts.TRANSLATE_TO_ENGLISH_SYSTEM
    user_content = f"Text:\n{text}\n\nEnglish:"
    return _chat(system, user_content, max_tokens=220, json_mode=False).strip()


def translate_reply_to_language(text: str, target_language: str) -> str:
    """Translate a reply into the contact's target language if needed."""
    if not text or not target_language or is_english(target_language):
        return text
    system = wa_prompts.TRANSLATE_REPLY_SYSTEM
    user_content = f"Target language: {target_language}\n\nText:\n{text}\n\nTranslation:"
    return _chat(system, user_content, max_tokens=220, json_mode=False).strip()


def _chat_json(system: str, user_content: str, *, max_tokens: int) -> dict[str, Any]:
    raw = _chat(system, user_content, max_tokens=max_tokens, json_mode=True)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        logger.warning("[WHATSAPP] Non-JSON model response: %s", raw[:200])
    return {}


def _silent_filter_result(label: str, language: str | None = None) -> dict[str, Any]:
    """Canonical result shape for a silently discarded message (forwarded/spam/group)."""
    return {
        "is_important": False,
        "category": label,
        "lane": "work",
        "priority": "low",
        "confidence": None,
        "payment_status": None,
        "document_type": None,
        "anger_level": None,
        "shipment_status": None,
        "language": language,
        "translation": None,
        "summary": None,
        "decision": wa_decision.closed_decision(label),
    }


def classify_message(
    history: list[dict[str, str]],
    message: str,
    *,
    is_group: bool = False,
    is_forwarded: bool = False,
    user_names: list[str] | None = None,
    corrections: list[dict] | None = None,
    instructions: list[str] | None = None,
    contact_name: str | None = None,
    is_known_sender: bool = True,
    is_personal_contact: bool = False,
) -> dict[str, Any]:
    if is_group:
        system = _group_system(user_names)
    elif is_forwarded:
        system = _forwarded_system()
    else:
        system = _CLASSIFY_SYSTEM

    if corrections:
        system = system + _corrections_block(corrections)
    if instructions:
        system = system + _user_instructions_block(
            instructions,
            contact_name=contact_name,
            is_group=is_group,
            is_known_sender=is_known_sender,
        )
    if is_personal_contact and not is_group:
        system = system + _personal_contact_block()

    context_tags: list[str] = []
    if is_group:
        context_tags.append("This message is from a GROUP chat.")
    if is_forwarded:
        context_tags.append("This message was FORWARDED by the sender.")
    context_line = "\n".join(context_tags) + "\n\n" if context_tags else ""

    user_content = (
        f"{context_line}"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"New client message:\n{message}\n\n"
        "Classify this new message as JSON:"
    )
    data = _chat_json(system, user_content, max_tokens=220)

    detected_language = (data.get("language") or "").strip() or None
    category = str(data.get("category") or "greeting").strip().lower()
    # A genuine safety/distress signal must never be silently dropped — not as spam/forwarded,
    # not as an irrelevant group message, not as an instruction-skip. It always surfaces.
    safety_concern = bool(data.get("safety_concern", False))
    # Deterministic @name / name match only — never trust the LLM's group_relevant
    # or addressed flags for Inbox surfacing (they reopen untagged chatter).
    # A meeting is the model's reading of this message in context, not a phrase list.
    owner_addressed = meeting_scope.message_addresses_owner(message, user_names)

    if instructions and bool(data.get("instruction_skip", False)) and not safety_concern:
        category = "instruction_skip"

    if safety_concern:
        if category in FILTER_LABELS or category not in CATEGORIES:
            category = "other"
    else:
        if category in FILTER_LABELS:
            return _silent_filter_result(category, detected_language)

        # Groups: tag/name only. Untagged call/meet/RFQ chatter stays silent.
        if is_group and not owner_addressed:
            return _silent_filter_result("group", detected_language)

    is_important = bool(data.get("is_important", False))
    if safety_concern:
        is_important = True
    if category in ALWAYS_IMPORTANT and category in CATEGORIES:
        is_important = True
    if category == "meeting" and (not is_group or owner_addressed):
        is_important = True
    if is_group and owner_addressed:
        is_important = True
    if not is_important:
        category = "greeting"
    elif category not in CATEGORIES:
        category = "other"

    payment_status = str(data.get("payment_status") or "").strip().lower() or None
    if category != "payment":
        payment_status = None
    elif payment_status not in ("received", "overdue"):
        payment_status = "overdue"

    document_type = str(data.get("document_type") or "").strip() or None
    if category != "document":
        document_type = None

    anger_level = str(data.get("anger_level") or "").strip().lower() or None
    if category != "complaint":
        anger_level = None
    elif anger_level not in ("low", "medium", "high"):
        anger_level = "high"

    shipment_status = str(data.get("shipment_status") or "").strip().lower() or None
    if category != "shipment":
        shipment_status = None
    elif shipment_status not in ("delayed", "good"):
        shipment_status = "good"

    language = (data.get("language") or "").strip() or None
    translation = (data.get("translation") or "").strip() or None
    if language and language.lower() in ("english", "en", "en-us", "en-gb"):
        translation = None

    priority = base_priority(category) if is_important else "low"
    if is_group and is_important:
        priority = max_priority(priority, "high")

    raw_conf = data.get("confidence")
    try:
        confidence: int | None = max(0, min(100, int(raw_conf)))
    except (TypeError, ValueError):
        confidence = None

    lane = "life" if category in LIFE_LANE_CATEGORIES else "work"

    clarifying_question = (data.get("clarifying_question") or "").strip() or None
    raw_options = data.get("clarifying_options")
    clarifying_options = None
    if isinstance(raw_options, list):
        cleaned = [str(o).strip() for o in raw_options if str(o).strip()][:3]
        if len(cleaned) >= 2:
            clarifying_options = cleaned
    # Fail closed on malformed output: only ever act on this when the model gave us
    # everything a tap-option question actually needs (a question + 2-3 real options).
    # Also never for an unimportant message (greeting/small-talk) — nothing to clarify.
    needs_clarification = bool(
        is_important and data.get("needs_clarification") and clarifying_question and clarifying_options
    )
    if not needs_clarification:
        clarifying_question = None
        clarifying_options = None

    # Deterministic gate: owner-only facts missing from the thread → clarify, never invent.
    from app.services.whatsapp import product_gates

    if (
        is_important
        and not needs_clarification
        and not safety_concern
        and product_gates.should_clarify_missing_owner_fact(message, history)
    ):
        needs_clarification = True
        clarifying_question = clarifying_question or "What should I tell them?"
        clarifying_options = clarifying_options or [
            "Share the date",
            "Still checking",
            "Not sure",
        ]
        if confidence is None or confidence > 60:
            confidence = 40

    decision = wa_decision.build_decision(
        data,
        intent=category,
        is_important=is_important,
        safety_concern=safety_concern,
        needs_clarification=needs_clarification,
        confidence=confidence,
    )

    return {
        "is_important": is_important,
        "category": category,
        "safety_concern": safety_concern,
        "lane": lane,
        "priority": priority,
        "confidence": confidence,
        "payment_status": payment_status,
        "document_type": document_type,
        "anger_level": anger_level,
        "shipment_status": shipment_status,
        "language": language,
        "translation": translation,
        "summary": (data.get("summary") or "").strip() or None,
        "personal_tone": bool(data.get("personal_tone", False)),
        "needs_clarification": needs_clarification,
        "clarifying_question": clarifying_question,
        "clarifying_options": clarifying_options,
        "decision": decision,
    }


def draft_reply(
    history: list[dict[str, str]],
    message: str,
    category: str,
    *,
    context_hint: str | None = None,
    language: str | None = None,
    translation: str | None = None,
    instructions: list[str] | None = None,
    corrections: list[dict] | None = None,
    personal: bool = False,
    voice_examples: list[str] | None = None,
) -> str:
    category_hint = wa_prompts.draft_category_hint(category, context_hint, personal=personal)

    lang_block = _language_context_block(language, translation)
    sender_label = "Their" if personal else "Client's"
    user_content = (
        f"{lang_block}"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"{sender_label} latest message (MATCH THIS LANGUAGE):\n{message}\n\n"
        f"Context: {category_hint}\n\n"
        "Write the suggested reply. Use the earlier messages as context for what this chat is about. "
        "Answer only their latest message. Match the tone, length, and wording of the Me: lines "
        "and of the user's own sent messages:"
    )
    system = (
        (_REPLY_SYSTEM_PERSONAL if personal else _REPLY_SYSTEM)
        + _tone_instructions_block(instructions)
        + _reply_corrections_block(corrections)
        + _voice_examples_block(voice_examples)
    )
    return _chat(system, user_content, max_tokens=160, json_mode=False).strip()


def draft_complaint_reply(
    history: list[dict[str, str]],
    message: str,
    anger_level: str | None = None,
    *,
    language: str | None = None,
    translation: str | None = None,
    instructions: list[str] | None = None,
    corrections: list[dict] | None = None,
    voice_examples: list[str] | None = None,
    context_hint: str | None = None,
) -> str:
    """Draft an empathetic reply to an unhappy client (acknowledge first, then resolve)."""
    tone = wa_prompts.complaint_tone(anger_level)

    lang_block = _language_context_block(language, translation)
    hint_block = f"{context_hint}\n\n" if context_hint else ""
    user_content = (
        f"{lang_block}"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"Client's latest message (MATCH THIS LANGUAGE):\n{message}\n\n"
        f"Tone guidance: {tone}\n\n"
        f"{hint_block}"
        "Write the empathetic reply matching their latest message's language (acknowledge first, then a next step):"
    )
    system = (
        _COMPLAINT_SYSTEM
        + _tone_instructions_block(instructions)
        + _reply_corrections_block(corrections)
        + _voice_examples_block(voice_examples)
    )
    return _chat(system, user_content, max_tokens=180, json_mode=False).strip()


def extract_deadline(message: str) -> dict[str, Any]:
    """Extract a business deadline/delivery date and resolve it to a concrete date,
    for the 'timeline' category — a null date means no concrete date was actually given."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    user_content = (
        f"Current date/time: {now.isoformat()} ({CALENDAR_DEFAULT_TIMEZONE})\n\n"
        f"Message:\n{message}\n\n"
        "Extract the deadline as JSON:"
    )
    data = _chat_json(_DEADLINE_SYSTEM, user_content, max_tokens=100)
    raw_date = (data.get("date") or "").strip() or None
    accepted = stated_deadline_date(message, raw_date)
    return {
        "deadline_label": (data.get("deadline_label") or "").strip() or "Deadline",
        "date": accepted,
        "date_rejected": bool(raw_date and not accepted),
    }


def extract_personal_date(message: str) -> dict[str, Any]:
    """Extract a personal date/event and resolve it to a concrete date."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    user_content = (
        f"Current date/time: {now.isoformat()} ({CALENDAR_DEFAULT_TIMEZONE})\n\n"
        f"Message:\n{message}\n\n"
        "Extract the personal date as JSON:"
    )
    data = _chat_json(_PERSONAL_DATE_SYSTEM, user_content, max_tokens=140)
    return {
        "event_label": (data.get("event_label") or "").strip() or "Personal date",
        "date": (data.get("date") or "").strip() or None,
        "reminder_title": (
            (data.get("reminder_title") or "").strip()
            or f"Remember: {(data.get('event_label') or 'personal date').strip()}"
        ),
        "notes": (data.get("notes") or "").strip() or None,
    }


def extract_personal_task(message: str) -> dict[str, Any]:
    """Extract a personal errand or task description."""
    user_content = (
        f"Message:\n{message}\n\n"
        "Extract the personal task as JSON:"
    )
    data = _chat_json(_PERSONAL_TASK_SYSTEM, user_content, max_tokens=120)
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []
    return {
        "task_summary": (data.get("task_summary") or "").strip() or message.strip()[:120],
        "items": [str(i).strip() for i in items if str(i).strip()],
        "timing_hint": (data.get("timing_hint") or "").strip() or None,
    }


def extract_family_plan(history: list[dict[str, str]], message: str) -> dict[str, Any]:
    """Extract a personal social event from a message + history and resolve the date/time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    user_content = (
        f"Current date/time: {now.isoformat()} ({CALENDAR_DEFAULT_TIMEZONE})\n\n"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"Latest message:\n{message}\n\n"
        "Extract the family plan as JSON:"
    )
    data = _chat_json(_FAMILY_PLAN_SYSTEM, user_content, max_tokens=140)
    return {
        "event_label": (data.get("event_label") or "").strip() or "Family plan",
        "date": (data.get("date") or "").strip() or None,
        "time": (data.get("time") or "").strip() or None,
        "place": (data.get("place") or "").strip() or None,
        "confirmed": bool(data.get("confirmed", False)),
    }


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
    data = _chat_json(_MEETING_SYSTEM, user_content, max_tokens=220)
    return {
        "title": (data.get("title") or "").strip() or "Meeting with client",
        "agenda": (data.get("agenda") or "").strip() or None,
        "location": (data.get("location") or "").strip() or None,
        "start": (data.get("start") or None),
        "end": (data.get("end") or None),
        "confirmed": bool(data.get("confirmed", False)),
        "is_meeting": bool(data.get("is_meeting", True)),
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
    except Exception as exc:
        # Catch-all for anything the openai SDK raises that isn't a RateLimitError/APIError
        # subclass (connection drops, timeouts, etc.) — these must never escape as a bare
        # exception, since callers only know how to handle WhatsAppAIError and an unhandled
        # type here can jam the background classification queue for every contact.
        logger.error("[WHATSAPP] OpenAI call failed unexpectedly: %s", exc)
        raise WhatsAppAIError(str(exc)) from exc

    return (response.choices[0].message.content or "").strip()
