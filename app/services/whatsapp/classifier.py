import json
import logging
from typing import Any

from openai import APIError, RateLimitError

from app.config import (
    CALENDAR_DEFAULT_TIMEZONE,
    OPENAI_MODEL,
    WHATSAPP_CONTEXT_MESSAGE_MAX_CHARS,
)
from app.services.summary.client import get_openai_client

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

_CATEGORY_GUIDE = (
    "- meeting: the client wants to schedule/confirm a call or meeting, asks about availability, "
    "or proposes/confirms a date, time, or location (e.g. 'Let's meet Thursday at 3pm', "
    "'Are you free tomorrow?', 'Confirming our meeting on Friday').\n"
    "- payment: anything about money changing hands — the client says they paid/transferred an "
    "amount, asks you to check/verify a payment, or chases an unpaid/pending/overdue invoice "
    "('Payment done please check', 'Amount transferred', 'Invoice still pending', "
    "'I sent the old invoice can you check', 'When will you pay?'). "
    "If they are chasing an invoice they already sent or asking you to check payment status → "
    "payment (usually overdue). If they only say they paid → payment (received).\n"
    "- lead: a NEW business opportunity from someone who is not yet an active client — a fresh "
    "inquiry, catalogue/price request, sourcing/supplier request, or a referral "
    "('can you share the catalogue?', 'we are looking for a supplier', 'someone referred me', "
    "'what are your bulk prices?').\n"
    "- document: the client is asking us to SEND or SHARE a specific document or file we have not "
    "yet sent — an invoice copy, price list, catalogue, shipping/customs documents, brochure, "
    "report, etc. ('Please send the invoice', 'Share the price list', 'I need the shipping "
    "documents'). Do NOT use document when they are chasing/checking an invoice they already sent — "
    "that is payment. "
    "- complaint: the client is unhappy, frustrated, or reporting a problem — late/undelivered "
    "order, damaged/defective product, poor service, or repeated unanswered follow-ups "
    "('Order not arrived yet', 'This product is damaged', 'This is the third time I am messaging', "
    "'Very unhappy with the service'). A frustrated/angry tone makes it a complaint even if it "
    "mentions a shipment.\n"
    "- shipment: a neutral/factual delivery or logistics status update about an order — customs, "
    "dispatch, transit, delay, or receipt confirmation, WITHOUT an angry tone "
    "('Shipment cleared customs', 'Package delayed by 2 days', 'Goods received in good condition', "
    "'Out for delivery today').\n"
    "- order: the client CONFIRMS placing an order or purchase — a definite commitment to buy, "
    "not just an inquiry ('We'll go ahead with 500 units', 'Confirming the order for the blue "
    "variant', 'Yes, let's book it', 'Please proceed with the order'). Distinguish from 'lead' "
    "(a NEW prospective inquiry, not yet committed to buying) and from 'payment' (money actually "
    "changing hands, not the order commitment itself) and from 'shipment' (delivery status of an "
    "order already placed).\n"
    "- budget: pricing, cost, quotation, or any amount discussion with an EXISTING client that is "
    "not itself a payment notification.\n"
    "- scope: questions about project scope, features, deliverables, requirements, or details.\n"
    "- timeline: deadlines, delivery dates, ETAs, or 'when will it be ready'.\n"
    "- follow_up: the client is waiting for a reply or chasing a pending response/deliverable "
    "('please confirm', 'still waiting for your reply', 'did you get my message?', "
    "'following up on my last message').\n"
    "- personal_date: a PERSONAL message mentioning a date, birthday, anniversary, appointment, "
    "or upcoming personal event that should be remembered "
    "('Mum's birthday is on the 15th', 'Our anniversary is next Friday', "
    "'Dad's surgery is on the 20th', 'School concert is Tuesday'). "
    "Classify this even if there is no direct question — the date itself is the action item.\n"
    "- personal_task: a PERSONAL errand, task, or reminder request — something physical to do or "
    "pick up, a booking, or a domestic reminder "
    "('get Crocin and Vitamin C on the way home', 'pick up the dry cleaning', "
    "'we need milk', 'book a table for Saturday night', 'pay the electricity bill').\n"
    "- family_plan: a PERSONAL social plan or outing being proposed or confirmed — a family dinner, "
    "movie, get-together, trip, or any casual shared activity with family or close friends "
    "('Let's go to dinner Saturday at 8pm', 'Family lunch on Sunday at Mum's place', "
    "'Movie tonight at 7?', 'Are you free Saturday evening?', "
    "'We're all going to the beach Sunday morning'). "
    "Distinguish from 'meeting' which is professional/work. "
    "Classify as family_plan whenever the plan is social/personal, even if it mentions a date and time.\n"
    "- other: any other important client query that does not fit the above."
)


def base_priority(category: str) -> str:
    """Default priority for a category before any time-based escalation."""
    return _CATEGORY_PRIORITY.get(category, "normal")


def max_priority(a: str, b: str) -> str:
    """Return the more urgent of two priority levels."""
    order = {level: i for i, level in enumerate(PRIORITIES)}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def payment_reply_hint(payment_status: str | None) -> str:
    """Drafting context for a payment reply, tailored to received vs overdue."""
    if payment_status == "received":
        return (
            "The client says they have made a payment to us. Thank them, confirm you will verify "
            "and acknowledge once it reflects. Do NOT confirm receipt as a fact yet."
        )
    return (
        "The client is chasing a payment we owe or an unpaid/pending invoice. Acknowledge it, "
        "apologise for any delay, and give a clear next step or timeline. Do NOT invent a date "
        "you cannot honour."
    )


def complaint_priority(anger_level: str | None) -> str:
    """A complaint is always urgent; the angrier the tone, the higher the level."""
    if anger_level == "low":
        return "very_high"
    return "critical"


def shipment_reply_hint(shipment_status: str | None) -> str:
    """Drafting context for a shipment update reply, tailored to delay vs good news."""
    if shipment_status == "delayed":
        return (
            "This is a delivery DELAY. Draft a proactive, reassuring update to send to the end "
            "client BEFORE they have to ask — acknowledge the delay, give the revised "
            "expectation if known, apologise for the inconvenience, and confirm you are tracking "
            "it. Do NOT invent a new delivery date you cannot confirm."
        )
    return (
        "This is a positive/neutral shipment update. Acknowledge it briefly and warmly and let "
        "the client know things are on track / received — closing the loop for this shipment."
    )

_CLASSIFY_SYSTEM = (
    "You triage inbound WhatsApp messages from clients for a freelancer/agency. "
    "SAFETY FIRST — before anything else, check whether the message suggests the sender may be in "
    "danger, distress, or an emergency (e.g. 'I'm in danger', 'help me', threats of harm, a medical "
    "emergency, or anything describing an unsafe situation for themselves or someone else). If so, "
    "set safety_concern=true regardless of category/confidence — a message like this must NEVER "
    "receive a generic auto-drafted reply ('I'll get back to you shortly' or similar), since no "
    "template response is ever appropriate here; it must be shown to the human to handle personally. "
    "Only set safety_concern=true for genuine signs of danger/distress, not for figurative language, "
    "complaints, or business urgency ('this is critical for our launch' is NOT a safety concern). "
    "Detect the message language. Decide whether the message is important and needs action. "
    "Greetings, small-talk, thanks, emojis-only, and acknowledgements are NOT important "
    "(set is_important=false, category=greeting). This also covers casual check-in QUESTIONS "
    "that carry no business content and no concrete task/plan/date — 'where are you', 'what's "
    "the plan today', 'you up?', 'hi bro lets connect' are just as much small-talk as 'hey' is, "
    "even though they're phrased as questions; do not treat 'it's phrased as a question' as a "
    "reason to call something important on its own. Set is_important=false, category=greeting, "
    "and use confidence 80+ for this call whenever the message has no business/client language "
    "(no pricing, deadline, order, deliverable, complaint, or request for a document) and the "
    "conversation history doesn't establish an existing business/client relationship — recognizing "
    "'this has nothing business-actionable in it' does not require being certain about anything "
    "else. Reserve low confidence for genuine ambiguity — e.g. history shows an active work "
    "thread and this could plausibly be a client follow-up on it.\n"
    "Important messages MUST be classified into exactly one category:\n"
    f"{_CATEGORY_GUIDE}\n"
    "IMPORTANT CLASSIFICATION RULES:\n"
    "- Classify by the UNDERLYING INTENT and meaning of the message, NOT by matching keywords. "
    "The quoted phrases above are only illustrative examples — real messages will be worded very "
    "differently, in any language, with typos, slang, abbreviations, voice-to-text errors, or "
    "mixed languages. Infer what the client actually wants.\n"
    "- Always map the message to the SINGLE best-fitting category from the list. Only use 'other' "
    "when an important message genuinely does not fit any specific category — do not use it as a "
    "lazy default.\n"
    "- If a message carries more than one intent, choose the category with the highest stakes/"
    "urgency (a complaint or payment outranks a routine question; an unhappy tone outranks "
    "everything).\n"
    "- Never invent facts; base the category only on what the message and history actually say.\n"
    "- SPAM: if the message is clearly promotional, prize/lottery/offer spam, bulk broadcast, "
    "or sent by an unknown number with no conversation history and no legitimate business intent, "
    "set category='spam' and is_important=false. Never surface spam as an action item.\n"
    "- CLARIFYING QUESTIONS: this is RARE — most messages should NOT trigger it. Only set "
    "needs_clarification=true when the message could concretely mean 2-3 SPECIFIC, materially "
    "different things — e.g. the conversation history already mentions two distinct orders/"
    "shipments/dates/topics, and this message doesn't say which one it's about, so a drafted "
    "reply would genuinely risk answering about the wrong one. Do NOT use it for ordinary wording "
    "uncertainty, a single topic with unclear phrasing, or whenever you'd otherwise just use a "
    "medium confidence score — in those cases classify normally instead. When you do use it: "
    "clarifying_question is ONE short question (under 12 words) naming the real ambiguity, and "
    "clarifying_options is a list of 2-3 SHORT (2-5 word) tap-friendly answers drawn from what's "
    "concretely in the conversation (e.g. 'April shipment', 'May shipment'), optionally ending "
    "with a catch-all like 'Not sure' — never something requiring the user to type. When "
    "needs_clarification is true, set confidence low (this is explicitly the low-confidence case) "
    "and leave clarifying_question/clarifying_options non-null; otherwise both must be null.\n"
    "- LIFE LANE vs WORK LANE: this system has two lanes that feel completely different to the user. "
    "WORK LANE (meeting, payment, lead, document, complaint, shipment, budget, scope, timeline, "
    "follow_up, other) — these produce urgent chips with business tone, draft replies, and "
    "follow-up timers. The tone is professional and action-driven. "
    "LIFE LANE (personal_date, personal_task, family_plan) — these are messages from family or "
    "close friends, not clients. They are GENUINELY IMPORTANT but NEVER URGENT. "
    "They produce gentle reminders, calendar entries, and soft nudges — never red flags, never "
    "business-style drafts, never action-driven language. The tone is always warm, caring, and "
    "human. Priority is ALWAYS low — this is a hard rule with no exceptions. "
    "A message about a family dinner is not less important than a payment message — it simply "
    "belongs in a different world and must never feel like a work task.\n"
    "- LANGUAGE: messages may arrive in ANY language (French, Arabic, Hindi, Spanish, Portuguese, "
    "etc.) or mixed languages. First detect the language, understand the message natively, then "
    "classify it into the SAME categories by intent. If the message is not in English, also "
    "provide a faithful English translation in the 'translation' field; if it is already English, "
    "set translation=null.\n"
    "Use the recent conversation history for context (e.g. a vague 'any update?' is follow_up "
    "on the prior thread; a one-word 'yes' may confirm a meeting). "
    "For category=payment, also set payment_status: 'received' when the client says THEY have "
    "paid/transferred money to us, or 'overdue' when the client is chasing money WE owe or an "
    "unpaid invoice. For any other category set payment_status=null. "
    "For category=document, set document_type to the short name of the requested document "
    "(e.g. 'invoice', 'price list', 'catalogue', 'shipping documents'); otherwise null. "
    "For category=complaint, set anger_level to 'low', 'medium', or 'high' based on the tone "
    "(strong frustration words, ALL CAPS, repeated-follow-up signals like 'third time' raise it); "
    "otherwise null. "
    "For category=shipment, set shipment_status: 'delayed' when it reports a delay/problem with "
    "delivery, or 'good' for positive/neutral progress (cleared customs, out for delivery, goods "
    "received); otherwise null. "
    "Respond ONLY with a JSON object with keys: "
    "is_important (boolean), category (one of "
    f"{', '.join(CATEGORIES)}, or 'greeting'), "
    "safety_concern (boolean — true only for genuine signs the sender is in danger/distress, "
    "per the SAFETY FIRST rule above; false otherwise), "
    "payment_status ('received', 'overdue', or null), "
    "document_type (string or null), anger_level ('low', 'medium', 'high', or null), "
    "shipment_status ('delayed', 'good', or null), "
    "language (the detected language name), translation (English translation or null), "
    "summary (one short sentence describing the ask), "
    "personal_tone (boolean — true if THIS message reads like a casual personal text from "
    "family/a friend: informal wording, slang, no business framing, e.g. 'hey', 'where are you', "
    "'lets connect bro'; false for a professional-sounding message even if brief, e.g. 'Hi, "
    "hope you're well — following up on the quote'), "
    "needs_clarification (boolean — per the CLARIFYING QUESTIONS rule above; false by default), "
    "clarifying_question (string or null — required if needs_clarification is true), "
    "clarifying_options (array of 2-3 short strings, or null — required if needs_clarification "
    "is true), "
    "confidence (integer 0-100 — how certain you are that your category is correct; "
    "only use 80+ when the message unambiguously fits that category; "
    "use lower values when the message is vague, ambiguous, or could fit multiple categories)."
)

def _corrections_block(corrections: list[dict]) -> str:
    """Build a system-level memory block from past user corrections.

    Each correction dict is expected to have:
      - feedback_type (str) — only "wrong" (a genuine category mistake) belongs here;
        "edited" means the category was fine and only the drafted wording was
        corrected, which is a drafting signal (_reply_corrections_block), not a
        classification one — repeating it here would wrongly imply the category itself
        was ever in question.
      - original_category (str)
      - message_snippet (str | None)
      - correct_response (str | None)
    """
    wrong_only = [c for c in corrections if c.get("feedback_type", "wrong") == "wrong"]
    if not wrong_only:
        return ""
    lines = [
        "\n\nCORRECTION MEMORY — the user marked these past classifications as WRONG. "
        "Study them carefully. NEVER repeat the same mistake:\n"
    ]
    for i, c in enumerate(wrong_only, 1):
        snippet = (c.get("message_snippet") or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:120].rstrip() + "..."
        cat = c.get("original_category") or "unknown"
        correct_response = (c.get("correct_response") or "").strip()
        if snippet:
            lines.append(
                f"{i}. Message: \"{snippet}\" → was wrongly classified as \"{cat}\". "
                f"Do not classify similar messages as \"{cat}\" without very high certainty."
            )
        else:
            lines.append(
                f"{i}. A message was wrongly classified as \"{cat}\". "
                f"Be extra careful before assigning \"{cat}\"."
            )
        if correct_response:
            lines.append(f"   The user said the correct reply should have been: \"{correct_response}\"")
    lines.append(
        "\nThese corrections take priority over all other rules. "
        "If in doubt — lower your confidence score rather than guessing."
    )
    return "\n".join(lines)


def _reply_corrections_block(corrections: list[dict] | None) -> str:
    """Corrections that carry an explicit corrected reply — fed into the DRAFTING prompts
    (separate from classification) so the wording/approach of a past mistake is never
    repeated, not just its category."""
    if not corrections:
        return ""
    usable = [c for c in corrections if (c.get("correct_response") or "").strip()]
    if not usable:
        return ""
    lines = [
        "\n\nREPLY CORRECTION MEMORY — the user previously marked a reply as WRONG and told us "
        "what it should have said instead. Match that style/content for similar messages; do not "
        "repeat the wrong approach:\n"
    ]
    for i, c in enumerate(usable, 1):
        snippet = (c.get("message_snippet") or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:120].rstrip() + "..."
        correct_response = c["correct_response"].strip()
        if snippet:
            lines.append(f"{i}. For a message like \"{snippet}\", the correct reply was: \"{correct_response}\"")
        else:
            lines.append(f"{i}. The correct reply in a similar past case was: \"{correct_response}\"")
    lines.append("\nThese take priority over the default tone guidance above.")
    return "\n".join(lines)


def _user_instructions_block(
    instructions: list[str] | None,
    *,
    contact_name: str | None = None,
    is_group: bool = False,
    is_known_sender: bool = True,
) -> str:
    """Standing rules the user configured in Settings — see UserInstruction. These take
    priority over every other rule in this prompt and stay in effect until the user edits
    or deletes them."""
    if not instructions:
        return ""
    lines = [
        "\n\nUSER INSTRUCTIONS — the user set these standing rules in Settings. They OVERRIDE "
        "all other guidance in this prompt and must be followed exactly, every time, until the "
        "user changes them:\n"
    ]
    for i, text in enumerate(instructions, 1):
        lines.append(f"{i}. {text.strip()}")

    context_bits = []
    if contact_name:
        context_bits.append(f"This conversation's saved name is \"{contact_name}\".")
    if is_group:
        context_bits.append("This message is from a GROUP chat.")
    else:
        context_bits.append("This message is a direct (1:1) chat, not a group.")
    context_bits.append(
        "This sender IS a saved contact." if is_known_sender
        else "This sender is NOT a saved contact (an unknown number)."
    )
    lines.append("\nContext for applying the instructions above: " + " ".join(context_bits))
    lines.append(
        "\nIf any instruction above means this message/conversation should not be read, "
        "surfaced, or actioned at all (e.g. 'ignore this group', 'do not read group messages', "
        "'never suggest replies to unknown numbers'), set is_important=false, category='greeting', "
        "and set \"instruction_skip\": true in your JSON response so it is fully suppressed. "
        "Otherwise set \"instruction_skip\": false. Instructions about tone or wording (e.g. "
        "'always reply formally') do not require instruction_skip — they are applied when the "
        "reply is drafted."
    )
    return "\n".join(lines)


def _forwarded_system() -> str:
    return (
        _CLASSIFY_SYSTEM
        + "\n\nFORWARDED MESSAGE: This message was forwarded by the sender rather than written "
        "by them. Apply this rule strictly: if the sender has NOT added their own personal text "
        "(a direct question, request, or comment addressed to you), set category='forwarded' and "
        "is_important=false — ignore the forwarded content entirely. "
        "Only classify and surface the message if there is clear personal text from the sender "
        "themselves in addition to the forwarded content "
        "(e.g. 'Did you see this? Should we do this?' or 'FYI — can you check?'). "
        "In that case, classify by the sender's personal intent, not by the forwarded content."
    )


def _group_system(user_names: list[str] | None) -> str:
    names = ", ".join(user_names) if user_names else "the account owner"
    return (
        _CLASSIFY_SYSTEM
        + "\n\nGROUP CHAT MODE: This message arrived in a WhatsApp GROUP, not a direct chat. "
        "Apply STRICT filtering. Set group_relevant=true ONLY if the message directly names or "
        f"addresses the account owner ({names}) — e.g. '@{names}', 'hey {names}', or a reply/"
        "question clearly directed at them by name. A payment, meeting, or urgent-sounding topic "
        "in the group is NOT enough by itself — if the owner is not actually named/addressed, set "
        "group_relevant=false regardless of how important the topic seems. "
        "For all other group chatter (general discussion, news, banter, messages aimed at other "
        "people, or anything not naming the owner), set group_relevant=false and is_important=false. "
        "Also set addressed=true if the owner is directly named/mentioned, else false — "
        "group_relevant must equal addressed exactly. "
        "Add both keys 'group_relevant' (boolean) and 'addressed' (boolean) to the JSON."
    )


_REPLY_SYSTEM = (
    "You draft a concise, professional WhatsApp reply on behalf of a freelancer/agency to a "
    "client. ALWAYS write the reply in English — even when the client's message is in another "
    "language. Be warm but to the point. "
    "Do not invent facts, prices, or commitments; if information is needed, ask for it or say "
    "you will confirm shortly. Return ONLY the reply text in English, no preamble."
)

_REPLY_SYSTEM_PERSONAL = (
    "You draft a short WhatsApp reply to a family member or close friend, in the user's own "
    "casual voice — this is NOT a business or client message, so do not use any client-service "
    "phrasing ('thank you for reaching out', 'lovely to hear from you', 'I appreciate your "
    "message', etc.). Reply the way a real person quickly texts someone they know back — brief, "
    "warm, plain, everyday language. ALWAYS write in English even if their message is in another "
    "language. Return ONLY the reply text, no preamble."
)

_COMPLAINT_SYSTEM = (
    "You draft a WhatsApp reply to an UNHAPPY client on behalf of a freelancer/agency. "
    "ALWAYS write the reply in English — even when the client's message is in another language. "
    "You MUST: "
    "(1) acknowledge and empathise with the problem FIRST and take it seriously; "
    "(2) apologise sincerely for the inconvenience; "
    "(3) THEN offer a concrete next step or ask for the detail you need to resolve it. "
    "Never be dismissive, defensive, or blame the client. Do not invent facts, refunds, or "
    "commitments you cannot keep. Keep it calm, human, and reassuring. "
    "Return ONLY the reply text in English, no preamble."
)

_FINALISATION_SIGNALS = (
    "'Confirmed', 'Sounds good', 'See you then', 'Done', 'Fixed', 'Ok 3pm works', "
    "'Yes let's do it', 'Perfect see you Thursday'"
)

_MEETING_SYSTEM = (
    "You extract meeting details from a client's WhatsApp message and conversation history. "
    "Use the provided current date/time to resolve relative phrases like 'tomorrow', 'next Monday', "
    "or 'at 3pm' into concrete ISO 8601 datetimes in the given timezone. "
    "NEVER use the current date/time as a stand-in for a time the client did not actually give. "
    "A vague reference like 'today' or 'this week' is a DATE hint at most, not a time — do not "
    "invent a clock time for it. Only resolve 'now'/'right now' to the current time when the "
    "client is clearly asking to connect immediately (e.g. 'call me now', 'can we talk right now?'); "
    "a short confirmation like 'yes connect today' or 'sounds good' is agreeing to meet, not "
    "stating a specific time — if no explicit time is given anywhere in the message or the recent "
    "conversation, set start and end to null and confirmed=false, even though the client agreed "
    "to meet in principle. "
    "MUTUAL CONFIRMATION RULE: the conversation history lines are labelled 'Client:' for the other "
    "person and 'Me:' for the account owner. Finalisation signals — short closing phrases such as "
    f"{_FINALISATION_SIGNALS} — mean a plan is agreed only once BOTH sides have shown clear "
    "agreement to the SAME date/time (one side proposing a time, and the other side accepting it "
    "with a phrase like these, in either order, counts as both sides). A time stated or proposed by "
    "only ONE side — even if worded confidently or definitively — is NOT confirmed if the other "
    "side has not also agreed to it anywhere in the message or history. Set confirmed=true ONLY "
    "when this mutual agreement is clearly present; otherwise confirmed=false, no exceptions. "
    "Respond ONLY with a JSON object with keys: title (short meeting title), agenda (1-3 lines), "
    "location (place, video link, or phone if mentioned, else null), "
    "start (ISO 8601 datetime with timezone offset if resolved, else null), "
    "end (ISO 8601 datetime if given or inferable, else null), "
    "confirmed (true only per the MUTUAL CONFIRMATION RULE above; false if the plan is tentative, "
    "proposed, one-sided, or still being negotiated such as 'are you free?' or 'maybe Friday?'). "
    "If the client wants to meet but gives no specific time, set start and end to null."
)


_COMMITMENT_SYSTEM = (
    "You analyze a business message the ACCOUNT OWNER just sent to a client on WhatsApp. "
    "Decide whether this message makes a NEW commitment to send or do something for the "
    "client that is NOT already done in the message itself — e.g. 'I'll send you the price "
    "list', 'Let me get you the details', 'I will share the invoice shortly', 'I'll confirm "
    "and send the documents tomorrow'. "
    "Do NOT treat it as a commitment if the message already delivers the thing right there "
    "(e.g. it contains the actual price/quote, or is just answering a question with the "
    "answer included) — only a promise of FUTURE action counts. Small talk, greetings, and "
    "messages with no promise at all are never commitments. "
    "Respond ONLY with a JSON object with keys: "
    "is_commitment (boolean), "
    "commitment_type ('pricing', 'document', 'other', or null — null if is_commitment is "
    "false; 'pricing' for a quote/cost/budget promise, 'document' for a file/invoice/"
    "catalogue/report promise, 'other' for anything else), "
    "label (a short description of what was promised, e.g. 'Send price list', 'Share the "
    "invoice', or null if is_commitment is false)."
)


def detect_commitment(message: str) -> dict[str, Any]:
    """Does this outbound message promise the client something not yet delivered?"""
    user_content = f"Message the account owner just sent:\n{message}\n\nAnalyze as JSON:"
    data = _chat_json(_COMMITMENT_SYSTEM, user_content, max_tokens=100)
    return {
        "is_commitment": bool(data.get("is_commitment", False)),
        "commitment_type": (data.get("commitment_type") or "").strip().lower() or None,
        "label": (data.get("label") or "").strip() or None,
    }


_FULFILLMENT_SYSTEM = (
    "The account owner previously promised something to a client on WhatsApp. They just "
    "sent this client a NEW message. Decide whether the new message actually fulfills that "
    "promise — e.g. it contains the price/quote, references an attached document, or "
    "explicitly confirms it was sent/shared. A message that just chats about something "
    "else, or repeats another vague 'I'll send it soon', does NOT fulfill it. "
    "Respond ONLY with a JSON object with key: fulfilled (boolean)."
)


def check_commitment_fulfilled(label: str, message: str) -> bool:
    """Does this new outbound message actually deliver on a previously promised commitment?"""
    user_content = (
        f"What was promised: {label}\n\n"
        f"New message the account owner just sent:\n{message}\n\n"
        "Analyze as JSON:"
    )
    data = _chat_json(_FULFILLMENT_SYSTEM, user_content, max_tokens=40)
    return bool(data.get("fulfilled", False))


def is_english(language: str | None) -> bool:
    if not language:
        return True
    return language.strip().lower() in ("english", "en", "en-us", "en-gb")


_is_english = is_english


def translate_reply_to_language(text: str, target_language: str) -> str:
    """Translate an English reply into the contact's own detected language before sending —
    the user always writes in English, the contact always receives their own language."""
    system = (
        "You translate a WhatsApp business reply from English into another language for sending "
        "to a client. Keep the tone, meaning, names, numbers, and links exactly as given — do not "
        "add, remove, or explain anything. Respond with ONLY the translated text, no preamble, "
        "no quotes, no English."
    )
    user_content = f"Target language: {target_language}\n\nEnglish text:\n{text}\n\nTranslation:"
    return _chat(system, user_content, max_tokens=220, json_mode=False).strip()


def _language_context_block(language: str | None, translation: str | None) -> str:
    """Give the drafter English context when the inbound message is not in English."""
    if _is_english(language):
        return ""
    lines: list[str] = []
    if language:
        lines.append(f"The client's message is in {language}.")
    if translation:
        lines.append(f"English meaning: {translation}")
    else:
        lines.append(
            "Understand the client's message (translate mentally if needed) and write your reply "
            "in English."
        )
    return "\n".join(lines) + "\n\n"


def _history_block(history: list[dict[str, str]]) -> str:
    if not history:
        return "(no prior messages)"
    lines = []
    for item in history:
        who = "Client" if item.get("direction") == "inbound" else "Me"
        text = (item.get("body") or "").strip()
        if len(text) > WHATSAPP_CONTEXT_MESSAGE_MAX_CHARS:
            text = text[:WHATSAPP_CONTEXT_MESSAGE_MAX_CHARS].rstrip() + "..."
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
    }


def _personal_contact_block() -> str:
    """This contact has already been established as family/a close personal contact
    (from a prior message that clearly classified as personal_date/personal_task/
    family_plan) — not a client. Vague personal chit-chat from them should not be
    forced into a business category just because it doesn't cleanly fit."""
    return (
        "\n\nPERSONAL CONTACT — this sender is a known family member or close friend, "
        "not a business client. Casual check-ins with no concrete plan/task/date "
        "('hey', 'where are you', 'what's the plan today', 'you up?') are is_important=false, "
        "category='greeting' — do NOT force these into a WORK category (follow_up, other, etc.) "
        "just because they don't cleanly fit personal_date/personal_task/family_plan. Only use "
        "personal_task/family_plan/personal_date when the message states an actual task, plan, "
        "or date. Because you know this is a personal contact, you have strong context for these "
        "calls — use confidence 80+ for a clear greeting/personal_task/family_plan read here, "
        "rather than hedging low just because the message is short."
    )


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

    if instructions and bool(data.get("instruction_skip", False)) and not safety_concern:
        category = "instruction_skip"

    if safety_concern:
        if category in FILTER_LABELS or category not in CATEGORIES:
            category = "other"
    else:
        if category in FILTER_LABELS:
            return _silent_filter_result(category, detected_language)

        if is_group and not bool(data.get("group_relevant", False)):
            return _silent_filter_result("group", detected_language)

    is_important = bool(data.get("is_important", False))
    if safety_concern:
        is_important = True
    if category in ALWAYS_IMPORTANT and category in CATEGORIES:
        is_important = True
    if is_group:
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
    }


def _tone_instructions_block(instructions: list[str] | None) -> str:
    """Standing user rules that affect how a reply is worded (e.g. 'always reply formally')."""
    if not instructions:
        return ""
    lines = ["\n\nUSER INSTRUCTIONS — standing rules set by the user in Settings. Follow them "
             "exactly, even if they conflict with the default tone above:\n"]
    for i, text in enumerate(instructions, 1):
        lines.append(f"{i}. {text.strip()}")
    return "\n".join(lines)


def _voice_examples_block(examples: list[str] | None) -> str:
    """Real messages the user actually sent (see repo.recent_outbound_examples) — the
    goal is for drafts to sound like the user wrote them, not like an AI. Skipped below
    a minimum sample size to avoid overfitting a whole voice to one message."""
    if not examples or len(examples) < 2:
        return ""
    lines = [
        "\n\nTHE USER'S OWN VOICE — recent messages the user actually wrote and sent "
        "themselves (not AI drafts). Match this tone, length, formality, language mix, "
        "and sign-off style as closely as the situation allows — the goal is for your "
        "draft to read like the user wrote it personally, not like a generic AI reply:\n"
    ]
    for i, text in enumerate(examples, 1):
        snippet = text.strip()
        if len(snippet) > 200:
            snippet = snippet[:200].rstrip() + "..."
        lines.append(f"{i}. \"{snippet}\"")
    return "\n".join(lines)


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
    if personal and context_hint is None and category == "greeting":
        context_hint = (
            "This is a casual message from a family member/close friend — reply the way you'd "
            "actually text them back. Short, plain, natural. No 'thank you for reaching out' or "
            "similar client-service phrasing — just reply like a person."
        )
    category_hint = context_hint or {
        "budget": "The client is asking about budget/pricing. Acknowledge and indicate next step.",
        "scope": "The client is asking about scope/requirements. Clarify or confirm details.",
        "timeline": "The client is asking about timeline. Confirm or propose dates.",
        "follow_up": "The client is following up. Acknowledge and give a status or next step.",
        "other": "Respond helpfully to the client's query.",
        "meeting": "The client wants to meet. Acknowledge and confirm a time or propose options.",
        "lead": (
            "This is a NEW lead/inquiry. Reply professionally and warmly as a first impression, "
            "thank them for reaching out, briefly invite the key detail you need (e.g. quantity, "
            "requirement) and signal you can share a catalogue/quote."
        ),
        "payment": "The client's message is about a payment. Acknowledge it clearly.",
        "document": (
            "The client has requested a specific document/information. Acknowledge the request "
            "and confirm you will send it shortly; if anything is needed to send it, ask briefly."
        ),
        "shipment": (
            "This is a shipment/delivery update. Acknowledge it appropriately and keep the client "
            "informed."
        ),
        "order": (
            "The client has confirmed an order/purchase. Reply warmly, confirm you're proceeding, "
            "and mention next steps (processing, production, or timeline) if appropriate — do not "
            "invent a specific date you can't commit to."
        ),
        "greeting": (
            "This is a casual/greeting message — there is no task to action. Reply warmly and "
            "briefly in a friendly tone that matches the client's message (including festival or "
            "well-wishing greetings). Keep it to one or two short sentences."
        ),
    }.get(category, "Respond helpfully to the client's query.")

    lang_block = _language_context_block(language, translation)
    sender_label = "Their" if personal else "Client's"
    user_content = (
        f"{lang_block}"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"{sender_label} latest message:\n{message}\n\n"
        f"Context: {category_hint}\n\n"
        "Write the reply text in English:"
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
) -> str:
    """Draft an empathetic reply to an unhappy client (acknowledge first, then resolve)."""
    tone = {
        "high": "The client is very angry. Lead with sincere empathy and a clear apology.",
        "medium": "The client is frustrated. Acknowledge the frustration and apologise.",
        "low": "The client is mildly unhappy. Acknowledge the issue warmly.",
    }.get(anger_level or "high", "Acknowledge the issue and apologise sincerely.")

    lang_block = _language_context_block(language, translation)
    user_content = (
        f"{lang_block}"
        f"Recent conversation:\n{_history_block(history)}\n\n"
        f"Client's latest message:\n{message}\n\n"
        f"Tone guidance: {tone}\n\n"
        "Write the empathetic reply text in English (acknowledge the problem first, then a next step):"
    )
    system = (
        _COMPLAINT_SYSTEM
        + _tone_instructions_block(instructions)
        + _reply_corrections_block(corrections)
        + _voice_examples_block(voice_examples)
    )
    return _chat(system, user_content, max_tokens=180, json_mode=False).strip()


_PERSONAL_DATE_SYSTEM = (
    "You extract a personal date or upcoming event from a WhatsApp message. "
    "Use the provided current date/time to resolve relative phrases like 'next Friday', "
    "'on the 15th', 'tomorrow', 'in two weeks' into concrete ISO 8601 dates in the given timezone. "
    "Respond ONLY with a JSON object with keys: "
    "event_label (short description of what the date is for, e.g. 'Mum birthday', "
    "'Wedding anniversary', 'Dad surgery', 'School concert'), "
    "date (ISO 8601 date string YYYY-MM-DD if resolved, else null), "
    "reminder_title (a natural calendar event title, e.g. 'Remember: Mum birthday'), "
    "notes (any extra context from the message, or null)."
)

_DEADLINE_SYSTEM = (
    "You extract a business deadline or delivery date from a client's WhatsApp message. "
    "Use the provided current date/time to resolve relative phrases like 'by Friday', "
    "'end of month', 'in 3 days', 'before the 20th' into a concrete ISO 8601 date in the "
    "given timezone. If the message only asks a vague question with no date stated or "
    "implied ('when will it be ready?', 'what's the timeline?'), set date to null — do "
    "NOT invent one just because the category is about a deadline. "
    "Respond ONLY with a JSON object with keys: "
    "deadline_label (short description, e.g. 'Delivery deadline', 'Report due date', "
    "'Requested ETA'), "
    "date (ISO 8601 date string YYYY-MM-DD if a concrete date is given/resolvable, else null)."
)

_PERSONAL_TASK_SYSTEM = (
    "You extract a personal errand, task, or to-do from a WhatsApp message. "
    "Respond ONLY with a JSON object with keys: "
    "task_summary (concise one-line description of what needs to be done, "
    "e.g. 'Buy Crocin and Vitamin C on the way home', 'Pick up dry cleaning', "
    "'Book table for Saturday night'), "
    "items (list of specific items/things if the task involves picking something up, else []), "
    "timing_hint (any timing context like 'on the way home', 'before Saturday', or null)."
)


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
    return {
        "deadline_label": (data.get("deadline_label") or "").strip() or "Deadline",
        "date": (data.get("date") or "").strip() or None,
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


_FAMILY_PLAN_SYSTEM = (
    "You extract a personal social plan or family outing from a WhatsApp message and its "
    "conversation history. "
    "Use the provided current date/time to resolve relative phrases like 'tonight', 'Saturday', "
    "'next Sunday', 'this weekend' into concrete ISO 8601 dates in the given timezone. "
    "MUTUAL CONFIRMATION RULE: history lines are labelled 'Client:' for the other person and "
    "'Me:' for the account owner. Finalisation signals — short closing phrases such as "
    f"{_FINALISATION_SIGNALS} — mean a plan is agreed only once BOTH sides have shown clear "
    "agreement to the SAME date/time/place (one side proposing, the other accepting with a phrase "
    "like these, in either order, counts as both sides). A plan mentioned or proposed by only ONE "
    "side is NOT confirmed, even if stated definitively, unless the other side has also agreed to "
    "it somewhere in the message or history. Set confirmed=true ONLY when this mutual agreement is "
    "clearly present; otherwise confirmed=false, no exceptions. "
    "Respond ONLY with a JSON object with keys: "
    "event_label (short human-readable title for the event, e.g. 'Family dinner', "
    "'Movie night', 'Sunday lunch at Mum's'), "
    "date (ISO 8601 date string YYYY-MM-DD if resolved, else null), "
    "time (HH:MM 24-hour string if mentioned, else null), "
    "place (venue or location if mentioned, else null), "
    "confirmed (true only per the MUTUAL CONFIRMATION RULE above; false if the plan is still a "
    "one-sided proposal or question)."
)


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
