export function createHeader({ platform, source }) {
  const header = document.createElement("header");
  header.className = "insights-header";

  const content = document.createElement("div");

  const label = document.createElement("p");
  label.className = "section-label";
  label.textContent = `${source.label} Panel`;

  const title = document.createElement("h1");
  title.textContent = `${source.label} Insights`;

  const copy = document.createElement("p");
  copy.className = "header-copy";
  copy.textContent = source.tagline;

  content.append(label, title, copy);

  const platformChip = document.createElement("div");
  platformChip.className = "platform-chip";
  platformChip.innerHTML = `Platform: <strong>${platform}</strong>`;

  header.append(content, platformChip);

  return header;
}
