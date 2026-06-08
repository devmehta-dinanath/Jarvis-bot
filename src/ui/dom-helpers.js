export function createCard(className, eyebrow, title, description) {
  const card = document.createElement("div");
  card.className = className;

  const eyebrowEl = document.createElement("p");
  eyebrowEl.className = "insight-card__eyebrow";
  eyebrowEl.textContent = eyebrow;

  const titleEl = document.createElement("h2");
  titleEl.textContent = title;

  const descriptionEl = document.createElement("p");
  descriptionEl.textContent = description;

  card.append(eyebrowEl, titleEl, descriptionEl);

  return card;
}

export function createPlaceholderRows(count, shortLastRow = false) {
  const list = document.createElement("div");
  list.className = "placeholder-list";

  for (let index = 0; index < count; index += 1) {
    const row = document.createElement("div");
    row.className = "placeholder-row";

    if (shortLastRow && index === count - 1) {
      row.classList.add("placeholder-row--short");
    }

    list.appendChild(row);
  }

  return list;
}

export function createInfoCard(title, description) {
  const card = document.createElement("article");
  card.className = "info-card";

  const titleEl = document.createElement("h3");
  titleEl.textContent = title;

  const descriptionEl = document.createElement("p");
  descriptionEl.textContent = description;

  card.append(titleEl, descriptionEl);

  return card;
}
