/**
 * One conversation → one card. Multiple confirmed plans render as dated items inside.
 */
import { applyAvatarGradient } from "../../lib/avatar-color.js";
import {
  buildCategoryMeta,
  canRemind,
  canSchedule,
  categoryLabel,
  isUrgent
} from "../../lib/whatsapp-categories.js";
import { formatMeetingTime } from "../../lib/time.js";
import { enhanceWhatsAppCard } from "../../lib/whatsapp-card-actions.js";

function displayName(groupOrSuggestion) {
  return groupOrSuggestion.contact_name || groupOrSuggestion.wa_id || "WhatsApp contact";
}

function planWhenLabel(suggestion) {
  const details = suggestion?.details || {};
  if (details.start) {
    return formatMeetingTime(details.start);
  }
  if (details.date && details.time) {
    return `${details.date} · ${details.time}`;
  }
  if (details.date) {
    return details.date;
  }
  const category = suggestion?.category || suggestion?.kind;
  if (category && category !== "meeting" && category !== "family_plan") {
    return categoryLabel(suggestion);
  }
  return "Time not set yet";
}

function scheduleButtonLabel(hasStartTime) {
  return hasStartTime ? "Yes — schedule & send" : "Schedule meeting";
}

function createPlanItem(suggestion, handlers) {
  const {
    onSchedule,
    onRemind,
    onDismiss,
    onSent,
    onInteractionChange
  } = handlers;

  function setInteracting(active) {
    if (typeof onInteractionChange === "function") {
      onInteractionChange(active);
    }
  }

  const item = document.createElement("div");
  item.className = "whatsapp-card__plan";
  item.dataset.suggestionId = String(suggestion.id);

  const when = document.createElement("p");
  when.className = "whatsapp-card__plan-when";
  when.textContent = planWhenLabel(suggestion);
  if (!suggestion.details?.start && !suggestion.details?.date) {
    when.title = "No time in the chat yet — tap Schedule meeting to pick one";
  }

  const original = document.createElement("blockquote");
  original.className = "language-card__original whatsapp-card__plan-message";
  original.textContent =
    suggestion.message_body || suggestion.message_summary || "No message text.";

  const meta = document.createElement("p");
  meta.className = "whatsapp-card__meta";
  meta.textContent = buildCategoryMeta(suggestion, { formatMeetingTime });
  meta.hidden = meta.textContent.length === 0;

  const actions = document.createElement("div");
  actions.className = "language-card__actions";

  const showSchedule = canSchedule(suggestion);
  const showRemind = canRemind(suggestion);
  const hasStartTime = Boolean(suggestion.details?.start);

  const scheduleBtn = document.createElement("button");
  scheduleBtn.type = "button";
  scheduleBtn.className = "btn btn--ghost";
  scheduleBtn.textContent = scheduleButtonLabel(hasStartTime);
  scheduleBtn.hidden = !showSchedule;
  if (suggestion.details?.calendar_event_id) {
    scheduleBtn.textContent = "On calendar";
    scheduleBtn.disabled = true;
  }

  const schedulePicker = document.createElement("div");
  schedulePicker.className = "whatsapp-card__schedule-picker";
  schedulePicker.hidden = true;

  const schedulePickerLabel = document.createElement("p");
  schedulePickerLabel.className = "language-card__label";
  schedulePickerLabel.textContent = "Pick a date & time";

  const scheduleTimeInput = document.createElement("input");
  scheduleTimeInput.type = "datetime-local";
  scheduleTimeInput.className = "whatsapp-card__schedule-input";
  scheduleTimeInput.setAttribute("aria-label", "Meeting date and time");

  const schedulePickerActions = document.createElement("div");
  schedulePickerActions.className = "language-card__actions";

  const scheduleConfirmBtn = document.createElement("button");
  scheduleConfirmBtn.type = "button";
  scheduleConfirmBtn.className = "btn btn--primary";
  scheduleConfirmBtn.textContent = "Confirm & schedule";

  const scheduleCancelBtn = document.createElement("button");
  scheduleCancelBtn.type = "button";
  scheduleCancelBtn.className = "btn btn--ghost";
  scheduleCancelBtn.textContent = "Cancel";

  schedulePickerActions.append(scheduleConfirmBtn, scheduleCancelBtn);
  schedulePicker.append(schedulePickerLabel, scheduleTimeInput, schedulePickerActions);

  const remindBtn = document.createElement("button");
  remindBtn.type = "button";
  remindBtn.className = "btn btn--ghost";
  remindBtn.textContent = "Remind me";
  remindBtn.hidden = !showRemind;
  if (suggestion.details?.reminder_event_id) {
    remindBtn.textContent = "Reminder set ✓";
    remindBtn.disabled = true;
  }

  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.className = "btn btn--ghost";
  dismissBtn.textContent = "Dismiss";

  actions.append(scheduleBtn, remindBtn, dismissBtn);

  async function runSchedule(overrides) {
    if (typeof onSchedule !== "function") {
      return;
    }
    scheduleBtn.disabled = true;
    scheduleBtn.textContent = "Scheduling…";
    try {
      await onSchedule(suggestion, scheduleBtn, overrides);
      if (scheduleBtn.textContent.includes("Scheduled")) {
        if (typeof onSent === "function") {
          await onSent();
        }
      }
    } finally {
      if (!scheduleBtn.textContent.includes("Scheduled") && !scheduleBtn.textContent.includes("On calendar")) {
        scheduleBtn.disabled = false;
        scheduleBtn.textContent = scheduleButtonLabel(hasStartTime);
      }
    }
  }

  scheduleBtn.addEventListener("click", async () => {
    if (!hasStartTime) {
      schedulePicker.hidden = !schedulePicker.hidden;
      setInteracting(!schedulePicker.hidden);
      if (!schedulePicker.hidden) {
        scheduleTimeInput.focus();
      }
      return;
    }
    await runSchedule();
  });

  scheduleConfirmBtn.addEventListener("click", async () => {
    if (!scheduleTimeInput.value) {
      scheduleConfirmBtn.title = "Pick a date and time first";
      window.setTimeout(() => {
        scheduleConfirmBtn.title = "";
      }, 3000);
      return;
    }
    const startIso = new Date(scheduleTimeInput.value).toISOString();
    scheduleConfirmBtn.disabled = true;
    const originalLabel = scheduleConfirmBtn.textContent;
    scheduleConfirmBtn.textContent = "Scheduling…";
    try {
      await runSchedule({ start: startIso });
      schedulePicker.hidden = true;
      setInteracting(false);
    } catch (error) {
      scheduleConfirmBtn.title = String(error.message || error);
      window.setTimeout(() => {
        scheduleConfirmBtn.title = "";
      }, 3000);
    } finally {
      scheduleConfirmBtn.disabled = false;
      scheduleConfirmBtn.textContent = originalLabel;
    }
  });

  scheduleCancelBtn.addEventListener("click", () => {
    schedulePicker.hidden = true;
    setInteracting(false);
  });

  remindBtn.addEventListener("click", async () => {
    if (typeof onRemind !== "function" || remindBtn.disabled) {
      return;
    }
    try {
      await onRemind(suggestion, remindBtn);
    } catch {
      // handler already surfaces the error
    }
  });

  dismissBtn.addEventListener("click", async () => {
    if (typeof onDismiss !== "function") {
      return;
    }
    dismissBtn.disabled = true;
    try {
      await onDismiss(suggestion);
    } catch (error) {
      dismissBtn.disabled = false;
      dismissBtn.title = String(error.message || error);
    }
  });

  item.append(when, original, meta, actions, schedulePicker);
  enhanceWhatsAppCard(item, suggestion);
  return item;
}

/**
 * @param {{ contact_id?: number, contact_name?: string, wa_id?: string, is_group?: boolean, items: object[] }} group
 * @param {object} handlers
 */
export function createWhatsAppConversationCard(group, handlers) {
  const items = Array.isArray(group.items) ? group.items : [];
  const primary = items[items.length - 1] || items[0] || group;

  const card = document.createElement("article");
  card.className = "language-card language-card--whatsapp whatsapp-card--conversation";
  if (items.some((item) => isUrgent(item))) {
    card.classList.add("language-card--urgent");
  }
  if (group.contact_id != null) {
    card.dataset.contactId = String(group.contact_id);
  }

  const header = document.createElement("div");
  header.className = "language-card__header";

  const identity = document.createElement("div");
  identity.className = "language-card__identity";

  const name = displayName(group);
  const avatar = document.createElement("span");
  avatar.className = "conversation-item__avatar language-card__avatar";
  avatar.textContent = name.charAt(0).toUpperCase();
  applyAvatarGradient(avatar, name);

  const contact = document.createElement("h3");
  contact.className = "language-card__contact";
  contact.textContent = name;

  identity.append(avatar, contact);

  const badge = document.createElement("span");
  badge.className = "language-card__badge";
  badge.textContent =
    items.length > 1
      ? `${items.length} plans`
      : categoryLabel(primary);

  header.append(identity, badge);

  const plans = document.createElement("div");
  plans.className = "whatsapp-card__plans";
  for (const suggestion of items) {
    plans.appendChild(createPlanItem(suggestion, handlers));
  }

  card.append(header, plans);

  if (group.is_group && typeof handlers.onExcludeGroup === "function") {
    const stopGroupBtn = document.createElement("button");
    stopGroupBtn.type = "button";
    stopGroupBtn.className = "btn btn--ghost btn--warning";
    stopGroupBtn.textContent = "Stop reading this group";
    stopGroupBtn.addEventListener("click", async () => {
      stopGroupBtn.disabled = true;
      try {
        await handlers.onExcludeGroup(primary);
      } catch (error) {
        stopGroupBtn.disabled = false;
        stopGroupBtn.title = String(error.message || error);
      }
    });
    const footerActions = document.createElement("div");
    footerActions.className = "language-card__actions";
    footerActions.appendChild(stopGroupBtn);
    card.appendChild(footerActions);
  }

  return card;
}
