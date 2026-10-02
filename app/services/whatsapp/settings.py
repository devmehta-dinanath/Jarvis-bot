"""STEP 2 WhatsApp settings — env defaults that do not require editing root-owned config.py."""

from __future__ import annotations

import os


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}


# Prefer env; default ON for STEP 2 (ignore older config.py default of false when unset).
def _drafts_enabled() -> bool:
    raw = os.getenv("WHATSAPP_AI_DRAFTS_ENABLED")
    if raw is None:
        return True
    return raw.lower() in {"1", "true", "yes"}


WHATSAPP_AI_DRAFTS_ENABLED = _drafts_enabled()

WAHA_WEBHOOK_URL = os.getenv("WAHA_WEBHOOK_URL", "").strip() or None
WAHA_ENSURE_WEBHOOK_EVENTS = _flag("WAHA_ENSURE_WEBHOOK_EVENTS", "true")

WHATSAPP_HISTORY_BACKFILL_ON_START = _flag("WHATSAPP_HISTORY_BACKFILL_ON_START", "false")
WHATSAPP_HISTORY_BACKFILL_CHAT_LIMIT = int(
    os.getenv("WHATSAPP_HISTORY_BACKFILL_CHAT_LIMIT", "40")
)
WHATSAPP_HISTORY_BACKFILL_MESSAGES_PER_CHAT = int(
    os.getenv("WHATSAPP_HISTORY_BACKFILL_MESSAGES_PER_CHAT", "40")
)

_FOLLOWUP_FLAG = os.getenv("WHATSAPP_FOLLOWUP_FLAG_HOURS", "24")
_FOLLOWUP_URGENT = os.getenv("WHATSAPP_FOLLOWUP_URGENT_HOURS", "72")
WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS = float(
    os.getenv("WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS", _FOLLOWUP_FLAG)
)
WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS = float(
    os.getenv("WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS", _FOLLOWUP_URGENT)
)

# Patterns that suggest the owner sent something worth chasing if they go silent.
OWNER_FOLLOWUP_BODY_RE = (
    r"(?i)("
    r"\b(quote|quotation|pricing|price\s*list|invoice|proposal|catalogue|catalog)\b|"
    r"\b(please\s+(confirm|review|check|let\s+me\s+know)|"
    r"can\s+you\s+(confirm|review|check|share)|"
    r"any\s+update|looking\s+forward|awaiting\s+your|"
    r"sent\s+(the|you)|attached|please\s+find)\b|"
    r"\?\s*$"  # last outbound ends with a question
    r")"
)
