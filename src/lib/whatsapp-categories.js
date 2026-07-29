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
  "order",
  "budget",
  "scope",
  "timeline",
  "follow_up",
  "other"
];

export const LIFE_CATEGORIES = ["personal_date", "personal_task", "family_plan"];

export const NUDGE_CATEGORIES = ["greeting", "voice_note", "media"];

export const LIFE_NUDGE_CATEGORIES = ["personal_silence"];

export const WORK_NUDGE_CATEGORIES = ["awaiting_reply", "pending_commitment"];

export const ALL_SURFACE_CATEGORIES = [
  ...WORK_CATEGORIES,
  ...LIFE_CATEGORIES,
  ...NUDGE_CATEGORIES,
  ...LIFE_NUDGE_CATEGORIES,
  ...WORK_NUDGE_CATEGORIES
];

export const CATEGORY_LABELS = {
  meeting: "Wants to meet",
  payment: "Payment",
  lead: "New lead",
  document: "Document request",
  complaint: "Complaint",
  shipment: "Shipment",
  order: "Order confirmed",
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
  personal_silence: "Reply reminder",
  awaiting_reply: "Awaiting your reply",
  pending_commitment: "Pending commitment"
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
      "order",
      "budget",
      "scope",
      "timeline",
      "follow_up",
      "other",
      "awaiting_reply",
      "pending_commitment"
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
const SCHEDULE_CATEGORIES = new Set(["meeting", "family_plan"]);

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
  // Never show Schedule for a tentative or one-sided plan — only once both sides of the
  // conversation have clearly confirmed (see classifier.py MUTUAL CONFIRMATION RULE).
  return (
    SCHEDULE_CATEGORIES.has(resolveCategory(suggestion)) &&
    suggestion.details?.confirmed === true
  );
}

export function canSendReply(suggestion) {
  // No draft means the AI either can't send a reply for this category (voice note,
  // media, reminders) or deliberately didn't draft one (low-confidence chip) — never
  // fall back to a generic canned reply in either case. The one exception is the 7-day
  // silent-observation window: there's still no AI draft, but the box itself should show
  // so the owner can type and send their own reply from inside the app.
  return Boolean(suggestion.draft_text) || Boolean(suggestion.details?.silent_observation);
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

function draftPendingHint(suggestion, formatMeetingTime) {
  // The card lists the moment it's classified; the drafted reply itself is what waits
  // out Rule 9's timer (instant/30min/4-6h). Only show this when that's actually why
  // there's no draft yet — not for low-confidence, a pending clarifying question (that
  // has its own UI — see needs_clarification), or reminder-only (no-reply) cards.
  if (
    suggestion.draft_text ||
    suggestion.details?.low_confidence ||
    suggestion.details?.needs_clarification ||
    suggestion.details?.silent_observation ||
    !suggestion.visible_after
  ) {
    return null;
  }
  const readyAt = new Date(suggestion.visible_after);
  if (Number.isNaN(readyAt.getTime()) || readyAt <= new Date()) {
    return null;
  }
  return `Drafting reply — ready ${formatMeetingTime ? formatMeetingTime(suggestion.visible_after) : readyAt.toLocaleString()}`;
}

export function buildCategoryMeta(suggestion, { formatMeetingTime }) {
  const key = resolveCategory(suggestion);
  const parts = [];

  const actionHint = categoryActionHint(suggestion);
  if (actionHint && actionHint !== categoryLabel(suggestion)) {
    parts.push(actionHint);
  }

  const pendingHint = draftPendingHint(suggestion, formatMeetingTime);
  if (pendingHint) {
    parts.push(pendingHint);
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
