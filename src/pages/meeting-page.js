import { createCard, createInfoCard, createPlaceholderRows } from "../ui/dom-helpers.js";

export function createMeetingPage(source) {
  const page = document.createElement("section");
  page.className = "tab-panel page page--meeting";

  const heroCard = createCard(
    "insight-card insight-card--warning",
    "Meeting tab",
    "Waiting for integration",
    "Keep this section for meeting notes, action items, schedules, and coordination alerts."
  );

  const placeholderCard = createInfoCard(
    "Meeting Placeholder",
    "Future area for agenda, owners, notes, and auto-detected actions."
  );
  placeholderCard.appendChild(createPlaceholderRows(2, true));

  page.append(heroCard, placeholderCard);

  return page;
}
