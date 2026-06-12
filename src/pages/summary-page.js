import { getPowerWindow, formatHourLabel } from "../lib/time.js";
import { createSectionHeader } from "../ui/components/section-header.js";
import { createStatChip } from "../ui/components/stat-chip.js";

const DAILY_HIGHLIGHTS = [
  "3 follow-ups overdue — Rohan, Pierre, and Acme Corp.",
  "Payment reminder due for Invoice #2841 by end of day.",
  "Team standup notes still need your approval."
];

const HOURLY_TIMELINE = [
  { time: "9:00", label: "Power window — deep work", active: true },
  { time: "10:30", label: "Reply to Pierre (French)", active: false },
  { time: "11:00", label: "Sync with Rohan", active: false },
  { time: "14:00", label: "Afternoon focus block", active: false },
  { time: "16:30", label: "Review OKR progress", active: false }
];

export function createSummaryPage() {
  const page = document.createElement("section");
  page.className = "os-page os-page--summary";

  const powerWindow = getPowerWindow();
  const currentHour = new Date().getHours();

  const hero = document.createElement("article");
  hero.className = "hero-card";

  const context = document.createElement("p");
  context.className = "hero-card__context";
  context.textContent = powerWindow.label;

  const stats = document.createElement("div");
  stats.className = "stat-row";
  stats.append(
    createStatChip(6, "Follow-ups", "urgent"),
    createStatChip(2, "Payments", "warning"),
    createStatChip(1, "FR Message", "info"),
    createStatChip(2, "Meetings", "success")
  );

  hero.append(context, stats);
  page.appendChild(hero);

  const dailySection = document.createElement("section");
  dailySection.className = "os-section";
  dailySection.appendChild(createSectionHeader("Daily Summary", "accent"));

  const dailyCard = document.createElement("article");
  dailyCard.className = "content-card";

  const dailyTitle = document.createElement("h4");
  dailyTitle.className = "content-card__title";
  dailyTitle.textContent = "Today's overview";

  const dailyList = document.createElement("ul");
  dailyList.className = "content-card__list";

  DAILY_HIGHLIGHTS.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    dailyList.appendChild(li);
  });

  dailyCard.append(dailyTitle, dailyList);
  dailySection.appendChild(dailyCard);
  page.appendChild(dailySection);

  const hourlySection = document.createElement("section");
  hourlySection.className = "os-section";
  hourlySection.appendChild(
    createSectionHeader(`Hourly — ${formatHourLabel(currentHour)} block`, "info")
  );

  const hourlyGrid = document.createElement("div");
  hourlyGrid.className = "timeline";

  HOURLY_TIMELINE.forEach((slot) => {
    const item = document.createElement("div");
    item.className = `timeline__item${slot.active ? " timeline__item--active" : ""}`;

    const time = document.createElement("span");
    time.className = "timeline__time";
    time.textContent = slot.time;

    const label = document.createElement("span");
    label.className = "timeline__label";
    label.textContent = slot.label;

    item.append(time, label);
    hourlyGrid.appendChild(item);
  });

  hourlySection.appendChild(hourlyGrid);
  page.appendChild(hourlySection);

  return page;
}
