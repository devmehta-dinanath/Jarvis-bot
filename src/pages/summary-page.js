import {
  dismissSuggestion,
  getPendingMeetingSuggestions,
  scheduleMeetingSuggestion
} from "../lib/api.js";
import { getGreeting } from "../lib/time.js";
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

function createEmptyState(message) {
  const empty = document.createElement("p");
  empty.className = "os-empty-state";
  empty.textContent = message;
  return empty;
}

async function handleSchedule(suggestion, button) {
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Scheduling…";

  try {
    await scheduleMeetingSuggestion(suggestion.id);
    button.textContent = "Scheduled";
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
  page.className = "os-page os-page--now";

  const hero = document.createElement("article");
  hero.className = "hero-card";

  const greeting = document.createElement("h2");
  greeting.className = "hero-card__greeting";
  greeting.textContent = getGreeting("Sujay");

  const stats = document.createElement("div");
  stats.className = "stat-row";

  const meetingSection = document.createElement("section");
  meetingSection.className = "os-section";

  const sectionHeader = createSectionHeader("Wants to meet", "info", 0);
  meetingSection.appendChild(sectionHeader);

  const cardList = document.createElement("div");
  cardList.className = "whatsapp-card-list";
  meetingSection.appendChild(cardList);

  hero.append(greeting, stats);
  page.append(hero, meetingSection);

  async function reload() {
    let suggestions = [];

    try {
      const data = await getPendingMeetingSuggestions();
      suggestions = dedupeSuggestions(
        (data.items ?? []).filter((item) => item.kind === "meeting")
      );
    } catch (error) {
      cardList.replaceChildren(
        createEmptyState(`Could not load WhatsApp requests — ${error.message}`)
      );
      stats.replaceChildren(createStatChip("—", "Meetings", "info"));
      sectionHeader.querySelector(".section-header__count").textContent = "0";
      return;
    }

    stats.replaceChildren(
      createStatChip(suggestions.length, "Call requests", suggestions.length ? "urgent" : "success"),
      createStatChip(suggestions.length, "WhatsApp", "info")
    );

    const countEl = sectionHeader.querySelector(".section-header__count");
    if (countEl) {
      countEl.textContent = String(suggestions.length);
    }

    if (suggestions.length === 0) {
      cardList.replaceChildren(
        createEmptyState("No pending call requests from WhatsApp.")
      );
      return;
    }

    cardList.replaceChildren();

    suggestions.forEach((suggestion) => {
      cardList.appendChild(
        createWhatsAppRequestCard(suggestion, {
          onSchedule: handleSchedule,
          onDismiss: async (item) => {
            await dismissSuggestion(item.id);
            await reload();
          },
          onSent: reload
        })
      );
    });
  }

  reload();
  const timer = window.setInterval(reload, REFRESH_MS);
  page.addEventListener("jarvis:destroy", () => window.clearInterval(timer));

  return page;
}
