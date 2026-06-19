export function createLanguageCard(thread) {
  const card = document.createElement("article");
  card.className = "language-card";

  const header = document.createElement("div");
  header.className = "language-card__header";

  const contact = document.createElement("h3");
  contact.className = "language-card__contact";
  contact.textContent = thread.contact;

  const badge = document.createElement("span");
  badge.className = "language-card__badge";
  badge.textContent = `${thread.language} detected`;

  header.append(contact, badge);

  const original = document.createElement("blockquote");
  original.className = "language-card__original";
  original.textContent = thread.original;

  const summarySection = document.createElement("div");
  summarySection.className = "language-card__summary";

  const summaryLabel = document.createElement("p");
  summaryLabel.className = "language-card__label";
  summaryLabel.textContent = "What he said";

  const summaryText = document.createElement("p");
  summaryText.className = "language-card__summary-text";
  summaryText.textContent = thread.summary;

  summarySection.append(summaryLabel, summaryText);

  const draftSection = document.createElement("div");
  draftSection.className = "language-card__draft";

  const draftLabel = document.createElement("p");
  draftLabel.className = "language-card__label";
  draftLabel.textContent = "Draft reply";

  const draftText = document.createElement("p");
  draftText.className = "language-card__draft-text";
  draftText.textContent = thread.draftFr;

  draftSection.append(draftLabel, draftText);

  const actions = document.createElement("div");
  actions.className = "language-card__actions";

  const sendBtn = document.createElement("button");
  sendBtn.type = "button";
  sendBtn.className = "btn btn--primary btn--large";
  sendBtn.textContent = "Yes — send in French";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn btn--ghost";
  editBtn.textContent = "Edit";

  const wrongBtn = document.createElement("button");
  wrongBtn.type = "button";
  wrongBtn.className = "btn btn--ghost";
  wrongBtn.textContent = "Wrong?";

  actions.append(sendBtn, editBtn, wrongBtn);

  card.append(header, original, summarySection, draftSection, actions);
  return card;
}
