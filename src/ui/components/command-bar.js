export function createCommandBar() {
  const footer = document.createElement("footer");
  footer.className = "command-bar";

  const form = document.createElement("form");
  form.className = "command-bar__form";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
  });

  const input = document.createElement("input");
  input.className = "command-bar__input";
  input.type = "text";
  input.placeholder = "Correct anything or give a command...";
  input.setAttribute("aria-label", "Command input");

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "command-bar__submit";
  submit.setAttribute("aria-label", "Submit command");
  submit.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>`;

  form.append(input, submit);

  const hints = document.createElement("p");
  hints.className = "command-bar__hints";
  hints.textContent = '"Pierre speaks Spanish" · "Move Rohan to tomorrow" · "Show payments"';

  const status = document.createElement("p");
  status.className = "command-bar__status";
  status.textContent = "Updated 1 min ago";

  footer.append(form, hints, status);
  return footer;
}
