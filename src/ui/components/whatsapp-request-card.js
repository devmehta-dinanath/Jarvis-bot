import { sendSuggestionReply } from "../../lib/api.js";
import { formatMeetingTime } from "../../lib/time.js";

function displayName(suggestion) {
  return suggestion.contact_name || suggestion.wa_id || "WhatsApp contact";
}

function defaultDraft(suggestion) {
  return (
    suggestion.draft_text ||
    "Thanks for reaching out. Happy to connect — I'll confirm the time shortly."
  );
}

export function createWhatsAppRequestCard(suggestion, { onSchedule, onDismiss, onSent }) {
  const card = document.createElement("article");
  card.className = "language-card language-card--whatsapp";

  const header = document.createElement("div");
  header.className = "language-card__header";

  const contact = document.createElement("h3");
  contact.className = "language-card__contact";
  contact.textContent = displayName(suggestion);

  const badge = document.createElement("span");
  badge.className = "language-card__badge";
  badge.textContent = "Wants to meet";

  header.append(contact, badge);

  const original = document.createElement("blockquote");
  original.className = "language-card__original";
  original.textContent =
    suggestion.message_body || suggestion.message_summary || "No message text.";

  const meta = document.createElement("p");
  meta.className = "whatsapp-card__meta";

  const metaParts = [];
  if (suggestion.details?.start) {
    metaParts.push(`Proposed: ${formatMeetingTime(suggestion.details.start)}`);
  }
  const calendarStatus = suggestion.details?.calendar_status;
  if (calendarStatus === "available") {
    metaParts.push("Slot is free");
  } else if (calendarStatus === "busy") {
    metaParts.push("Slot looks busy");
  }
  meta.textContent = metaParts.join(" · ");
  meta.hidden = metaParts.length === 0;

  const draftSection = document.createElement("div");
  draftSection.className = "language-card__draft";

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
  sendBtn.textContent = "Send reply";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn btn--ghost";
  editBtn.textContent = "Edit";

  const scheduleBtn = document.createElement("button");
  scheduleBtn.type = "button";
  scheduleBtn.className = "btn btn--ghost";
  scheduleBtn.textContent = "Schedule meeting";

  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.className = "btn btn--ghost";
  dismissBtn.textContent = "Dismiss";

  actions.append(sendBtn, editBtn, scheduleBtn, dismissBtn);

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

  sendBtn.addEventListener("click", async () => {
    const text = currentDraftText();
    if (!text) {
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
  });

  scheduleBtn.addEventListener("click", async () => {
    if (typeof onSchedule !== "function") {
      return;
    }
    scheduleBtn.disabled = true;
    scheduleBtn.textContent = "Scheduling…";
    try {
      await onSchedule(suggestion, scheduleBtn);
    } finally {
      if (!scheduleBtn.textContent.includes("Scheduled")) {
        scheduleBtn.disabled = false;
        scheduleBtn.textContent = "Schedule meeting";
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

  card.append(header, original, meta, draftSection, actions);
  return card;
}
