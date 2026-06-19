const API_BASE = window.jarvisApp?.apiBase ?? "http://127.0.0.1:8000";

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(options.headers ?? {})
    },
    ...options
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export function getPendingMeetingSuggestions() {
  return fetchJson("/api/v1/whatsapp/suggestions?kind=meeting&status=pending");
}

export function getScheduledMeetingSuggestions() {
  return fetchJson("/api/v1/whatsapp/suggestions?kind=meeting&status=done");
}

export function scheduleMeetingSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/add-to-calendar`, {
    method: "POST",
    body: JSON.stringify({ conference: true })
  });
}

export function dismissSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/dismiss`, {
    method: "POST"
  });
}

export function sendSuggestionReply(suggestionId, { text, mode = "auto" } = {}) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/send-reply`, {
    method: "POST",
    body: JSON.stringify({ text, mode })
  });
}

export function getTodayCalendarEvents() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date();
  end.setHours(23, 59, 59, 999);

  const params = new URLSearchParams({
    time_min: start.toISOString(),
    time_max: end.toISOString(),
    max_results: "50"
  });

  return fetchJson(`/api/v1/calendar/events?${params.toString()}`);
}

export function getCalendarStatus() {
  return fetchJson("/api/v1/calendar/status");
}
