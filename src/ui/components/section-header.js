export function createSectionHeader(title, accent = "accent", count) {
  const header = document.createElement("div");
  header.className = "section-header";

  const bar = document.createElement("span");
  bar.className = `section-header__accent section-header__accent--${accent}`;
  bar.setAttribute("aria-hidden", "true");

  const label = document.createElement("h3");
  label.className = "section-header__title";
  label.textContent = title;

  header.append(bar, label);

  if (count !== undefined) {
    const badge = document.createElement("span");
    badge.className = "section-header__count";
    badge.textContent = String(count);
    header.appendChild(badge);
  }

  return header;
}
