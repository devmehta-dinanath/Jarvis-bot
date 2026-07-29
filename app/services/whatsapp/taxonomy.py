from __future__ import annotations

from typing import Any

CLASSIFIABLE_CATEGORIES = (
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

WORK_CATEGORIES = (
    "meeting",
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

LIFE_CATEGORIES = ("personal_date", "personal_task", "family_plan")

NUDGE_CATEGORIES = ("greeting", "voice_note", "media")

SYSTEM_CATEGORIES = ("personal_silence", "awaiting_reply", "pending_commitment", "group")

FILTER_CATEGORIES = ("forwarded", "spam")

UI_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "urgent",
        "title": "Urgent",
        "accent": "urgent",
        "categories": ("payment", "complaint"),
    },
    {
        "id": "meetings",
        "title": "Wants to meet",
        "accent": "info",
        "categories": ("meeting",),
    },
    {
        "id": "replies",
        "title": "Client messages",
        "accent": "info",
        "categories": (
            "lead",
            "document",
            "shipment",
            "order",
            "budget",
            "scope",
            "timeline",
            "follow_up",
            "other",
            "awaiting_reply",
            "pending_commitment",
        ),
    },
    {
        "id": "nudges",
        "title": "Casual & voice",
        "accent": "success",
        "categories": ("greeting", "voice_note", "media"),
    },
    {
        "id": "life",
        "title": "Life",
        "accent": "success",
        "categories": ("personal_date", "personal_task", "family_plan", "personal_silence"),
    },
)

_CATEGORY_META: dict[str, dict[str, Any]] = {
    "meeting": {
        "label": "Wants to meet",
        "lane": "work",
        "chip_label": "Meeting request — confirm time or add to calendar",
        "demo_messages": [
            "Hi, can we connect today at 3pm?",
            "Are you free tomorrow for a quick call?",
        ],
    },
    "payment": {
        "label": "Payment",
        "lane": "work",
        "chip_label": "Payment — verify or acknowledge",
        "demo_messages": [
            "I have sent you old invoice can you check",
            "Payment done, please confirm receipt",
            "Invoice still pending — when will you pay?",
        ],
    },
    "lead": {
        "label": "New lead",
        "lane": "work",
        "chip_label": "New lead — send a professional first reply",
        "demo_messages": [
            "Can you share your catalogue and bulk prices?",
            "We are looking for a supplier for packaging",
        ],
    },
    "document": {
        "label": "Document request",
        "lane": "work",
        "chip_label": "Document requested — send now",
        "demo_messages": [
            "Please send the invoice PDF",
            "Share the updated price list",
        ],
    },
    "complaint": {
        "label": "Complaint",
        "lane": "work",
        "chip_label": "Complaint — respond empathetically now",
        "demo_messages": [
            "Order not arrived yet, very unhappy",
            "This is the third time I am messaging about the damaged product",
        ],
    },
    "shipment": {
        "label": "Shipment",
        "lane": "work",
        "chip_label": "Shipment update — acknowledge",
        "demo_messages": [
            "Shipment cleared customs today",
            "Package delayed by 2 days",
        ],
    },
    "order": {
        "label": "Order confirmed",
        "lane": "work",
        "chip_label": "Order confirmed — acknowledge & proceed",
        "demo_messages": [
            "We'll go ahead with 500 units",
            "Confirming the order for the blue variant",
        ],
    },
    "budget": {
        "label": "Budget / pricing",
        "lane": "work",
        "chip_label": "Budget / pricing question — reply with next step",
        "demo_messages": [
            "What is the revised quote for 500 units?",
            "Can you share costing for the new scope?",
        ],
    },
    "scope": {
        "label": "Scope",
        "lane": "work",
        "chip_label": "Scope question — clarify requirements",
        "demo_messages": [
            "Does this include installation and training?",
            "What exactly is covered in phase 2?",
        ],
    },
    "timeline": {
        "label": "Timeline",
        "lane": "work",
        "chip_label": "Timeline question — confirm dates",
        "demo_messages": [
            "When will the first batch be ready?",
            "What is the ETA for delivery?",
        ],
    },
    "follow_up": {
        "label": "Follow-up",
        "lane": "work",
        "chip_label": "Follow-up — client is waiting for your reply",
        "demo_messages": [
            "Just following up on my last message",
            "Did you get a chance to review this?",
        ],
    },
    "other": {
        "label": "Client message",
        "lane": "work",
        "chip_label": "Client message — draft a helpful reply",
        "demo_messages": [
            "Can you help me with the export paperwork?",
        ],
    },
    "personal_date": {
        "label": "Personal date",
        "lane": "life",
        "chip_label": "Personal date — remember this",
        "demo_messages": [
            "Mum's birthday is on the 15th",
            "Dad's surgery is on the 20th",
        ],
    },
    "personal_task": {
        "label": "Personal task",
        "lane": "life",
        "chip_label": "Personal reminder — add to your list",
        "demo_messages": [
            "Pick up dry cleaning on the way home",
            "Book a table for Saturday night",
        ],
    },
    "family_plan": {
        "label": "Family plan",
        "lane": "life",
        "chip_label": "Family plan — add to calendar?",
        "demo_messages": [
            "Family lunch on Sunday at Mum's place",
            "Movie tonight at 7?",
        ],
    },
    "greeting": {
        "label": "Casual message",
        "lane": "work",
        "chip_label": "Casual message — reply when you can",
        "demo_messages": [
            "Good morning! Hope you're doing well",
            "Happy Diwali!",
        ],
    },
    "voice_note": {
        "label": "Voice note",
        "lane": "work",
        "chip_label": "Voice note — listen before replying",
        "demo_messages": [],
    },
    "media": {
        "label": "Media",
        "lane": "work",
        "chip_label": "Media message — open WhatsApp to view it",
        "demo_messages": [],
    },
    "personal_silence": {
        "label": "Reply reminder",
        "lane": "life",
        "chip_label": "You haven't replied yet",
        "demo_messages": [],
    },
    "awaiting_reply": {
        "label": "Awaiting your reply",
        "lane": "work",
        "chip_label": "You haven't replied yet",
        "demo_messages": [],
    },
    "pending_commitment": {
        "label": "Pending commitment",
        "lane": "work",
        "chip_label": "You still owe them something",
        "demo_messages": [],
    },
}


def category_label(category: str | None) -> str:
    if not category:
        return "WhatsApp"
    return _CATEGORY_META.get(category, {}).get("label") or category.replace("_", " ").title()


def default_chip_label(category: str | None) -> str | None:
    if not category:
        return None
    return _CATEGORY_META.get(category, {}).get("chip_label")


def ui_section_for_category(category: str | None) -> str | None:
    if not category:
        return None
    for section in UI_SECTIONS:
        if category in section["categories"]:
            return section["id"]
    return None


def taxonomy_payload() -> dict[str, Any]:
    categories = []
    for key in (
        *WORK_CATEGORIES,
        *LIFE_CATEGORIES,
        *NUDGE_CATEGORIES,
        "personal_silence",
        "awaiting_reply",
        "pending_commitment",
    ):
        meta = _CATEGORY_META.get(key, {})
        categories.append(
            {
                "id": key,
                "label": meta.get("label", key),
                "lane": meta.get("lane", "work"),
                "section": ui_section_for_category(key),
                "chip_label": meta.get("chip_label"),
                "demo_messages": meta.get("demo_messages", []),
            }
        )

    return {
        "sections": [
            {
                "id": section["id"],
                "title": section["title"],
                "accent": section["accent"],
                "categories": list(section["categories"]),
            }
            for section in UI_SECTIONS
        ],
        "categories": categories,
        "classifiable": list(CLASSIFIABLE_CATEGORIES),
        "filters": list(FILTER_CATEGORIES),
    }
