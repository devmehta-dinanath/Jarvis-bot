export function createFocusBanner({ message, onFocus }) {
  const banner = document.createElement("div");
  banner.className = "focus-banner";

  const icon = document.createElement("span");
  icon.className = "focus-banner__icon";
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;

  const text = document.createElement("p");
  text.className = "focus-banner__text";
  text.textContent = message;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "focus-banner__action";
  button.textContent = "Focus →";

  if (onFocus) {
    button.addEventListener("click", onFocus);
  }

  banner.append(icon, text, button);
  return banner;
}
