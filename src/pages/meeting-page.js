import {
  getCalendarStatus,
  getScheduledMeetingSuggestions,
  getTodayCalendarEvents
} from "../lib/api.js";
import { formatClockTime, formatMeetingTime, isToday, meetingDurationMinutes } from "../lib/time.js";
import { createActionCard } from "../ui/components/action-card.js";
import { createSectionHeader } from "../ui/components/section-header.js";
import { createStatChip } from "../ui/components/stat-chip.js";

const REFRESH_MS = 30000;

function joinCall(url) {
  if (!url) {
    return;
  }

  if (window.jarvisApp?.openExternal) {
    window.jarvisApp.openExternal(url);
    return;
  }

  window.open(url, "_blank", "noopener,noreferrer");
}

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
  attendees.textContent = meeting.attendees.join(" · ") || "Calendar event";

  const prep = document.createElement("p");
  prep.className = "meeting-card__prep";
  prep.textContent = meeting.prep;

  body.append(title, attendees, prep);

  const callBtn = document.createElement("button");
  callBtn.type = "button";
  callBtn.className = "meeting-card__call btn btn--primary";
  callBtn.innerHTML =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg> Call';
  callBtn.setAttribute("aria-label", `Join call for ${meeting.title}`);
  callBtn.disabled = !meeting.callUrl;
  callBtn.addEventListener("click", () => joinCall(meeting.callUrl));

  card.append(timeBlock, body, callBtn);

  return card;
}

function createEmptyState(message) {
  const empty = document.createElement("p");
  empty.className = "os-empty-state";
  empty.textContent = message;
  return empty;
}

function mapCalendarEvent(event) {
  const startRaw = event.start?.dateTime || event.start?.date;
  const endRaw = event.end?.dateTime || event.end?.date;
  const start = startRaw ? new Date(startRaw) : null;
  const now = new Date();

  return {
    time: formatClockTime(startRaw),
    duration: `${meetingDurationMinutes(startRaw, endRaw)} min`,
    title: event.summary || "Meeting",
    attendees: (event.attendees ?? [])
      .map((attendee) => attendee.displayName || attendee.email)
      .filter(Boolean),
    prep: event.description || event.location || "",
    status: start && start < now ? "past" : "upcoming",
    callUrl: event.hangoutLink || event.htmlLink || null,
    calendarEventId: event.id || null,
    sortKey: start ? start.getTime() : 0
  };
}

function mapWhatsAppMeeting(suggestion) {
  const details = suggestion.details ?? {};
  const startRaw = details.start;
  const endRaw = details.end;
  const start = startRaw ? new Date(startRaw) : null;
  const now = new Date();

  return {
    time: formatClockTime(startRaw),
    duration: `${meetingDurationMinutes(startRaw, endRaw)} min`,
    title: details.title || `${suggestion.contact_name || "Client"} — WhatsApp`,
    attendees: [suggestion.contact_name || suggestion.wa_id || "WhatsApp contact", "You"].filter(
      Boolean
    ),
    prep: details.agenda || suggestion.message_body || "Scheduled from WhatsApp",
    status: start && start < now ? "past" : "upcoming",
    callUrl: details.meet_link || details.calendar_html_link || null,
    sortKey: start ? start.getTime() : 0
  };
}

function isTodaySuggestion(suggestion) {
  return isToday(suggestion.details?.start);
}

function bookedDescription(suggestion) {
  const details = suggestion.details ?? {};
  if (details.agenda) {
    return details.agenda;
  }
  if (details.start) {
    return formatMeetingTime(details.start);
  }
  return suggestion.message_body || "Booked from WhatsApp";
}

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

function dedupeMeetings(meetings) {
  const seen = new Set();
  return meetings.filter((meeting) => {
    const key = `${meeting.title}|${meeting.sortKey}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
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
  context.textContent = "Loading schedule…";

  const stats = document.createElement("div");
  stats.className = "stat-row";

  const scheduleSection = document.createElement("section");
  scheduleSection.className = "os-section";
  scheduleSection.appendChild(createSectionHeader("Schedule", "success"));

  const meetingList = document.createElement("div");
  meetingList.className = "meeting-list";
  scheduleSection.appendChild(meetingList);

  const bookedSection = document.createElement("section");
  bookedSection.className = "os-section";
  bookedSection.appendChild(createSectionHeader("Booked from WhatsApp", "info"));

  const bookedList = document.createElement("div");
  bookedList.className = "action-list";
  bookedSection.appendChild(bookedList);

  hero.append(greeting, context, stats);
  page.append(hero, scheduleSection, bookedSection);

  async function reload() {
    let meetings = [];
    let calendarConnected = false;
    let calendarError = null;

    try {
      const status = await getCalendarStatus();
      calendarConnected = Boolean(status.google_calendar?.authorized);
    } catch {
      calendarConnected = false;
    }

    let bookedSuggestions = [];
    try {
      const data = await getScheduledMeetingSuggestions();
      bookedSuggestions = dedupeSuggestions(data.items ?? []).filter(isTodaySuggestion);
    } catch {
      bookedSuggestions = [];
    }

    let todayEventIds = new Set();

    if (calendarConnected) {
      try {
        const events = await getTodayCalendarEvents();
        todayEventIds = new Set((events.items ?? []).map((event) => event.id).filter(Boolean));
        meetings = (events.items ?? []).map(mapCalendarEvent);
      } catch (error) {
        calendarError = error.message;
      }
    }

    if (meetings.length === 0 && bookedSuggestions.length > 0) {
      meetings = bookedSuggestions.map(mapWhatsAppMeeting);
    }

    const bookedOnly = dedupeSuggestions(
      bookedSuggestions.filter((suggestion) => {
        const eventId = suggestion.details?.calendar_event_id;
        return !eventId || !todayEventIds.has(eventId);
      })
    );

    meetings = dedupeMeetings(meetings);
    meetings.sort((a, b) => a.sortKey - b.sortKey);

    const upcoming = meetings.filter((meeting) => meeting.status === "upcoming").length;

    context.textContent = calendarConnected
      ? `${meetings.length} on calendar today`
      : calendarError
        ? `Calendar error — showing WhatsApp bookings`
        : "Connect Google Calendar for live schedule";

    stats.replaceChildren(
      createStatChip(meetings.length, "Today", "success"),
      createStatChip(upcoming, "Upcoming", "info"),
      createStatChip(bookedOnly.length, "From WhatsApp", "warning")
    );

    if (meetings.length === 0) {
      meetingList.replaceChildren(
        createEmptyState(
          calendarConnected
            ? "No meetings on your calendar today."
            : "No meetings yet. Schedule one from the WhatsApp Messages tab."
        )
      );
    } else {
      meetingList.replaceChildren();
      meetings.forEach((meeting) => {
        meetingList.appendChild(createMeetingCard(meeting));
      });
    }

    if (bookedOnly.length === 0) {
      bookedList.replaceChildren(createEmptyState("No WhatsApp meetings booked yet."));
      return;
    }

    bookedList.replaceChildren();
    bookedOnly.forEach((suggestion) => {
      const details = suggestion.details ?? {};
      const link = details.calendar_html_link;
      bookedList.appendChild(
        createActionCard({
          title: details.title || `${suggestion.contact_name || "Contact"} meeting`,
          description: bookedDescription(suggestion),
          accent: "success",
          actions: link
            ? [
                {
                  label: "Open in Calendar",
                  variant: "primary",
                  primary: true,
                  onClick: () => joinCall(link)
                }
              ]
            : []
        })
      );
    });
  }

  reload();
  const timer = window.setInterval(reload, REFRESH_MS);
  page.addEventListener("jarvis:destroy", () => window.clearInterval(timer));

  return page;
}
