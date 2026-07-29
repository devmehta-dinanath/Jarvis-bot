import {
  answerClarification,
  forwardSuggestion,
  sendSuggestionFeedback,
  sendSuggestionReply
} from "../../lib/api.js";
import { applyAvatarGradient } from "../../lib/avatar-color.js";
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

function isEnglishLanguage(language) {
  if (!language) {
    return true;
  }
  return ["english", "en", "en-us", "en-gb"].includes(language.trim().toLowerCase());
}

function defaultDraft(suggestion) {
  // 7-day silent observation: never fall back to a canned line here — the whole point is
  // an empty box so whatever gets sent is genuinely the owner's own wording, not AI-authored.
  if (suggestion.details?.silent_observation) {
    return suggestion.draft_text || "";
  }
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

function scheduleButtonLabel(suggestion) {
  // Family plans never send a WhatsApp confirmation message — only meetings do.
  return resolveCategory(suggestion) === "family_plan" ? "Schedule" : "Yes — schedule & send";
}

export function createWhatsAppRequestCard(
  suggestion,
  { onSchedule, onDismiss, onSent, onExcludeGroup, onWrong, onInteractionChange }
) {
  function setInteracting(active) {
    if (typeof onInteractionChange === "function") {
      onInteractionChange(active);
    }
  }

  const card = document.createElement("article");
  card.className = "language-card language-card--whatsapp";
  if (isUrgent(suggestion)) {
    card.classList.add("language-card--urgent");
  }

  const category = resolveCategory(suggestion);
  const showDraft = canSendReply(suggestion);
  const showSchedule = canSchedule(suggestion);
  // Rule 13 — smart clarifying questions: no draft yet, just a question + tap options.
  const needsClarification =
    suggestion.kind === "clarify" && suggestion.details?.needs_clarification === true;

  const header = document.createElement("div");
  header.className = "language-card__header";

  const identity = document.createElement("div");
  identity.className = "language-card__identity";

  const avatar = document.createElement("span");
  avatar.className = "conversation-item__avatar language-card__avatar";
  avatar.textContent = displayName(suggestion).charAt(0).toUpperCase();
  applyAvatarGradient(avatar, displayName(suggestion));

  const contact = document.createElement("h3");
  contact.className = "language-card__contact";
  contact.textContent = displayName(suggestion);

  identity.append(avatar, contact);

  const badge = document.createElement("span");
  badge.className = "language-card__badge";
  badge.textContent = categoryLabel(suggestion);

  header.append(identity, badge);

  const isForeignLanguage =
    Boolean(suggestion.message_language) && !isEnglishLanguage(suggestion.message_language);

  if (isForeignLanguage) {
    const languageBadge = document.createElement("span");
    languageBadge.className = "language-card__badge";
    languageBadge.textContent = `${suggestion.message_language} detected`;
    header.appendChild(languageBadge);
  }

  const original = document.createElement("blockquote");
  original.className = "language-card__original";
  original.textContent =
    suggestion.message_body || suggestion.message_summary || "No message text.";
  if (
    (category === "personal_silence" ||
      category === "awaiting_reply" ||
      category === "pending_commitment") &&
    !suggestion.message_body
  ) {
    original.textContent =
      suggestion.details?.chip_label ||
      `You haven't replied to ${displayName(suggestion)} yet.`;
  }

  const originalTranslation = document.createElement("p");
  originalTranslation.className = "language-card__draft-translation";
  originalTranslation.hidden = true;
  if (isForeignLanguage && suggestion.message_translation) {
    originalTranslation.textContent = `English: ${suggestion.message_translation}`;
    originalTranslation.hidden = false;
  }
  if (category === "media" && !suggestion.message_body) {
    original.textContent = suggestion.details?.chip_label || "Media message — no caption.";
  }

  const meta = document.createElement("p");
  meta.className = "whatsapp-card__meta";
  meta.textContent = buildCategoryMeta(suggestion, { formatMeetingTime });
  meta.hidden = meta.textContent.length === 0;

  const clarifySection = document.createElement("div");
  clarifySection.className = "language-card__clarify";
  clarifySection.hidden = !needsClarification;

  if (needsClarification) {
    const question = document.createElement("p");
    question.className = "language-card__clarify-question";
    question.textContent = suggestion.details?.clarifying_question || "Which one did you mean?";

    const optionRow = document.createElement("div");
    optionRow.className = "language-card__clarify-options";

    const options = Array.isArray(suggestion.details?.clarifying_options)
      ? suggestion.details.clarifying_options
      : [];

    options.forEach((optionText) => {
      const optionBtn = document.createElement("button");
      optionBtn.type = "button";
      optionBtn.className = "language-card__clarify-option";
      optionBtn.textContent = optionText;
      optionBtn.addEventListener("click", async () => {
        optionRow.querySelectorAll("button").forEach((btn) => {
          btn.disabled = true;
        });
        optionBtn.textContent = "...";
        try {
          await answerClarification(suggestion.id, optionText);
          if (typeof onSent === "function") {
            await onSent();
          }
        } catch (error) {
          optionRow.querySelectorAll("button").forEach((btn) => {
            btn.disabled = false;
          });
          optionBtn.textContent = optionText;
          optionBtn.title = String(error.message || error);
          window.setTimeout(() => {
            optionBtn.title = "";
          }, 3000);
        }
      });
      optionRow.appendChild(optionBtn);
    });

    clarifySection.append(question, optionRow);
  }

  const draftSection = document.createElement("div");
  draftSection.className = "language-card__draft";
  draftSection.hidden = !showDraft;

  const isSilentObservation = Boolean(suggestion.details?.silent_observation);

  const draftLabel = document.createElement("p");
  draftLabel.className = "language-card__label";
  draftLabel.textContent = isSilentObservation
    ? "Your reply — learning your tone, no AI draft"
    : isForeignLanguage
    ? `Draft reply — write in English, sent to them in ${suggestion.message_language}`
    : "Draft reply";

  const draftText = document.createElement("p");
  draftText.className = "language-card__draft-text";
  draftText.textContent = defaultDraft(suggestion);

  const draftInput = document.createElement("textarea");
  draftInput.className = "whatsapp-card__draft-input";
  draftInput.value = defaultDraft(suggestion);
  draftInput.rows = 3;
  draftInput.hidden = !isSilentObservation;
  draftInput.setAttribute("aria-label", "Edit draft reply");
  if (isSilentObservation) {
    draftInput.placeholder = "Type your reply…";
    draftText.hidden = true;
  }

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
  editBtn.textContent = isSilentObservation ? "Done" : "Edit";
  editBtn.hidden = !showDraft;

  const scheduleBtn = document.createElement("button");
  scheduleBtn.type = "button";
  scheduleBtn.className = "btn btn--ghost";
  scheduleBtn.textContent = scheduleButtonLabel(suggestion);
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

  // Rule 14 — forward to team: one tap, no copy/paste, no switching apps. The backend
  // only ever sets forward_label once a real team member is assigned to this category
  // in Settings and this suggestion hasn't already been forwarded.
  const forwardBtn = document.createElement("button");
  forwardBtn.type = "button";
  forwardBtn.className = "btn btn--ghost";
  forwardBtn.hidden = !suggestion.forward_label && !suggestion.forwarded_to;
  if (suggestion.forwarded_to) {
    forwardBtn.textContent = `Forwarded to ${suggestion.forwarded_to} ✓`;
    forwardBtn.disabled = true;
  } else {
    forwardBtn.textContent = `Forward to ${suggestion.forward_label}`;
  }

  const stopGroupBtn = document.createElement("button");
  stopGroupBtn.type = "button";
  stopGroupBtn.className = "btn btn--ghost btn--warning";
  stopGroupBtn.textContent = "Stop reading this group";
  stopGroupBtn.hidden = !suggestion.is_group;

  const wrongBtn = document.createElement("button");
  wrongBtn.type = "button";
  wrongBtn.className = "btn btn--ghost btn--warning";
  wrongBtn.textContent = "Wrong";
  wrongBtn.hidden = needsClarification;

  actions.append(sendBtn, editBtn, scheduleBtn, forwardBtn, dismissBtn, wrongBtn, stopGroupBtn);

  const correctionPanel = document.createElement("div");
  correctionPanel.className = "whatsapp-card__correction";
  correctionPanel.hidden = true;

  const correctionLabel = document.createElement("p");
  correctionLabel.className = "language-card__label";
  correctionLabel.textContent = isForeignLanguage
    ? `What should the correct response have been? Write in English — it'll be sent to the contact in ${suggestion.message_language} now.`
    : "What should the correct response have been? This will be sent to the contact now.";

  const correctionInput = document.createElement("textarea");
  correctionInput.className = "whatsapp-card__correction-input";
  correctionInput.rows = 2;
  correctionInput.placeholder = "e.g. Should have said we're out of stock until next week.";

  const correctionActions = document.createElement("div");
  correctionActions.className = "language-card__actions";

  const correctionSaveBtn = document.createElement("button");
  correctionSaveBtn.type = "button";
  correctionSaveBtn.className = "btn btn--primary";
  correctionSaveBtn.textContent = "Send & save correction";

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

  // Focus-driven instead of click-driven: covers both the "click Edit first" flow above
  // and the silent-observation box, which is editable from the moment it renders with no
  // Edit click to hook into. Without this, typing here is invisible to the auto-refresh
  // guard and the periodic reload wipes the in-progress draft out from under the user.
  draftInput.addEventListener("focus", () => setInteracting(true));
  draftInput.addEventListener("blur", () => setInteracting(false));

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
        scheduleBtn.textContent = scheduleButtonLabel(suggestion);
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

  forwardBtn.addEventListener("click", async () => {
    if (forwardBtn.disabled) {
      return;
    }
    forwardBtn.disabled = true;
    const originalLabel = forwardBtn.textContent;
    forwardBtn.textContent = "Forwarding…";
    try {
      const updated = await forwardSuggestion(suggestion.id);
      forwardBtn.textContent = `Forwarded to ${updated.forwarded_to ?? suggestion.forward_label} ✓`;
      if (typeof onSent === "function") {
        await onSent();
      }
    } catch (error) {
      forwardBtn.disabled = false;
      forwardBtn.textContent = originalLabel;
      forwardBtn.title = String(error.message || error);
      window.setTimeout(() => {
        forwardBtn.title = "";
      }, 3000);
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
    const isOpen = !correctionPanel.hidden;
    if (isOpen) {
      correctionInput.value = "";
      correctionInput.focus();
    }
    setInteracting(isOpen);
  });

  correctionCancelBtn.addEventListener("click", () => {
    correctionPanel.hidden = true;
    setInteracting(false);
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
    correctionSaveBtn.textContent = "Sending…";
    try {
      await onWrong(suggestion, correctResponse);
      correctionSaveBtn.textContent = "Sent ✓";
      card.classList.add("whatsapp-card--sent");
      setInteracting(false);
    } catch (error) {
      correctionSaveBtn.disabled = false;
      correctionSaveBtn.textContent = originalLabel;
      correctionSaveBtn.title = String(error.message || error);
      window.setTimeout(() => {
        correctionSaveBtn.title = "";
      }, 3000);
    }
  });

  card.append(header, original, originalTranslation, meta, clarifySection, draftSection, actions, correctionPanel);
  return card;
}
