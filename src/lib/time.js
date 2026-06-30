export const DISPLAY_TIMEZONE = "Asia/Kolkata";

const LOCALE = "en-IN";

function toDate(value) {
  if (value instanceof Date) {
    return value;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value, options = {}) {
  const date = toDate(value);
  if (!date) {
    return String(value ?? "");
  }

  return date.toLocaleString(LOCALE, {
    timeZone: DISPLAY_TIMEZONE,
    ...options
  });
}

export function getGreeting(name = "there") {
  const hour = new Date().getHours();

  if (hour < 12) {
    return `Good morning, ${name}.`;
  }

  if (hour < 17) {
    return `Good afternoon, ${name}.`;
  }

  return `Good evening, ${name}.`;
}

export function startGreetingClock(element, name = "there") {
  function refresh() {
    element.textContent = getGreeting(name);

    const now = new Date();
    const next = new Date(now);
    const hour = now.getHours();

    if (hour < 12) {
      next.setHours(12, 0, 0, 0);
    } else if (hour < 17) {
      next.setHours(17, 0, 0, 0);
    } else {
      next.setDate(next.getDate() + 1);
      next.setHours(0, 0, 0, 0);
    }

    setTimeout(refresh, Math.max(next.getTime() - now.getTime(), 1000));
  }

  refresh();
}

export function getPowerWindow() {
  const hour = new Date().getHours();

  if (hour >= 9 && hour < 11) {
    return { label: "Your power window starts now — 9am to 11am." };
  }

  if (hour >= 14 && hour < 16) {
    return { label: "Afternoon focus block — 2pm to 4pm." };
  }

  const nextWindow = hour < 9 ? "9am" : hour < 14 ? "2pm" : "tomorrow at 9am";

  return { label: `Next power window at ${nextWindow}.` };
}

export function formatHourLabel(hour) {
  const suffix = hour >= 12 ? "pm" : "am";
  const normalized = hour % 12 === 0 ? 12 : hour % 12;
  return `${normalized}${suffix}`;
}

export function isToday(iso) {
  if (!iso) {
    return false;
  }

  const date = toDate(iso);
  if (!date) {
    return false;
  }

  const dayKey = (value) =>
    value.toLocaleDateString(LOCALE, {
      timeZone: DISPLAY_TIMEZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    });

  return dayKey(date) === dayKey(new Date());
}

export function formatMeetingTime(iso) {
  if (!iso) {
    return "Time not set";
  }

  const date = toDate(iso);
  if (!date) {
    return iso;
  }

  return formatDateTime(date, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true
  });
}

export function formatClockTime(iso) {
  if (!iso) {
    return "--:--";
  }

  const date = toDate(iso);
  if (!date) {
    return "--:--";
  }

  return date.toLocaleTimeString(LOCALE, {
    timeZone: DISPLAY_TIMEZONE,
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  });
}

export function meetingDurationMinutes(startIso, endIso) {
  if (!startIso || !endIso) {
    return 60;
  }

  const start = new Date(startIso);
  const end = new Date(endIso);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return 60;
  }

  return Math.max(15, Math.round((end.getTime() - start.getTime()) / 60000));
}
