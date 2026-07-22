import { sendSuggestionFeedback, sendSuggestionReply } from "../../lib/api.js";
import {
  buildCategoryMeta,
  canSchedule,
  canSendReply,
  categoryLabel,
  isUrgent,
  resolveCategory
} from "../../lib/whatsapp-categories.js";
import { formatMeetingTime } from "../../lib/time.js";

function displayName(suggestion) {
  return suggestion.contact_name || suggestion.wa_id || "WhatsApp contact";
}

function defaultDraft(suggestion) {
  const link = suggestion.details?.meet_link || suggestion.details?.calendar_html_link;
  if (link && resolveCategory(suggestion) === "meeting") {
    return `Yes, happy to connect! Here's the link: ${link}`;
  }
  return (
    suggestion.draft_text ||
    "Thanks for your message — I'll get back to you shortly."
  );
}

function primaryActionLabel(suggestion) {
  const key = resolveCategory(suggestion);
  if (key === "meeting" && suggestion.details?.calendar_event_id) {
    return "Send confirmation";
  }
  if (key === "personal_silence") {
    return "Reply in WhatsApp";
  }
  return "Send reply";
}

export function createWhatsAppRequestCard(suggestion, { onSchedule, onDismiss, onSent, onExcludeGroup, onWrong }) {
  const card = document.createElement("article");
  card.className = "language-card language-card--whatsapp";
  if (isUrgent(suggestion)) {
    card.classList.add("language-card--urgent");
  }

  const category = resolveCategory(suggestion);
  const showDraft = canSendReply(suggestion);
  const showSchedule = canSchedule(suggestion);

  const header = document.createElement("div");
  header.className = "language-card__header";

  const contact = document.createElement("h3");
  contact.className = "language-card__contact";
  contact.textContent = displayName(suggestion);

  const badge = document.createElement("span");
  badge.className = "language-card__badge";
  badge.textContent = categoryLabel(suggestion);

  header.append(contact, badge);

  const original = document.createElement("blockquote");
  original.className = "language-card__original";
  original.textContent =
    suggestion.message_body || suggestion.message_summary || "No message text.";
  if (category === "personal_silence" && !suggestion.message_body) {
    original.textContent =
      suggestion.details?.chip_label ||
      `You haven't replied to ${displayName(suggestion)} yet.`;
  }
  if (category === "media" && !suggestion.message_body) {
    original.textContent = suggestion.details?.chip_label || "Media message — no caption.";
  }

  const meta = document.createElement("p");
  meta.className = "whatsapp-card__meta";
  meta.textContent = buildCategoryMeta(suggestion, { formatMeetingTime });
  meta.hidden = meta.textContent.length === 0;

  const draftSection = document.createElement("div");
  draftSection.className = "language-card__draft";
  draftSection.hidden = !showDraft;

  const draftLabel = document.createElement("p");
  draftLabel.className = "language-card__label";
  draftLabel.textContent = "Draft reply";

  const draftText = document.createElement("p");
  draftText.className = "language-card__draft-text";
  draftText.textContent = defaultDraft(suggestion);

  const draftInput = document.createElement("textarea");
  draftInput.className = "whatsapp-card__draft-input";
  draftInput.value = defaultDraft(suggestion);
  draftInput.rows = 3;
  draftInput.hidden = true;
  draftInput.setAttribute("aria-label", "Edit draft reply");

  draftSection.append(draftLabel, draftText, draftInput);

  const actions = document.createElement("div");
  actions.className = "language-card__actions";

  const sendBtn = document.createElement("button");
  sendBtn.type = "button";
  sendBtn.className = "btn btn--primary btn--large";
  sendBtn.textContent = primaryActionLabel(suggestion);
  sendBtn.hidden = !showDraft;

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn btn--ghost";
  editBtn.textContent = "Edit";
  editBtn.hidden = !showDraft;

  const scheduleBtn = document.createElement("button");
  scheduleBtn.type = "button";
  scheduleBtn.className = "btn btn--ghost";
  scheduleBtn.textContent = "Yes — schedule & send";
  scheduleBtn.hidden = !showSchedule;

  if (suggestion.details?.calendar_event_id) {
    scheduleBtn.textContent = "On calendar";
    scheduleBtn.disabled = true;
    if (showDraft) {
      sendBtn.textContent = "Send confirmation";
    }
  }

  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.className = "btn btn--ghost";
  dismissBtn.textContent = "Dismiss";

  const stopGroupBtn = document.createElement("button");
  stopGroupBtn.type = "button";
  stopGroupBtn.className = "btn btn--ghost btn--warning";
  stopGroupBtn.textContent = "Stop reading this group";
  stopGroupBtn.hidden = !suggestion.is_group;

  const wrongBtn = document.createElement("button");
  wrongBtn.type = "button";
  wrongBtn.className = "btn btn--ghost btn--warning";
  wrongBtn.textContent = "Wrong";

  actions.append(sendBtn, editBtn, scheduleBtn, dismissBtn, wrongBtn, stopGroupBtn);

  const correctionPanel = document.createElement("div");
  correctionPanel.className = "whatsapp-card__correction";
  correctionPanel.hidden = true;

  const correctionLabel = document.createElement("p");
  correctionLabel.className = "language-card__label";
  correctionLabel.textContent = "What should the correct response have been?";

  const correctionInput = document.createElement("textarea");
  correctionInput.className = "whatsapp-card__correction-input";
  correctionInput.rows = 2;
  correctionInput.placeholder = "e.g. Should have said we're out of stock until next week.";

  const correctionActions = document.createElement("div");
  correctionActions.className = "language-card__actions";

  const correctionSaveBtn = document.createElement("button");
  correctionSaveBtn.type = "button";
  correctionSaveBtn.className = "btn btn--primary";
  correctionSaveBtn.textContent = "Save correction";

  const correctionCancelBtn = document.createElement("button");
  correctionCancelBtn.type = "button";
  correctionCancelBtn.className = "btn btn--ghost";
  correctionCancelBtn.textContent = "Cancel";

  correctionActions.append(correctionSaveBtn, correctionCancelBtn);
  correctionPanel.append(correctionLabel, correctionInput, correctionActions);

  function currentDraftText() {
    return draftInput.hidden ? draftText.textContent.trim() : draftInput.value.trim();
  }

  function setEditing(editing) {
    draftInput.hidden = !editing;
    draftText.hidden = editing;
    editBtn.textContent = editing ? "Done" : "Edit";
    if (editing) {
      draftInput.value = draftText.textContent;
      draftInput.focus();
    } else {
      draftText.textContent = draftInput.value.trim() || defaultDraft(suggestion);
    }
  }

  editBtn.addEventListener("click", () => {
    setEditing(draftInput.hidden);
  });

  async function sendDraft() {
    const text = currentDraftText();
    if (!text || sendBtn.disabled) {
      return;
    }

    sendBtn.disabled = true;
    const originalLabel = sendBtn.textContent;
    sendBtn.textContent = "Sending…";

    try {
      await sendSuggestionReply(suggestion.id, { text, mode: "auto" });
      sendBtn.textContent = "Sent ✓";
      card.classList.add("whatsapp-card--sent");
      if (typeof onSent === "function") {
        await onSent();
      }
    } catch (error) {
      sendBtn.textContent = "Send failed";
      sendBtn.disabled = false;
      sendBtn.title = String(error.message || error);
      window.setTimeout(() => {
        sendBtn.textContent = originalLabel;
        sendBtn.title = "";
      }, 3000);
    }
  }

  sendBtn.addEventListener("click", sendDraft);

  draftInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    setEditing(false);
    sendDraft();
  });

  scheduleBtn.addEventListener("click", async () => {
    if (typeof onSchedule !== "function") {
      return;
    }
    scheduleBtn.disabled = true;
    scheduleBtn.textContent = "Scheduling…";
    try {
      await onSchedule(suggestion, scheduleBtn);
      if (scheduleBtn.textContent.includes("Scheduled")) {
        if (!scheduleBtn.textContent.includes("send reply")) {
          card.classList.add("whatsapp-card--sent");
        }
        if (typeof onSent === "function") {
          await onSent();
        }
      }
    } finally {
      if (!scheduleBtn.textContent.includes("Scheduled")) {
        scheduleBtn.disabled = false;
        scheduleBtn.textContent = "Yes — schedule & send";
      }
    }
  });

  dismissBtn.addEventListener("click", async () => {
    if (typeof onDismiss !== "function") {
      return;
    }
    dismissBtn.disabled = true;
    try {
      await onDismiss(suggestion);
    } catch {
      dismissBtn.disabled = false;
    }
  });

  stopGroupBtn.addEventListener("click", async () => {
    if (typeof onExcludeGroup !== "function") {
      return;
    }
    stopGroupBtn.disabled = true;
    const originalLabel = stopGroupBtn.textContent;
    stopGroupBtn.textContent = "Stopping…";
    try {
      await onExcludeGroup(suggestion);
    } catch (error) {
      stopGroupBtn.disabled = false;
      stopGroupBtn.textContent = originalLabel;
      stopGroupBtn.title = String(error.message || error);
      window.setTimeout(() => {
        stopGroupBtn.title = "";
      }, 3000);
    }
  });

  wrongBtn.addEventListener("click", () => {
    correctionPanel.hidden = !correctionPanel.hidden;
    if (!correctionPanel.hidden) {
      correctionInput.value = "";
      correctionInput.focus();
    }
  });

  correctionCancelBtn.addEventListener("click", () => {
    correctionPanel.hidden = true;
  });

  correctionSaveBtn.addEventListener("click", async () => {
    const correctResponse = correctionInput.value.trim();
    if (!correctResponse) {
      return;
    }
    if (typeof onWrong !== "function") {
      return;
    }
    correctionSaveBtn.disabled = true;
    const originalLabel = correctionSaveBtn.textContent;
    correctionSaveBtn.textContent = "Saving…";
    try {
      await onWrong(suggestion, correctResponse);
      correctionSaveBtn.textContent = "Saved ✓";
      card.classList.add("whatsapp-card--sent");
    } catch (error) {
      correctionSaveBtn.disabled = false;
      correctionSaveBtn.textContent = originalLabel;
      correctionSaveBtn.title = String(error.message || error);
      window.setTimeout(() => {
        correctionSaveBtn.title = "";
      }, 3000);
    }
  });

  card.append(header, original, meta, draftSection, actions, correctionPanel);
  return card;
}
