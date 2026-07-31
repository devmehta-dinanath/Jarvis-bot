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

export function getWhatsAppContacts({ limit = 200, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return fetchJson(`/api/v1/whatsapp/contacts?${params.toString()}`, {}, SERVER_API);
}

export function setContactExcluded(waId, excluded) {
  return fetchJson(`/api/v1/whatsapp/contacts/${encodeURIComponent(waId)}/exclude`, {
    method: "PATCH",
    body: JSON.stringify({ excluded })
  }, SERVER_API);
}

export function getUserInstructions() {
  return fetchJson("/api/v1/whatsapp/instructions", {}, SERVER_API);
}

export function createUserInstruction(text) {
  return fetchJson("/api/v1/whatsapp/instructions", {
    method: "POST",
    body: JSON.stringify({ text })
  }, SERVER_API);
}

export function updateUserInstruction(instructionId, { text, isActive } = {}) {
  const body = {};
  if (text !== undefined) body.text = text;
  if (isActive !== undefined) body.is_active = isActive;
  return fetchJson(`/api/v1/whatsapp/instructions/${encodeURIComponent(instructionId)}`, {
    method: "PATCH",
    body: JSON.stringify(body)
  }, SERVER_API);
}

export function deleteUserInstruction(instructionId) {
  return fetchJson(`/api/v1/whatsapp/instructions/${encodeURIComponent(instructionId)}`, {
    method: "DELETE"
  }, SERVER_API);
}

export function getForwardingRules() {
  return fetchJson("/api/v1/whatsapp/forwarding-rules", {}, SERVER_API);
}

export function createForwardingRule({
  label,
  triggerCategory,
  triggerPaymentStatus,
  teamMemberName,
  teamMemberWaId
}) {
  return fetchJson("/api/v1/whatsapp/forwarding-rules", {
    method: "POST",
    body: JSON.stringify({
      label,
      trigger_category: triggerCategory,
      trigger_payment_status: triggerPaymentStatus ?? null,
      team_member_name: teamMemberName ?? null,
      team_member_wa_id: teamMemberWaId ?? null
    })
  }, SERVER_API);
}

export function updateForwardingRule(ruleId, fields = {}) {
  const body = {};
  if (fields.label !== undefined) body.label = fields.label;
  if (fields.triggerCategory !== undefined) body.trigger_category = fields.triggerCategory;
  if (fields.triggerPaymentStatus !== undefined) body.trigger_payment_status = fields.triggerPaymentStatus;
  if (fields.teamMemberName !== undefined) body.team_member_name = fields.teamMemberName;
  if (fields.teamMemberWaId !== undefined) body.team_member_wa_id = fields.teamMemberWaId;
  if (fields.isActive !== undefined) body.is_active = fields.isActive;
  return fetchJson(`/api/v1/whatsapp/forwarding-rules/${encodeURIComponent(ruleId)}`, {
    method: "PATCH",
    body: JSON.stringify(body)
  }, SERVER_API);
}

export function deleteForwardingRule(ruleId) {
  return fetchJson(`/api/v1/whatsapp/forwarding-rules/${encodeURIComponent(ruleId)}`, {
    method: "DELETE"
  }, SERVER_API);
}

export function forwardSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/forward`, {
    method: "POST"
  }, SERVER_API);
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

export function scheduleMeetingSuggestion(suggestionId, overrides = {}) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/add-to-calendar`, {
    method: "POST",
    body: JSON.stringify({ conference: true, send_confirmation: true, ...overrides })
  }, SERVER_API);
}

export function remindMeSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/remind`, {
    method: "POST",
    body: JSON.stringify({})
  }, SERVER_API);
}

export function dismissSuggestion(suggestionId) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/dismiss`, {
    method: "POST"
  }, SERVER_API);
}

export function dismissAllSuggestions() {
  return fetchJson("/api/v1/whatsapp/suggestions/dismiss-all", {
    method: "POST"
  }, SERVER_API);
}

export function sendSuggestionReply(suggestionId, { text, mode = "auto" } = {}) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/send-reply`, {
    method: "POST",
    body: JSON.stringify({ text, mode })
  }, SERVER_API);
}

export function answerClarification(suggestionId, answer) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/clarify`, {
    method: "POST",
    body: JSON.stringify({ answer })
  }, SERVER_API);
}

export function sendSuggestionFeedback(suggestionId, { feedbackType, correctResponse } = {}) {
  return fetchJson(`/api/v1/whatsapp/suggestions/${suggestionId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ feedback_type: feedbackType, correct_response: correctResponse ?? null })
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

export function getUpcomingCalendarEvents(days = 2) {
  const start = new Date();
  const end = new Date();
  end.setDate(end.getDate() + days);

  const params = new URLSearchParams({
    time_min: start.toISOString(),
    time_max: end.toISOString(),
    max_results: "100"
  });

  return fetchJson(`/api/v1/calendar/events?${params.toString()}`, {}, SERVER_API);
}

export function getCalendarStatus() {
  return fetchJson("/api/v1/calendar/status", {}, SERVER_API);
}

export function getCalendarAuthUrl() {
  return fetchJson("/api/v1/calendar/auth/url", {}, SERVER_API);
}

export function revokeCalendarAuth() {
  return fetchJson("/api/v1/calendar/auth/token", { method: "DELETE" }, SERVER_API);
}

export function getLatestDailySummary() {
  return fetchJson("/api/v1/summaries/latest?period_type=daily", {}, SERVER_API);
}

export function listDailySummaries({ limit = 120, offset = 0 } = {}) {
  const params = new URLSearchParams({
    period_type: "daily",
    limit: String(limit),
    offset: String(offset)
  });
  return fetchJson(`/api/v1/summaries?${params.toString()}`, {}, SERVER_API);
}

export function getDailySummaryForDate(isoDateKey) {
  return fetchJson(`/api/v1/summaries/day/${isoDateKey}?period_type=daily`, {}, SERVER_API);
}

export function getSummaryStats() {
  return fetchJson("/api/v1/summaries/stats", {}, SERVER_API);
}

export function getPendingSummaries() {
  return fetchJson("/api/v1/summaries/pending", {}, SERVER_API);
}

export function generateDailySummaryForDate(isoDateKey) {
  const params = new URLSearchParams({ day: isoDateKey });
  return fetchJson(`/api/v1/summaries/generate?${params.toString()}`, {
    method: "POST"
  }, SERVER_API);
}

export function catchUpPendingSummaries() {
  return fetchJson("/api/v1/summaries/catch-up", { method: "POST" }, SERVER_API);
}
