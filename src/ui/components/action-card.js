export function createActionCard({ title, description, accent = "urgent", actions = [] }) {
  const card = document.createElement("article");
  card.className = `action-card action-card--${accent}`;

  const body = document.createElement("div");
  body.className = "action-card__body";

  const titleEl = document.createElement("h4");
  titleEl.className = "action-card__title";
  titleEl.textContent = title;

  body.appendChild(titleEl);

  if (description) {
    const descEl = document.createElement("p");
    descEl.className = "action-card__desc";
    descEl.textContent = description;
    body.appendChild(descEl);
  }

  card.appendChild(body);

  if (actions.length > 0) {
    const actionRow = document.createElement("div");
    actionRow.className = "action-card__actions";

    actions.forEach(({ label, variant = "ghost", primary = false, onClick, disabled = false }) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `btn btn--${variant}${primary ? " btn--primary" : ""}`;
      button.textContent = label;
      button.disabled = disabled;
      if (typeof onClick === "function") {
        button.addEventListener("click", onClick);
      }
      actionRow.appendChild(button);
    });

    card.appendChild(actionRow);
  }

  return card;
}
