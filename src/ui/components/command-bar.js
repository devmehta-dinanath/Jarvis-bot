import { getLastUpdated, onUpdate } from "../../lib/last-updated.js";

function formatStatus(lastUpdatedAt) {
  if (lastUpdatedAt == null) {
    return "Updating…";
  }
  const seconds = Math.max(0, Math.round((Date.now() - lastUpdatedAt) / 1000));
  if (seconds < 2) {
    return "Updated just now";
  }
  return `Updated ${seconds}s ago`;
}

export function createCommandBar() {
  const footer = document.createElement("footer");
  footer.className = "command-bar";

  const row = document.createElement("div");
  row.className = "command-bar__row";

  const status = document.createElement("p");
  status.className = "command-bar__status";
  status.textContent = formatStatus(getLastUpdated());

  const reloadBtn = document.createElement("button");
  reloadBtn.type = "button";
  reloadBtn.className = "command-bar__reload";
  reloadBtn.setAttribute("aria-label", "Reload the app");
  reloadBtn.title = "Reload the app (bypasses cache)";
  reloadBtn.innerHTML =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>';

  reloadBtn.addEventListener("click", () => {
    if (window.jarvisApp?.hardReload) {
      window.jarvisApp.hardReload();
      return;
    }
    window.location.reload();
  });

  row.append(status, reloadBtn);
  footer.appendChild(row);

  const tickTimer = window.setInterval(() => {
    status.textContent = formatStatus(getLastUpdated());
  }, 1000);

  onUpdate(() => {
    status.textContent = formatStatus(getLastUpdated());
  });

  footer.addEventListener("jarvis:destroy", () => window.clearInterval(tickTimer));

  return footer;
}
