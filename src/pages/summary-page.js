import {
  dismissSuggestion,
  getInboxStatus,
  getPendingSuggestions,
  getWhatsAppCategories,
  refreshPendingInbox,
  scheduleMeetingSuggestion
} from "../lib/api.js";
import {
  applyTaxonomyFromApi,
  CATEGORY_SECTIONS,
  partitionSuggestions
} from "../lib/whatsapp-categories.js";
import { formatDateTime, getGreeting } from "../lib/time.js";
import { createSectionHeader } from "../ui/components/section-header.js";
import { createStatChip } from "../ui/components/stat-chip.js";
import { createWhatsAppRequestCard } from "../ui/components/whatsapp-request-card.js";

const REFRESH_MS = 15000;

function dedupeSuggestions(suggestions) {
  const seen = new Set();
  return suggestions.filter((suggestion) => {
    const key = suggestion.message_id ?? suggestion.id;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function formatInboxHint(status) {
  if (!status?.last_inbound_at) {
    return "No client messages received yet. Point the Meta webhook to this server and send a test message from another phone.";
  }

  const when = new Date(status.last_inbound_at);
  const preview = status.last_inbound_preview ? ` — “${status.last_inbound_preview}”` : "";
  const ageHours = Math.round((Date.now() - when.getTime()) / 3600000);

  const whenLabel = formatDateTime(when, {
    day: "numeric",
    month: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    timeZoneName: "short"
  });

  if (ageHours > 24) {
    return `Last client message was ${whenLabel}${preview}. If you messaged today, the WhatsApp webhook is not reaching Jarvis.`;
  }

  return `Last client message ${whenLabel}${preview}.`;
}

function createEmptyState(message, hint) {
  const wrap = document.createElement("div");
  wrap.className = "os-empty-state-wrap";

  const empty = document.createElement("p");
  empty.className = "os-empty-state";
  empty.textContent = message;
  wrap.appendChild(empty);

  if (hint) {
    const hintEl = document.createElement("p");
    hintEl.className = "os-empty-state os-empty-state--hint";
    hintEl.textContent = hint;
    wrap.appendChild(hintEl);
  }

  return wrap;
}

function createSuggestionSection({ title, accent }) {
  const section = document.createElement("section");
  section.className = "os-section";

  const header = createSectionHeader(title, accent, 0);
  const cardList = document.createElement("div");
  cardList.className = "whatsapp-card-list";

  section.append(header, cardList);
  return { section, header, cardList };
}

function renderSectionCards(cardList, suggestions, handlers) {
  cardList.replaceChildren();
  if (suggestions.length === 0) {
    return;
  }

  suggestions.forEach((suggestion) => {
    cardList.appendChild(createWhatsAppRequestCard(suggestion, handlers));
  });
}

async function handleSchedule(suggestion, button) {
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Scheduling…";

  try {
    const result = await scheduleMeetingSuggestion(suggestion.id);
    if (result.reply_sent) {
      button.textContent = "Scheduled & sent ✓";
    } else if (result.reply_error) {
      button.textContent = "Scheduled — send reply";
      button.title = result.reply_error;
    } else {
      button.textContent = "Scheduled";
    }
  } catch (error) {
    button.textContent = "Try again";
    button.disabled = false;
    button.title = String(error.message || error);
    window.setTimeout(() => {
      button.textContent = originalLabel;
      button.title = "";
    }, 3000);
    throw error;
  }
}

export function createSummaryPage() {
  const page = document.createElement("section");
  page.className = "os-page os-page--whatsapp";

  const hero = document.createElement("article");
  hero.className = "hero-card";

  const greeting = document.createElement("h2");
  greeting.className = "hero-card__greeting";
  greeting.textContent = getGreeting("Sujay");

  const stats = document.createElement("div");
  stats.className = "stat-row";

  const sections = CATEGORY_SECTIONS.map((config) => ({
    config,
    ...createSuggestionSection(config)
  }));

  const emptyState = createEmptyState(
    "No pending WhatsApp items right now.",
    "Checking inbox…"
  );
  emptyState.hidden = true;

  hero.append(greeting, stats);
  page.append(hero, ...sections.map((item) => item.section), emptyState);

  const handlers = {
    onSchedule: handleSchedule,
    onDismiss: async (item) => {
      await dismissSuggestion(item.id);
      await reload();
    },
    onSent: reload
  };

  async function reload() {
    let suggestions = [];
    let inboxStatus = null;

    try {
      await refreshPendingInbox();
      const [pendingData, statusData] = await Promise.all([
        getPendingSuggestions(),
        getInboxStatus()
      ]);
      inboxStatus = statusData;
      suggestions = dedupeSuggestions(pendingData.items ?? []);
    } catch (error) {
      sections.forEach((item) => {
        item.cardList.replaceChildren(
          createEmptyState(`Could not load — ${error.message}`, null)
        );
        item.section.hidden = false;
      });
      emptyState.hidden = true;
      stats.replaceChildren(createStatChip("—", "WhatsApp", "info"));
      return;
    }

    const buckets = partitionSuggestions(suggestions);
    const urgentCount = buckets.urgent?.length ?? 0;
    const workCount =
      (buckets.meetings?.length ?? 0) +
      (buckets.replies?.length ?? 0) +
      urgentCount;
    const lifeCount =
      (buckets.life?.length ?? 0) + (buckets.nudges?.length ?? 0);

    stats.replaceChildren(
      createStatChip(urgentCount, "Urgent", urgentCount ? "urgent" : "success"),
      createStatChip(workCount, "Work", workCount ? "info" : "success"),
      createStatChip(lifeCount, "Life", lifeCount ? "info" : "success")
    );

    let visibleSections = 0;

    sections.forEach((item) => {
      const items = buckets[item.config.id] ?? [];
      const countEl = item.header.querySelector(".section-header__count");
      if (countEl) {
        countEl.textContent = String(items.length);
      }

      item.section.hidden = items.length === 0;
      if (items.length > 0) {
        visibleSections += 1;
      }

      item.cardList.replaceChildren();
      renderSectionCards(item.cardList, items, handlers);
    });

    emptyState.hidden = visibleSections > 0;
    if (!emptyState.hidden) {
      emptyState.replaceChildren(
        ...createEmptyState(
          "No pending WhatsApp items right now.",
          formatInboxHint(inboxStatus)
        ).childNodes
      );
    }
  }

  reload();
  getWhatsAppCategories().then(applyTaxonomyFromApi).catch(() => {});
  const timer = window.setInterval(reload, REFRESH_MS);
  page.addEventListener("jarvis:destroy", () => window.clearInterval(timer));

  return page;
}
