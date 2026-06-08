import { createCard, createInfoCard, createPlaceholderRows } from "../ui/dom-helpers.js";

export function createSmartMessagingPage(source) {
  const page = document.createElement("section");
  page.className = "tab-panel page page--smart-messaging";

  const heroCard = createCard(
    "insight-card insight-card--primary",
    "Ready for API data",
    "Smart Messaging",
    "Keep this area for smart replies, tone suggestions, and follow-up prompts once the API integration is ready."
  );

  const grid = document.createElement("div");
  grid.className = "content-grid";

  const repliesCard = createInfoCard(
    "Suggested Replies",
    "Empty state for AI-generated replies, acknowledgements, and follow-up prompts."
  );
  repliesCard.appendChild(createPlaceholderRows(3, true));

  const signalsCard = createInfoCard(
    "Workflow Signals",
    "Reserve this block for urgency, tone, owner, and next-step guidance."
  );

  const badgeStack = document.createElement("div");
  badgeStack.className = "status-stack";
  ["Priority", "Follow-up", "Pending API"].forEach((label) => {
    const badge = document.createElement("span");
    badge.className = "status-badge";
    badge.textContent = label;
    badgeStack.appendChild(badge);
  });
  signalsCard.appendChild(badgeStack);

  grid.append(repliesCard, signalsCard);
  page.append(heroCard, grid);

  return page;
}
