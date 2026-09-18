/** Small UI helpers for WhatsApp Inbox cards (toast + external links). */

const URL_IN_TEXT = /(https?:\/\/[^\s<>"']+)/gi;

export function openExternal(url) {
  if (!url) {
    return;
  }
  if (window.jarvisApp?.openExternal) {
    window.jarvisApp.openExternal(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

export function showInboxToast(message, { timeoutMs = 4500 } = {}) {
  let host = document.getElementById("jarvis-inbox-toast-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "jarvis-inbox-toast-host";
    host.style.cssText = [
      "position:fixed",
      "right:20px",
      "bottom:20px",
      "z-index:9999",
      "display:flex",
      "flex-direction:column",
      "gap:8px",
      "max-width:min(360px, calc(100vw - 32px))",
      "pointer-events:none"
    ].join(";");
    document.body.appendChild(host);
  }

  const toast = document.createElement("div");
  toast.setAttribute("role", "status");
  toast.style.cssText = [
    "pointer-events:auto",
    "background:#12241a",
    "color:#e8f5e9",
    "border:1px solid #2e7d32",
    "border-radius:10px",
    "padding:12px 14px",
    "font:500 13px/1.4 system-ui,sans-serif",
    "box-shadow:0 8px 24px rgba(0,0,0,.25)",
    "white-space:pre-wrap"
  ].join(";");
  toast.textContent = message;
  host.appendChild(toast);
  window.setTimeout(() => {
    toast.remove();
    if (host.childElementCount === 0) {
      host.remove();
    }
  }, timeoutMs);
}

/** Turn plain-text URLs into clickable links (opens via jarvisApp.openExternal). */
export function linkifyElement(el) {
  if (!el || el.dataset.jarvisLinkified === "1") {
    return;
  }
  const text = el.textContent || "";
  if (!URL_IN_TEXT.test(text)) {
    return;
  }
  URL_IN_TEXT.lastIndex = 0;

  const frag = document.createDocumentFragment();
  let lastIndex = 0;
  let match;
  while ((match = URL_IN_TEXT.exec(text))) {
    if (match.index > lastIndex) {
      frag.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
    }
    const url = match[1].replace(/[),.;!?]+$/g, "");
    const trailing = match[1].slice(url.length);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.textContent = url;
    anchor.rel = "noopener noreferrer";
    anchor.target = "_blank";
    anchor.style.color = "#7ec8ff";
    anchor.style.textDecoration = "underline";
    anchor.style.cursor = "pointer";
    anchor.style.wordBreak = "break-all";
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      openExternal(url);
    });
    frag.appendChild(anchor);
    if (trailing) {
      frag.appendChild(document.createTextNode(trailing));
    }
    lastIndex = match.index + match[1].length;
  }
  if (lastIndex < text.length) {
    frag.appendChild(document.createTextNode(text.slice(lastIndex)));
  }
  el.replaceChildren(frag);
  el.dataset.jarvisLinkified = "1";
}

function extractMeetUrl(...candidates) {
  for (const value of candidates) {
    if (typeof value !== "string" || !value) {
      continue;
    }
    const direct = value.match(/https?:\/\/meet\.google\.com\/[a-z0-9-]+/i);
    if (direct) {
      return direct[0];
    }
  }
  return null;
}

/**
 * After schedule / auto-remind, add clear Join / Open actions on the card
 * without depending on root-owned request-card edits.
 */
export function enhanceWhatsAppCard(card, suggestion) {
  const draftText = card.querySelector(".language-card__draft-text");
  if (draftText) {
    linkifyElement(draftText);
  }
  const original = card.querySelector(".language-card__original");
  if (original) {
    linkifyElement(original);
  }

  const actions = card.querySelector(".language-card__actions");
  if (!actions) {
    return card;
  }

  const details = suggestion.details || {};
  const calendarUrl = details.calendar_html_link || null;
  const reminderUrl = details.reminder_html_link || null;
  const draftBody = draftText?.textContent || suggestion.draft_text || "";
  const meetUrl = extractMeetUrl(details.meet_link, draftBody, calendarUrl);
  const joinUrl = meetUrl || calendarUrl;

  const remindBtn = Array.from(actions.querySelectorAll("button")).find((btn) =>
    /remind/i.test(btn.textContent || "")
  );
  if (remindBtn && details.reminder_event_id) {
    remindBtn.textContent = "Reminder set ✓";
    remindBtn.disabled = true;
    remindBtn.style.borderColor = "#2e7d32";
    remindBtn.style.color = "#1b5e20";
    remindBtn.title = details.reminder_at
      ? "Confirmed on your calendar"
      : "Reminder confirmed on your calendar";
  }

  // Show Join whenever a Meet/calendar link exists in details or draft text.
  if (joinUrl && !actions.querySelector("[data-jarvis-join-meet]")) {
    const joinBtn = document.createElement("button");
    joinBtn.type = "button";
    joinBtn.className = "btn btn--primary";
    joinBtn.dataset.jarvisJoinMeet = "1";
    joinBtn.textContent = meetUrl ? "Join Google Meet" : "Open Calendar event";
    joinBtn.title = joinUrl;
    joinBtn.addEventListener("click", () => openExternal(joinUrl));
    actions.appendChild(joinBtn);
  }

  if (reminderUrl && !actions.querySelector("[data-jarvis-open-reminder]")) {
    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "btn btn--ghost";
    openBtn.dataset.jarvisOpenReminder = "1";
    openBtn.textContent = "Open reminder";
    openBtn.title = reminderUrl;
    openBtn.addEventListener("click", () => openExternal(reminderUrl));
    actions.appendChild(openBtn);
  }

  return card;
}
