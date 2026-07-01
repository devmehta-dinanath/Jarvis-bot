const LOCAL_API = window.jarvisApp?.apiBase ?? "http://127.0.0.1:8000";
const SERVER_API = window.jarvisApp?.serverUrl ?? LOCAL_API;

async function fetchJson(path, options = {}, baseUrl = LOCAL_API) {
  const response = await fetch(`${baseUrl}${path}`, {
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
  return fetchJson("/api/v1/whatsapp/suggestions?kind=meeting&status=pending", {}, SERVER_API);
}

export function getPendingSuggestions() {
  return fetchJson("/api/v1/whatsapp/suggestions?status=pending", {}, SERVER_API);
}

export function getInboxStatus() {
  return fetchJson("/api/v1/whatsapp/inbox/status", {}, SERVER_API);
}

export function getWhatsAppCategories() {
  return fetchJson("/api/v1/whatsapp/categories", {}, SERVER_API);
}

export function refreshPendingInbox() {
  return fetchJson("/api/v1/whatsapp/inbox/refresh-pending", { method: "POST" }, SERVER_API);
}

export function getScheduledMeetingSuggestions() {
  return fetchJson("/api/v1/whatsapp/suggestions?kind=meeting&status=done", {}, SERVER_API);
}

export function scheduleMeetingSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/add-to-calendar`, {
    method: "POST",
    body: JSON.stringify({ conference: true, send_confirmation: true })
  }, SERVER_API);
}

export function dismissSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/dismiss`, {
    method: "POST"
  }, SERVER_API);
}

export function sendSuggestionReply(suggestionId, { text, mode = "auto" } = {}) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/send-reply`, {
    method: "POST",
    body: JSON.stringify({ text, mode })
  }, SERVER_API);
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

  return fetchJson(`/api/v1/calendar/events?${params.toString()}`, {}, SERVER_API);
}

export function getCalendarStatus() {
  return fetchJson("/api/v1/calendar/status", {}, SERVER_API);
}
