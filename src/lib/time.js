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
