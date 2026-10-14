"""WhatsApp model prompt text.

Phase 1 collects the existing prompt strings here. Wording is unchanged.
Callers still decide when to use each prompt.
"""

from app.config import CALENDAR_DEFAULT_TIMEZONE, WHATSAPP_CONTEXT_MESSAGE_MAX_CHARS


def _is_english(language: str | None) -> bool:
    if not language:
        return True
    return language.strip().lower() in ("english", "en", "en-us", "en-gb")


_CATEGORY_GUIDE = (
    "- meeting: the latest message, read with the chat around it, is about arranging or agreeing "
    "a call or meeting — asking availability, proposing a time or place, or agreeing to that plan "
    "('Let's meet Thursday at 3pm', 'Are you free tomorrow?', 'can we connect at 5pm?', "
    "'Confirming our meeting on Friday', 'let's schedule a call next week'). "
    "Do NOT use meeting for: (1) product/plan/package choice questions with no call request "
    "('which plan — group or one on one?', 'did you decide what plan to opt for?'); "
    "(2) immediate 'connect/talk/call NOW / right now / can connect now' — already in attention, "
    "not a schedulable Inbox plan; (3) short delay replies like 'allow me 1 hour' with no meet ask; "
    "(4) a price, order, payment, or delivery confirmation; (5) one person saying they will call "
    "back later, with no time being arranged. A single word such as call, connect, or confirm "
    "does not make the message a meeting.\n"
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
    "- timeline: the sender wants to know WHEN work, a delivery, or a promised task will be "
    "finished or ready — any language or wording, not only an explicit date.\n"
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
        "you cannot honour. If the conversation history shows we already told them we'd check "
        "and get back to them (or gave any other status), this message is the client repeating "
        "themselves — do not just say 'let me check' again; give a real update, name what's "
        "taking longer, or ask what specifically they still need."
    )



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


def _render_classify_system(categories) -> str:
    return (
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
        "that carry no task and no request to talk — 'where are you', 'what's the plan today', "
        "'you up?' are just as much small-talk as 'hey' is, even though they're phrased "
        "as questions; do not treat 'it's phrased as a question' as a reason to call something "
        "important on its own. Set is_important=false, category=greeting, and use confidence 80+ "
        "for this call whenever the message has no business/client ask (no pricing, deadline, order, "
        "deliverable, complaint, document request, question about when work will be finished, "
        "or request to call, meet, or connect) "
        "and the conversation history doesn't establish an existing business/client relationship — "
        "recognizing 'this has nothing business-actionable in it' does not require being certain "
        "about anything else. Reserve low confidence for genuine ambiguity — e.g. history shows an "
        "active work thread and this could plausibly be a client follow-up on it.\n"
        "CALL AND MEETING REQUESTS ARE IMPORTANT in any language, script, slang, or spelling. "
        "Read the latest message together with the conversation around it, and decide from that "
        "meaning. Set is_important=true and category=meeting only when the latest message is "
        "asking to arrange a call or meeting, or is agreeing to one. Do not mark that as greeting "
        "because it starts with a hello, is short, or does not use a particular English phrase. "
        "A greeting with no request to talk stays greeting. Do not use meeting because an earlier "
        "message mentioned a call, or because this message contains a word such as call, connect, "
        "or confirm while its real intent is a price, an order, a payment, a delivery, or a promise "
        "to call back later with no plan to schedule. Those stay in their own category.\n"
        "STATUS AND DEADLINE QUESTIONS ARE IMPORTANT in any language, script, slang, or spelling. "
        "If the sender is asking when work, a delivery, an order, or something already promised "
        "will be finished or ready, set is_important=true. Use category=timeline when they want a "
        "time, or category=follow_up when they are chasing a reply or deliverable already in the "
        "thread. Do not mark that as greeting just because the message is short, informal, or does "
        "not use English words like deadline, order, or deliverable. Social check-ins with no task "
        "behind them stay greeting.\n"
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
        "- CLARIFYING QUESTIONS: set needs_clarification=true in TWO cases only: "
        "(A) Ambiguous referent — the message could mean 2-3 SPECIFIC, materially different things "
        "already in history (e.g. two distinct orders/shipments), so a draft would risk answering "
        "the wrong one. "
        "(B) Missing owner-only fact — the client asks for a date, price, stock ETA, invoice number, "
        "or similar private detail that is NOT present anywhere in the conversation history "
        "(e.g. 'When will the stock arrive?' with no arrival date in the thread). In that case do "
        "NOT invent the fact; ask the owner first. "
        "Do NOT use clarification for ordinary wording uncertainty, greetings, or when the needed "
        "fact IS already in the thread (then draft normally using that fact). When you do use it: "
        "clarifying_question is ONE short question (under 12 words), and clarifying_options is a "
        "list of 2-3 SHORT (2-5 word) tap-friendly answers (e.g. 'Friday', 'Next week', 'Not sure') "
        "— never something requiring the user to type free-form. When needs_clarification is true, "
        "set confidence low and leave clarifying_question/clarifying_options non-null; otherwise "
        "both must be null.\n"
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
        "classify it into the SAME categories by intent. If the message is Hindi words written in "
        "Roman/Latin script, mixed with English (e.g. 'kal meeting hai, confirm kar do'), set "
        "language='Hinglish' — NOT 'Hindi'. Only use 'Hindi' when the message is actually written in "
        "Devanagari script. If the message is not in English, also "
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
        f"{', '.join(categories)}, or 'greeting'), "
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


def install_categories(categories) -> None:
    """Fill the classify prompt from the live category list."""
    global _CLASSIFY_SYSTEM
    _CLASSIFY_SYSTEM = _render_classify_system(tuple(categories))


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

    The app's only correction UI is the "Wrong" button, and it always asks "what should
    the correct response have been" — it never asks the user to name a different
    category. So every entry it produces carries a correct_response and is really a
    wording complaint about the DRAFT, not a claim that original_category was mistaken.
    Treating those as category-mistake evidence here previously taught the classifier to
    second-guess and lower confidence on that category for every future message,
    cascading into "not important" -> forced to "greeting" (see classify_message) even
    for messages that were genuinely e.g. payment. Only a bare "wrong" with no
    correct_response — which would have to come from some other, non-UI caller — is
    actual category-mistake signal.
    """
    wrong_only = [
        c
        for c in corrections
        if c.get("feedback_type", "wrong") == "wrong" and not (c.get("correct_response") or "").strip()
    ]
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
        "repeat the wrong approach. Listed MOST RECENT FIRST (#1 is the newest) — if entries "
        "conflict in tone or approach, the user changed their mind since the older one; always "
        "follow the lower-numbered (more recent) entry, not an earlier one it contradicts:\n"
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
    lines.append(
        "\nThese take priority over the default tone guidance above, and #1 takes priority "
        "over any other numbered entry it conflicts with."
    )
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
        "Apply STRICT tag-only filtering. Set group_relevant=true ONLY if the message "
        f"directly tags or names the account owner ({names}) — e.g. '@{names}', "
        f"'hey {names}', or a question clearly directed at them by name/mention. "
        "Call/meeting asks, payments, RFQs, and other topics WITHOUT naming/tagging the "
        "owner are NOT enough — set group_relevant=false and is_important=false. "
        "For all other group chatter (general discussion, news, banter, messages aimed at "
        "other people), set group_relevant=false and is_important=false. "
        "Also set addressed=true only when the owner is directly named/mentioned; "
        "group_relevant must equal addressed exactly. "
        "Add both keys 'group_relevant' (boolean) and 'addressed' (boolean) to the JSON."
    )


_REPLY_SYSTEM = (
    "You draft a WhatsApp reply on behalf of the account owner. "
    "ADAPTIVE PER-CONTACT TONE & LANGUAGE RULES:\n"
    "1. HARD LANGUAGE MATCH (highest priority): Match the language/script of the OTHER "
    "PERSON'S LATEST message (the 'Client:' / latest inbound line), not your own past 'Me:' lines. "
    "If their latest message is English, reply in English — even if older history is Hinglish. "
    "If their latest message is Roman Hinglish (e.g. 'aaj meet kre?', 'nahi aaj nahi'), "
    "reply in natural Roman Hinglish (Latin script — do NOT use Devanagari). "
    "If their latest message is Devanagari Hindi, reply in Hindi. "
    "Short acks like 'Ok'/'Yes' in English → keep the reply in English unless the rest of "
    "that same message is clearly Hinglish.\n"
    "2. RELATIONSHIP & TONE MATCHING: Analyze the 'Me:' lines in the recent conversation. "
    "If 'Me:' speaks informally, uses short punchy texts (e.g. 3-6 words), or casual phrasing with this person, match that exact casual brevity. "
    "If 'Me:' speaks formally with a corporate client, match that professional tone. "
    "If there are no past 'Me:' messages yet, mirror the contact's own level of formality, brevity, and language style.\n"
    "3. CONCISE & NATURAL: WhatsApp messages are conversational. Avoid corporate customer-support fluff (do NOT say 'Thank you for reaching out' or 'I hope this message finds you well' unless the user writes like that).\n"
    "4. ACCURACY (HARD): Never invent dates, prices, stock availability, ETAs, invoice numbers, "
    "quantities, or other business facts. Only use details that appear in the conversation "
    "history or the user's clarifying answer. If a needed fact is missing, do not guess — "
    "ask a short clarifying question or say you will confirm (without inventing the fact).\n"
    "5. NO UNNECESSARY REPETITION: Check the 'Me:' lines in the recent conversation before writing: if you already said this same thing (already promised to check and get back, already gave this exact update), do NOT repeat it near-verbatim. Treat a repeated message as a follow-up nudge — give a fresh status or acknowledge you're still on it.\n"
    "Return ONLY the plain reply text, no preamble, no quotes."
)

_REPLY_SYSTEM_PERSONAL = (
    "You draft a short WhatsApp reply to a family member or close friend in the user's own casual voice. "
    "This is NOT a business or client message — never use any customer-service phrasing ('thank you for reaching out', etc.). "
    "HARD RULE: match the language of THEIR latest message (English→English, Roman Hinglish→Roman Hinglish). "
    "Reply the way a real person quickly texts someone close back — brief, warm, plain, everyday language. "
    "Return ONLY the reply text, no preamble, no quotes."
)

_COMPLAINT_SYSTEM = (
    "You draft an empathetic WhatsApp reply to an UNHAPPY client on behalf of the account owner. "
    "HARD RULE: match the language of THEIR latest message (English→English, Roman Hinglish→Roman Hinglish). "
    "You MUST: "
    "(1) acknowledge and empathise with the problem FIRST and take it seriously; "
    "(2) apologise sincerely for the inconvenience; "
    "(3) THEN offer a concrete next step or ask for the detail you need to resolve it. "
    "Never be dismissive, defensive, or blame the client. Do not invent facts, refunds, or commitments you cannot keep. "
    "Keep it calm, human, and reassuring. "
    "Return ONLY the reply text, no preamble, no quotes."
)

_FINALISATION_SIGNALS = (
    "'Confirmed', 'Sounds good', 'See you then', 'Done', 'Fixed', 'Ok 3pm works', "
    "'Yes let's do it', 'Perfect see you Thursday'"
)

_MEETING_SYSTEM = (
    "You extract meeting details from a client's WhatsApp message and conversation history. "
    "Use the provided current date/time to resolve relative phrases like 'tomorrow', 'next Monday', "
    "or 'at 3pm' into concrete ISO 8601 datetimes in the given timezone. "
    "CRITICAL TIMEZONE RULE: the account owner operates in the timezone given in the current "
    "date/time line (e.g. Asia/Kolkata / IST). Bare clock times like '4 pm', '4:30pm', or "
    "'lets connect at 4' ALWAYS mean that timezone — never UTC, never the contact's unknown "
    "timezone, never US Pacific/Eastern. Emit start/end with the correct numeric offset for "
    "that timezone (e.g. +05:30 for Asia/Kolkata). "
    "NEVER use the current date/time as a stand-in for a time the client did not actually give. "
    "A vague reference like 'today' or 'this week' is a DATE hint at most, not a time — do not "
    "invent a clock time for it. Only resolve 'now'/'right now' to the current time when the "
    "client is clearly asking to connect immediately (e.g. 'call me now', 'can we talk right now?'); "
    "a short confirmation like 'yes connect today' or 'sounds good' is agreeing to meet, not "
    "stating a specific time — if no explicit time is given anywhere in the message or the recent "
    "conversation, set start and end to null and confirmed=false, even though the client agreed "
    "to meet in principle. "
    "If the message gives a clock time but no day ('at 4 pm'), use today's date in the account "
    "timezone when that time is still ahead; otherwise use tomorrow. "
    "If multiple different times appear in the history, ALWAYS use the LATEST time that was "
    "proposed or agreed — never an earlier superseded time (e.g. if they first said 5pm and "
    "later said 6pm, use 6pm). "
    "MUTUAL CONFIRMATION RULE: the conversation history lines are labelled 'Client:' for the other "
    "person and 'Me:' for the account owner. Finalisation signals — short closing phrases such as "
    f"{_FINALISATION_SIGNALS}, plus 'ok sure', 'lets connect', 'ok sure lets connect', 'sure lets "
    "connect' — mean a plan is agreed only once BOTH sides have shown clear "
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
    "proposed, one-sided, or still being negotiated such as 'are you free?' or 'maybe Friday?'), "
    "is_meeting (true only when the latest message, read with the conversation, is arranging or "
    "agreeing a call or meeting). "
    "If the client wants to meet but gives no specific time, set start and end to null. "
    "If the latest message is a price, order, payment, or delivery confirmation, or one person "
    "saying they will call back later with no time being set, set is_meeting=false, "
    "confirmed=false, and start and end to null. Do not turn an earlier 'call' or 'connect' "
    "in the history into a meeting for this message."
)


_DEADLINE_HINT_INSTRUCTION = (
    "Use the given current date/time to resolve any timeframe mentioned ('in 2 hours', "
    "'by tomorrow', 'by Friday', 'end of day') into a concrete ISO 8601 datetime in the "
    "given timezone. If no timeframe is mentioned at all, leave it null — do not invent one."
)


def _now_context_line() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(CALENDAR_DEFAULT_TIMEZONE))
    return f"Current date/time: {now.isoformat()} ({CALENDAR_DEFAULT_TIMEZONE})\n\n"



_COMMITMENT_SYSTEM = (
    "You analyze a business message the ACCOUNT OWNER just sent to a client on WhatsApp, "
    "using the recent conversation (especially the client's last inbound request). "
    "Decide whether this outbound message makes a NEW commitment to send or do something "
    "that is NOT already done in the message itself. "
    "Examples that ARE commitments: "
    "'I'll send the documents tomorrow', 'Will call you on Monday', 'Kal bhejta hoon', "
    "'Let me check and revert', 'I will share the invoice shortly'. "
    "ACK-AFTER-REQUEST: a short acknowledgement like 'Ok', 'Sure', 'Haan', 'Done' IS a "
    "commitment ONLY when the immediately prior client message clearly asked the owner to "
    "do something (e.g. client: 'Please send the COA by Friday' → owner: 'Ok' → commitment "
    "to send the COA by Friday). An isolated 'Ok' after small talk ('How are you?' → 'Ok') "
    "is NOT a commitment. "
    "Do NOT treat it as a commitment if the message already delivers the thing, is only "
    "answering a question with the answer included, or is greeting/small-talk with no promise. "
    f"{_DEADLINE_HINT_INSTRUCTION} "
    "When the commitment comes from accepting a client request that named a deadline "
    "('by Friday'), use that deadline. "
    "Respond ONLY with a JSON object with keys: "
    "is_commitment (boolean), "
    "commitment_type ('pricing', 'document', 'other', or null — null if is_commitment is "
    "false; 'pricing' for a quote/cost/budget promise, 'document' for a file/invoice/"
    "catalogue/report/COA promise, 'other' for anything else), "
    "label (a short description of what was promised, e.g. 'Send documents', 'Send COA', "
    "'Check and revert', or null if is_commitment is false), "
    "deadline_at (ISO 8601 datetime string for when they said they'd do it, else null)."
)


_FULFILLMENT_SYSTEM = (
    "The account owner previously promised something to a client on WhatsApp. They just "
    "sent this client a NEW message. Decide whether the new message actually fulfills that "
    "promise — e.g. it contains the price/quote, references an attached document, or "
    "explicitly confirms it was sent/shared. A message that just chats about something "
    "else, or repeats another vague 'I'll send it soon', does NOT fulfill it. "
    "Respond ONLY with a JSON object with key: fulfilled (boolean)."
)


_CLIENT_COMMITMENT_SYSTEM = (
    "You analyze a WhatsApp message a CLIENT just sent to the account owner. Decide "
    "whether this message makes a NEW commitment to send or do something for the account "
    "owner that is NOT already done in the message itself — e.g. 'I'll send the payment "
    "proof in 2 hours', 'I'll get you the signed contract tomorrow', 'let me confirm and "
    "get back to you by Friday', 'I'll confirm tomorrow'. "
    "Do NOT treat it as a commitment if the message already delivers the thing right there, "
    "is just a question, is a greeting/small-talk, is an immediate 'abhi'/'right now'/'send "
    "now' demand (those are urgent asks, not future promises), or is a vague acknowledgement "
    "('ok', 'noted', 'sure') with no actual promise — only a promise of FUTURE action from "
    "the client counts. "
    f"{_DEADLINE_HINT_INSTRUCTION} "
    "Respond ONLY with a JSON object with keys: "
    "is_commitment (boolean), "
    "commitment_type ('payment', 'document', 'other', or null — null if is_commitment is "
    "false), "
    "label (a short description of what the client promised, e.g. 'Send payment proof', "
    "'Share signed contract', or null if is_commitment is false), "
    "deadline_at (ISO 8601 datetime string for when they said they'd do it, else null)."
)


_CLIENT_FULFILLMENT_SYSTEM = (
    "A client previously promised the account owner something on WhatsApp. They just sent "
    "a NEW message. Decide whether the new message actually fulfills that promise — e.g. it "
    "contains the promised info, references an attached file, or explicitly confirms it was "
    "sent/done. A message that just chats about something else, or repeats another vague "
    "'I'll send it soon', does NOT fulfill it. "
    "Respond ONLY with a JSON object with key: fulfilled (boolean)."
)


_CLIENT_NUDGE_SYSTEM = (
    "Write a short, friendly WhatsApp follow-up message from the account owner to a client "
    "who has not yet delivered on something they said they'd do. Reference what they "
    "promised naturally and briefly ask for an update in the language and tone of the chat "
    "(Roman Hinglish if the conversation is in Hinglish, English if in English). "
    "If a real contact name is provided, you may use it once naturally. "
    "If no real name is provided, open with a simple greeting (e.g. Hi) — never invent a "
    "name and never use bracket placeholders for the name. "
    "Do not sound automated, pushy, or passive-aggressive. One or two short sentences."
)

_OWNER_COMMITMENT_DRAFT_SYSTEM = (
    "You draft a short WhatsApp message the account owner can send to a client about "
    "something the OWNER previously promised to send or do. "
    "A Reminder only proves a promise was made — not that it was fulfilled. "
    "Write natural, concise WhatsApp tone expressing future/imminent intent to fulfill "
    "the promise (any language that fits the chat). "
    "HARD RULE: Never state or imply the promised action is already completed, sent, "
    "prepared, confirmed, or delivered — in any language or wording — unless the prompt "
    "explicitly provides conversation evidence that it was. "
    "Do not invent new deadlines, attachments, or other facts. "
    "One or two short sentences. Return ONLY the message text, no preamble, no quotes."
)


TRANSLATE_TO_ENGLISH_SYSTEM = (
    "You turn a WhatsApp draft into English so the sender can read it. "
    "If the text is already English, return it unchanged. "
    "Otherwise return a faithful English translation of that same message. "
    "Keep names, numbers, links, and tone. Do not add, remove, or explain anything. "
    "Respond with ONLY the text, no preamble and no quotes."
)


TRANSLATE_REPLY_SYSTEM = (
    "You translate a WhatsApp reply into the target language for sending to a contact. "
    "If the input text is ALREADY in the target language or natural Hinglish, return it as-is without changing anything. "
    "Keep the tone, meaning, names, numbers, and links exactly as given — do not add, remove, or explain anything. "
    "Respond with ONLY the translated text, no preamble, no quotes. "
    "If the target language is 'Hinglish', write Hindi words in Roman/Latin script mixed "
    "naturally with English, the way people actually text (e.g. 'kal 5 baje confirm hai, "
    "milte hain') — do NOT write in Devanagari script and do NOT write pure formal Hindi."
)


def _language_context_block(language: str | None, translation: str | None) -> str:
    """Force the drafter to match the latest inbound language, not overall chat drift."""
    lines: list[str] = [
        "LANGUAGE LOCK: Reply in the same language as the client's LATEST message below.",
        "Do not switch to Hinglish if that latest message is English. "
        "Do not switch to English if that latest message is Hinglish/Hindi.",
    ]
    if language and not _is_english(language):
        lines.append(f"Detected language of their latest message: {language}.")
        if translation:
            lines.append(f"English meaning: {translation}")
    elif language:
        lines.append("Detected language of their latest message: English.")
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
        "just because they don't cleanly fit personal_date/personal_task/family_plan. A question "
        "about when a concrete task will be finished is still a task (personal_task), not a "
        "greeting. Only use personal_task/family_plan/personal_date when the message states an "
        "actual task, plan, or date. Because you know this is a personal contact, you have "
        "strong context for these "
        "calls — use confidence 80+ for a clear greeting/personal_task/family_plan read here, "
        "rather than hedging low just because the message is short."
    )


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
    goal is for drafts to sound like the user wrote them, not like an AI. A single reply
    during the silent-observation window is enough to start matching that tone for its
    category — no need to wait for a second one to accumulate first."""
    if not examples:
        return ""
    lines = [
        "\n\nTHE USER'S OWN VOICE IN THIS CHAT — recent messages the user actually wrote and sent "
        "themselves (not AI drafts). Match this exact tone, length, vocabulary, formality, language mix "
        "(e.g., Hinglish vs English), and texting style as closely as possible — the goal is for your "
        "draft to read exactly like the user wrote it personally, not like a generic AI reply:\n"
    ]
    for i, text in enumerate(examples, 1):
        snippet = text.strip()
        if len(snippet) > 200:
            snippet = snippet[:200].rstrip() + "..."
        lines.append(f"{i}. \"{snippet}\"")
    return "\n".join(lines)


def draft_category_hint(
    category: str,
    context_hint: str | None,
    *,
    personal: bool,
) -> str:
    if personal and context_hint is None and category == "greeting":
        context_hint = (
            "This is a casual message from a family member/close friend — reply the way you'd "
            "actually text them back. Short, plain, natural. No 'thank you for reaching out' or "
            "similar client-service phrasing — just reply like a person."
        )
    return context_hint or {
        "budget": "The client is asking about budget/pricing. Acknowledge and indicate next step.",
        "scope": "The client is asking about scope/requirements. Clarify or confirm details.",
        "timeline": (
            "The client is asking about timeline. Do not confirm or propose a date unless "
            "that date is already in the conversation."
        ),
        "follow_up": "The client is following up. Acknowledge and give a status or next step.",
        "other": "Respond helpfully to the client's query.",
        "meeting": (
            "The client wants to meet. Acknowledge the request. Do not confirm or propose a "
            "clock time unless that time is already in the conversation."
        ),
        "lead": (
            "This is a NEW lead/inquiry. Reply professionally and warmly as a first impression. "
            "Ask only for a detail this message is missing. Do not offer a catalogue, quote, or "
            "price unless that offer is already in the conversation."
        ),
        "payment": "The client's message is about a payment. Acknowledge it clearly.",
        "document": (
            "The client has requested a specific document/information. Acknowledge the request. "
            "Do not promise to send it unless the conversation shows it is ready to send; if "
            "something is needed first, ask briefly."
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
            "This is a casual/greeting or festival message — do NOT draft a business reply. "
            "If you must produce text, keep it empty-feeling and never invent business facts. "
            "Prefer no reply at all for bare hi/hello/festival wishes."
        ),
    }.get(category, "Respond helpfully to the client's query.")


def complaint_tone(anger_level: str | None) -> str:
    return {
        "high": "The client is very angry. Lead with sincere empathy and a clear apology.",
        "medium": "The client is frustrated. Acknowledge the frustration and apologise.",
        "low": "The client is mildly unhappy. Acknowledge the issue warmly.",
    }.get(anger_level or "high", "Acknowledge the issue and apologise sincerely.")


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

