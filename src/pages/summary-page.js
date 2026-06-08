import { createCard, createInfoCard, createPlaceholderRows } from "../ui/dom-helpers.js";

export function createSummaryPage(source) {
  const page = document.createElement("section");
  page.className = "tab-panel page page--summary";

  const heroCard = createCard(
    "insight-card",
    "Summary tab",
    "No data yet",
    "Use this area for summaries, highlights, and key takeaways after the APIs are connected."
  );

  const placeholderCard = createInfoCard(
    "Summary Placeholder",
    "This panel is intentionally empty and structured for future summary content."
  );
  placeholderCard.appendChild(createPlaceholderRows(4, true));

  const questionCard = document.createElement("form");
  questionCard.className = "question-card";
  questionCard.action = "#";

  const questionTitle = document.createElement("h3");
  questionTitle.textContent = "Ask a Question";

  const questionCopy = document.createElement("p");
  questionCopy.textContent =
    "Ask about the summary, pending actions, important context, or next steps.";

  const inputRow = document.createElement("div");
  inputRow.className = "question-card__row";

  const input = document.createElement("input");
  input.className = "question-card__input";
  input.type = "text";
  input.name = "summary-question";
  input.placeholder = "Ask about this summary...";
  input.setAttribute("aria-label", "Ask a summary question");

  const button = document.createElement("button");
  button.className = "question-card__button";
  button.type = "submit";
  button.textContent = "Ask";

  inputRow.append(input, button);
  questionCard.append(questionTitle, questionCopy, inputRow);

  page.append(heroCard, questionCard, placeholderCard);

  return page;
}
