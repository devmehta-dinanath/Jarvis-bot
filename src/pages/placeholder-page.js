import { createSectionHeader } from "../ui/components/section-header.js";

export function createPlaceholderPage(title, description) {
  const page = document.createElement("section");
  page.className = "os-page os-page--placeholder";

  const section = document.createElement("section");
  section.className = "os-section";
  section.appendChild(createSectionHeader(title, "info"));

  const card = document.createElement("article");
  card.className = "content-card";

  const copy = document.createElement("p");
  copy.className = "content-card__desc";
  copy.textContent = description;

  card.appendChild(copy);
  section.appendChild(card);
  page.appendChild(section);

  return page;
}
