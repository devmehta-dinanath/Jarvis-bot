// Shared "last successful data refresh" clock. Pages call markUpdated() when a
// reload() actually lands (auto-poll or manual), the footer subscribes to render
// a live "Updated Ns ago" instead of a static string.

let lastUpdatedAt = null;
const listeners = new Set();

export function markUpdated(timestamp = Date.now()) {
  lastUpdatedAt = timestamp;
  listeners.forEach((listener) => listener(lastUpdatedAt));
}

export function getLastUpdated() {
  return lastUpdatedAt;
}

export function onUpdate(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
