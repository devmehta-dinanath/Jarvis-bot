/**
 * UI taxonomy — kept in sync with app/services/whatsapp/taxonomy.py
 * Backend exposes GET /api/v1/whatsapp/categories for demo scenarios + labels.
 */

export const WORK_CATEGORIES = [
  "meeting",
  "payment",
  "lead",
  "document",
  "complaint",
  "shipment",
  "budget",
  "scope",
  "timeline",
  "follow_up",
  "other"
];

export const LIFE_CATEGORIES = ["personal_date", "personal_task", "family_plan"];

export const NUDGE_CATEGORIES = ["greeting", "voice_note", "media"];

export const LIFE_NUDGE_CATEGORIES = ["personal_silence"];

export const ALL_SURFACE_CATEGORIES = [
  ...WORK_CATEGORIES,
  ...LIFE_CATEGORIES,
  ...NUDGE_CATEGORIES,
  ...LIFE_NUDGE_CATEGORIES
];

export const CATEGORY_LABELS = {
  meeting: "Wants to meet",
  payment: "Payment",
  lead: "New lead",
  document: "Document request",
  complaint: "Complaint",
  shipment: "Shipment",
  budget: "Budget / pricing",
  scope: "Scope",
  timeline: "Timeline",
  follow_up: "Follow-up",
  other: "Client message",
  personal_date: "Personal date",
  personal_task: "Personal task",
  family_plan: "Family plan",
  greeting: "Casual message",
  voice_note: "Voice note",
  media: "Media",
  personal_silence: "Reply reminder"
};

/** Merge labels from GET /api/v1/whatsapp/categories (backend taxonomy.py). */
export function applyTaxonomyFromApi(payload) {
  if (!payload?.categories) {
    return;
  }
  for (const category of payload.categories) {
    if (category.id && category.label) {
      CATEGORY_LABELS[category.id] = category.label;
    }
  }
}

export const CATEGORY_SECTIONS = [
  {
    id: "urgent",
    title: "Urgent",
    accent: "urgent",
    categories: ["payment", "complaint"]
  },
  {
    id: "meetings",
    title: "Wants to meet",
    accent: "info",
    categories: ["meeting"]
  },
  {
    id: "replies",
    title: "Client messages",
    accent: "info",
    categories: [
      "lead",
      "document",
      "shipment",
      "budget",
      "scope",
      "timeline",
      "follow_up",
      "other"
    ]
  },
  {
    id: "nudges",
    title: "Casual & voice",
    accent: "success",
    categories: ["greeting", "voice_note", "media"]
  },
  {
    id: "life",
    title: "Life",
    accent: "success",
    categories: ["personal_date", "personal_task", "family_plan", "personal_silence"]
  }
];

const URGENT_CATEGORIES = new Set(["payment", "complaint"]);
const SCHEDULE_CATEGORIES = new Set(["meeting"]);

export function resolveCategory(suggestion) {
  return suggestion.category || suggestion.kind || "other";
}

export function categoryLabel(suggestion) {
  const key = resolveCategory(suggestion);
  return CATEGORY_LABELS[key] || "WhatsApp";
}

export function categoryActionHint(suggestion) {
  const chip = suggestion.details?.chip_label;
  if (chip) {
    return chip.split("—")[0].trim();
  }
  return null;
}

export function isUrgent(suggestion) {
  const key = resolveCategory(suggestion);
  return (
    URGENT_CATEGORIES.has(key) ||
    suggestion.priority === "critical" ||
    suggestion.priority === "very_high"
  );
}

export function canSchedule(suggestion) {
  return SCHEDULE_CATEGORIES.has(resolveCategory(suggestion));
}

export function canSendReply(suggestion) {
  // No draft means the AI either can't send a reply for this category (voice note,
  // media, reminders) or deliberately didn't draft one (low-confidence chip) — never
  // fall back to a generic canned reply in either case.
  return Boolean(suggestion.draft_text);
}

export function partitionSuggestions(suggestions) {
  const buckets = Object.fromEntries(
    CATEGORY_SECTIONS.map((section) => [section.id, []])
  );

  for (const suggestion of suggestions) {
    const key = resolveCategory(suggestion);
    const section = CATEGORY_SECTIONS.find((item) => item.categories.includes(key));
    if (section) {
      buckets[section.id].push(suggestion);
    }
  }

  return buckets;
}

export function buildCategoryMeta(suggestion, { formatMeetingTime }) {
  const key = resolveCategory(suggestion);
  const parts = [];

  const actionHint = categoryActionHint(suggestion);
  if (actionHint && actionHint !== categoryLabel(suggestion)) {
    parts.push(actionHint);
  }

  if (key === "payment") {
    parts.push(
      suggestion.details?.payment_status === "received"
        ? "Payment received"
        : "Invoice / payment pending"
    );
  }

  if (key === "complaint" && suggestion.details?.anger_level) {
    parts.push(`Tone: ${suggestion.details.anger_level}`);
  }

  if (key === "document" && suggestion.details?.document_type) {
    parts.push(`Requested: ${suggestion.details.document_type}`);
  }

  if (key === "shipment" && suggestion.details?.shipment_status) {
    parts.push(
      suggestion.details.shipment_status === "delayed"
        ? "Delivery delayed"
        : "Shipment update"
    );
  }

  if (key === "follow_up" && suggestion.details?.hours_since_reply != null) {
    parts.push(`Waiting ${suggestion.details.hours_since_reply}h`);
  }

  if (key === "personal_date" && suggestion.details?.date) {
    parts.push(`Date: ${suggestion.details.date}`);
  }

  if (key === "personal_task" && suggestion.details?.task_summary) {
    parts.push(suggestion.details.task_summary);
  }

  if (key === "family_plan") {
    if (suggestion.details?.date) {
      parts.push(`Plan: ${suggestion.details.date}`);
    }
    if (suggestion.details?.time) {
      parts.push(`Time: ${suggestion.details.time}`);
    }
  }

  if (key === "personal_silence" && suggestion.details?.days_silent != null) {
    parts.push(`${suggestion.details.days_silent} days without reply`);
  }

  if (key === "voice_note") {
    parts.push("Listen to the voice note before replying");
  }

  if (suggestion.details?.start && formatMeetingTime) {
    parts.push(`Proposed: ${formatMeetingTime(suggestion.details.start)}`);
  }

  const calendarStatus = suggestion.details?.calendar_status;
  if (calendarStatus === "available") {
    parts.push("Slot is free");
  } else if (calendarStatus === "busy") {
    parts.push("Slot looks busy");
  }

  if (suggestion.details?.calendar_html_link && key !== "meeting") {
    parts.push("On your calendar");
  }

  return parts.join(" · ");
}
