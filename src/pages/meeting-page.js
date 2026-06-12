import { createActionCard } from "../ui/components/action-card.js";
import { createSectionHeader } from "../ui/components/section-header.js";
import { createStatChip } from "../ui/components/stat-chip.js";

const TODAY_MEETINGS = [
  {
    time: "11:00",
    duration: "30 min",
    title: "Sync with Rohan",
    attendees: ["Rohan Mehta", "You"],
    prep: "Discuss launch timeline shift and blockers.",
    status: "upcoming"
  },
  {
    time: "15:00",
    duration: "45 min",
    title: "Acme Corp — Payment Review",
    attendees: ["Finance team", "Acme Corp", "You"],
    prep: "Invoice #2841 follow-up. Have updated price list ready.",
    status: "upcoming"
  }
];

const MEETING_ACTIONS = [
  {
    title: "Send pre-read to Rohan before 11am sync",
    description: "Include launch timeline and dependency list.",
    accent: "info",
    actions: [{ label: "Draft message", variant: "primary", primary: true }]
  },
  {
    title: "Prepare price list for Acme meeting",
    description: "Pierre also requested this — kill two birds.",
    accent: "warning",
    actions: [{ label: "Open file", variant: "primary", primary: true }]
  }
];

function createMeetingCard(meeting) {
  const card = document.createElement("article");
  card.className = `meeting-card meeting-card--${meeting.status}`;

  const timeBlock = document.createElement("div");
  timeBlock.className = "meeting-card__time";

  const time = document.createElement("span");
  time.className = "meeting-card__hour";
  time.textContent = meeting.time;

  const duration = document.createElement("span");
  duration.className = "meeting-card__duration";
  duration.textContent = meeting.duration;

  timeBlock.append(time, duration);

  const body = document.createElement("div");
  body.className = "meeting-card__body";

  const title = document.createElement("h4");
  title.className = "meeting-card__title";
  title.textContent = meeting.title;

  const attendees = document.createElement("p");
  attendees.className = "meeting-card__attendees";
  attendees.textContent = meeting.attendees.join(" · ");

  const prep = document.createElement("p");
  prep.className = "meeting-card__prep";
  prep.textContent = meeting.prep;

  body.append(title, attendees, prep);
  card.append(timeBlock, body);

  return card;
}

export function createMeetingPage() {
  const page = document.createElement("section");
  page.className = "os-page os-page--meetings";

  const hero = document.createElement("article");
  hero.className = "hero-card hero-card--compact";

  const greeting = document.createElement("h2");
  greeting.className = "hero-card__greeting";
  greeting.textContent = "Today's meetings";

  const context = document.createElement("p");
  context.className = "hero-card__context";
  context.textContent = `${TODAY_MEETINGS.length} scheduled · 1h 15m total`;

  const stats = document.createElement("div");
  stats.className = "stat-row";
  stats.append(
    createStatChip(TODAY_MEETINGS.length, "Today", "success"),
    createStatChip(1, "Prep needed", "warning"),
    createStatChip(0, "Overdue", "urgent")
  );

  hero.append(greeting, context, stats);
  page.appendChild(hero);

  const scheduleSection = document.createElement("section");
  scheduleSection.className = "os-section";
  scheduleSection.appendChild(createSectionHeader("Schedule", "success"));

  const meetingList = document.createElement("div");
  meetingList.className = "meeting-list";

  TODAY_MEETINGS.forEach((meeting) => {
    meetingList.appendChild(createMeetingCard(meeting));
  });

  scheduleSection.appendChild(meetingList);
  page.appendChild(scheduleSection);

  const prepSection = document.createElement("section");
  prepSection.className = "os-section";
  prepSection.appendChild(createSectionHeader("Prep & Actions", "warning", MEETING_ACTIONS.length));

  const actionList = document.createElement("div");
  actionList.className = "action-list";

  MEETING_ACTIONS.forEach((item) => {
    actionList.appendChild(createActionCard(item));
  });

  prepSection.appendChild(actionList);
  page.appendChild(prepSection);

  return page;
}
