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

export const WORK_NUDGE_CATEGORIES = ["awaiting_reply", "pending_commitment", "client_commitment"];

export const ALL_SURFACE_CATEGORIES = [
  ...WORK_CATEGORIES,
  ...LIFE_CATEGORIES,
  ...NUDGE_CATEGORIES,
  ...LIFE_NUDGE_CATEGORIES,
  ...WORK_NUDGE_CATEGORIES
];

export const MEETING_REMINDER_CATEGORIES = new Set(["meeting"]);

export const MEETING_REMINDER_KINDS = new Set(["meeting"]);

/** Inbox product scope: meeting/call chips only (matches server default). */
export const MEETINGS_REMINDERS_ONLY = true;

export function isMeetingOrReminder(suggestion) {
  if (suggestion?.details?.safety_concern) {
    return true;
  }
  if (MEETING_REMINDER_KINDS.has(suggestion?.kind)) {
    return true;
  }
  return MEETING_REMINDER_CATEGORIES.has(suggestion?.category);
}

export function filterMeetingReminderSuggestions(suggestions) {
  if (!MEETINGS_REMINDERS_ONLY) {
    return suggestions;
  }
  return (suggestions || []).filter(isMeetingOrReminder);
}

/**
 * Mutual confirmation only. The classifier sets details.confirmed and the
 * "Unconfirmed plan — …" chip when both sides have not agreed — those must not
 * appear in the inbox or go to Schedule / Remind me.
 */
export function isUnconfirmedPlan(suggestion) {
  const details = suggestion?.details;
  if (!details || typeof details !== "object") {
    return false;
  }
  if (details.confirmed === false) {
    return true;
  }
  const chip = String(details.chip_label || "");
  if (/^unconfirmed\b/i.test(chip.trim())) {
    return true;
  }
  return false;
}

export function isConfirmedPlan(suggestion) {
  // Require an explicit true — missing confirmed (e.g. recovery chips) must not surface.
  return suggestion?.details?.confirmed === true;
}

export function filterConfirmedPlans(suggestions) {
  return (suggestions || []).filter((suggestion) => {
    if (isUnconfirmedPlan(suggestion)) {
      return false;
    }
    // Meeting / family-plan style chips need mutual confirmation.
    const category = suggestion?.category || suggestion?.kind;
    if (category === "meeting" || category === "family_plan") {
      return isConfirmedPlan(suggestion);
    }
    return true;
  });
}

/**
 * One conversation (contact) → one group. Plans sorted soonest-first by start/date.
 */
export function groupSuggestionsByContact(suggestions) {
  const byContact = new Map();

  for (const suggestion of suggestions || []) {
    const key = suggestion.contact_id ?? suggestion.wa_id ?? suggestion.id;
    if (!byContact.has(key)) {
      byContact.set(key, {
        contact_id: suggestion.contact_id,
        contact_name: suggestion.contact_name,
        wa_id: suggestion.wa_id,
        is_group: Boolean(suggestion.is_group),
        items: []
      });
    }
    const group = byContact.get(key);
    group.items.push(suggestion);
    // Prefer the richest contact label we have seen.
    if (!group.contact_name && suggestion.contact_name) {
      group.contact_name = suggestion.contact_name;
    }
    if (!group.wa_id && suggestion.wa_id) {
      group.wa_id = suggestion.wa_id;
    }
  }

  const planSortKey = (suggestion) => {
    const start = suggestion?.details?.start;
    if (start) {
      const ms = Date.parse(start);
      if (!Number.isNaN(ms)) {
        return ms;
      }
    }
    const date = suggestion?.details?.date;
    const time = suggestion?.details?.time || "00:00";
    if (date) {
      const ms = Date.parse(`${date}T${time}`);
      if (!Number.isNaN(ms)) {
        return ms;
      }
    }
    const created = Date.parse(suggestion?.created_at || "");
    return Number.isNaN(created) ? Number.MAX_SAFE_INTEGER : created;
  };

  const groups = Array.from(byContact.values());
  for (const group of groups) {
    group.items.sort((a, b) => planSortKey(a) - planSortKey(b));
  }
  groups.sort((a, b) => {
    const nameA = (a.contact_name || a.wa_id || "").toLowerCase();
    const nameB = (b.contact_name || b.wa_id || "").toLowerCase();
    return nameA.localeCompare(nameB);
  });
  return groups;
}

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
  pending_commitment: "Pending commitment",
  client_commitment: "Client hasn't delivered"
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
    id: "meetings",
    title: "Wants to meet",
    accent: "info",
    categories: ["meeting"]
  }
];

const URGENT_CATEGORIES = new Set(["payment", "complaint"]);
// Kinds that are already reminders in their own right — "Remind me" on top of a reminder
// would just be noise.
const ALREADY_REMINDER_KINDS = new Set([
  "life_nudge",
  "followup_nudge",
  "commitment_reminder",
  "client_commitment_reminder"
]);

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
  // Schedule only for mutually confirmed meeting plans. Unconfirmed plans are filtered
  // out of the inbox; this is a second gate if one slips through.
  if (resolveCategory(suggestion) !== "meeting") {
    return false;
  }
  return isConfirmedPlan(suggestion);
}

export function canRemind(suggestion) {
  // Show Remind me (or Reminder set ✓) on actionable cards. Nudge/reminder kinds are
  // already reminders themselves. Do NOT hide the control once a reminder exists — the
  // card must still show "Reminder set ✓" so auto-remind is visible.
  if (ALREADY_REMINDER_KINDS.has(suggestion.kind)) {
    return false;
  }
  // Meetings / family plans: only mutually confirmed plans get Remind me.
  const category = resolveCategory(suggestion);
  if (category === "meeting" || category === "family_plan") {
    return isConfirmedPlan(suggestion);
  }
  return true;
}

export function hasReminder(suggestion) {
  return Boolean(suggestion.details?.reminder_event_id);
}

export function canSendReply() {
  // The reply box always shows, for every category — populated with the AI's draft when
  // there is one, empty for the owner to type into when there isn't. Never fall back to a
  // generic canned line: empty-but-present beats fake-personalized.
  return true;
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

  if (suggestion.details?.calendar_html_link && key === "meeting") {
    parts.push("Meeting on calendar");
  } else if (suggestion.details?.calendar_html_link && key !== "meeting") {
    parts.push("On your calendar");
  }

  // Step 1 — surface auto-created personal reminders on the card meta line.
  if (suggestion.details?.reminder_event_id) {
    if (suggestion.details?.reminder_at && formatMeetingTime) {
      parts.push(`Reminder confirmed for ${formatMeetingTime(suggestion.details.reminder_at)}`);
    } else {
      parts.push("Reminder confirmed ✓");
    }
  }

  if (suggestion.details?.meet_link) {
    parts.push("Google Meet ready");
  }

  return parts.join(" · ");
}
