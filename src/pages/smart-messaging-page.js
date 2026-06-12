const CONVERSATIONS = [
  { id: "pierre", name: "Pierre Dubois", preview: "Bonjour, pouvez-vous me donner...", unread: true, time: "9:12" },
  { id: "rohan", name: "Rohan Mehta", preview: "Can we push the launch to next week?", unread: true, time: "Yesterday" },
  { id: "acme", name: "Acme Corp", preview: "Payment confirmation for #2841", unread: false, time: "Mon" },
  { id: "dev-team", name: "Dev Team", preview: "Sprint recap is ready for review", unread: false, time: "Mon" }
];

const ACTIVE_THREAD = {
  contact: "Pierre Dubois",
  language: "French",
  original:
    "Bonjour, pouvez-vous me donner une mise à jour sur la dernière commande et m'envoyer la liste de prix actualisée ?",
  summary: "Hi, asking for delivery update on last order and wants the updated price list.",
  draftFr:
    "Bonjour Pierre, votre commande est en transit et arrivera jeudi. Je vous envoie la liste de prix mise à jour dans un instant.",
  draftEn:
    "Hi Pierre, your order is in transit and will arrive Thursday. I'll send the updated price list shortly."
};

function createConversationItem(conversation, active = false) {
  const item = document.createElement("button");
  item.type = "button";
  item.className = `conversation-item${active ? " conversation-item--active" : ""}`;
  item.setAttribute("aria-pressed", String(active));

  const avatar = document.createElement("span");
  avatar.className = "conversation-item__avatar";
  avatar.textContent = conversation.name.charAt(0);

  const body = document.createElement("div");
  body.className = "conversation-item__body";

  const topRow = document.createElement("div");
  topRow.className = "conversation-item__top";

  const name = document.createElement("span");
  name.className = "conversation-item__name";
  name.textContent = conversation.name;

  const time = document.createElement("span");
  time.className = "conversation-item__time";
  time.textContent = conversation.time;

  topRow.append(name, time);

  const preview = document.createElement("p");
  preview.className = "conversation-item__preview";
  preview.textContent = conversation.preview;

  body.append(topRow, preview);

  if (conversation.unread) {
    const dot = document.createElement("span");
    dot.className = "conversation-item__unread";
    dot.setAttribute("aria-label", "Unread");
    item.append(avatar, body, dot);
  } else {
    item.append(avatar, body);
  }

  return item;
}

function createLanguageCard(thread) {
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

  const draftToggle = document.createElement("div");
  draftToggle.className = "language-card__toggle";
  draftToggle.textContent = "FR → EN → FR";

  const draftFr = document.createElement("p");
  draftFr.className = "language-card__draft-text";
  draftFr.textContent = thread.draftFr;

  const draftEn = document.createElement("p");
  draftEn.className = "language-card__draft-translation";
  draftEn.textContent = thread.draftEn;

  draftSection.append(draftToggle, draftFr, draftEn);

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

export function createSmartMessagingPage() {
  const page = document.createElement("section");
  page.className = "os-page os-page--messaging";

  const workspace = document.createElement("div");
  workspace.className = "messaging-workspace";

  const sidebar = document.createElement("aside");
  sidebar.className = "messaging-sidebar";

  const sidebarHeader = document.createElement("div");
  sidebarHeader.className = "messaging-sidebar__header";

  const sidebarTitle = document.createElement("h2");
  sidebarTitle.textContent = "WhatsApp";

  const search = document.createElement("input");
  search.className = "messaging-sidebar__search";
  search.type = "search";
  search.placeholder = "Search conversations";
  search.setAttribute("aria-label", "Search conversations");

  sidebarHeader.append(sidebarTitle, search);

  const list = document.createElement("div");
  list.className = "conversation-list";
  list.setAttribute("role", "list");

  CONVERSATIONS.forEach((conversation, index) => {
    list.appendChild(createConversationItem(conversation, index === 0));
  });

  sidebar.append(sidebarHeader, list);

  const main = document.createElement("div");
  main.className = "messaging-main";

  const mainHeader = document.createElement("div");
  mainHeader.className = "messaging-main__header";

  const mainTitle = document.createElement("h2");
  mainTitle.textContent = ACTIVE_THREAD.contact;

  const status = document.createElement("span");
  status.className = "messaging-main__status";
  status.textContent = "Smart reply ready";

  mainHeader.append(mainTitle, status);
  main.append(mainHeader, createLanguageCard(ACTIVE_THREAD));

  workspace.append(sidebar, main);
  page.appendChild(workspace);

  return page;
}
