import {
  createUserInstruction,
  deleteUserInstruction,
  getUserInstructions,
  getWhatsAppContacts,
  setContactExcluded,
  updateUserInstruction
} from "../lib/api.js";
import { createSectionHeader } from "../ui/components/section-header.js";

function contactLabel(contact) {
  return contact.profile_name || contact.wa_id || "Unknown";
}

function createToggle(checked, onChange) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "toggle-switch";
  button.setAttribute("role", "switch");
  button.setAttribute("aria-checked", String(checked));
  button.classList.toggle("toggle-switch--on", checked);

  const knob = document.createElement("span");
  knob.className = "toggle-switch__knob";
  button.appendChild(knob);

  button.addEventListener("click", async () => {
    const next = !button.classList.contains("toggle-switch--on");
    button.disabled = true;
    try {
      await onChange(next);
      button.classList.toggle("toggle-switch--on", next);
      button.setAttribute("aria-checked", String(next));
    } finally {
      button.disabled = false;
    }
  });

  return button;
}

function createContactRow(contact, { onToggle }) {
  const row = document.createElement("div");
  row.className = "settings-row";

  const avatar = document.createElement("span");
  avatar.className = "conversation-item__avatar";
  avatar.textContent = contactLabel(contact).charAt(0).toUpperCase();

  const body = document.createElement("div");
  body.className = "settings-row__body";

  const nameRow = document.createElement("div");
  nameRow.className = "settings-row__top";

  const name = document.createElement("span");
  name.className = "settings-row__name";
  name.textContent = contactLabel(contact);

  const badge = document.createElement("span");
  badge.className = "settings-row__badge";
  badge.textContent = contact.is_group ? "Group" : "Contact";

  nameRow.append(name, badge);

  const hint = document.createElement("p");
  hint.className = "settings-row__hint";
  hint.textContent = contact.wa_id;

  body.append(nameRow, hint);

  const toggleWrap = document.createElement("div");
  toggleWrap.className = "settings-row__toggle";

  const toggleLabel = document.createElement("span");
  toggleLabel.className = "settings-row__toggle-label";
  toggleLabel.textContent = "Reading";

  const toggle = createToggle(!contact.is_excluded, (nextEnabled) =>
    onToggle(contact, !nextEnabled)
  );

  toggleWrap.append(toggleLabel, toggle);

  row.append(avatar, body, toggleWrap);
  return row;
}

function createInstructionRow(instruction, { onSave, onDelete }) {
  const row = document.createElement("div");
  row.className = "instruction-row";

  const text = document.createElement("p");
  text.className = "instruction-row__text";
  text.textContent = instruction.text;

  const actions = document.createElement("div");
  actions.className = "instruction-row__actions";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn btn--ghost instruction-row__action";
  editBtn.textContent = "Edit";

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "btn btn--warning instruction-row__action";
  deleteBtn.textContent = "Delete";

  actions.append(editBtn, deleteBtn);
  row.append(text, actions);

  editBtn.addEventListener("click", () => {
    const editor = document.createElement("textarea");
    editor.className = "instruction-form__input";
    editor.value = instruction.text;

    const editActions = document.createElement("div");
    editActions.className = "instruction-row__actions";

    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "btn btn--primary instruction-row__action";
    saveBtn.textContent = "Save";

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn--ghost instruction-row__action";
    cancelBtn.textContent = "Cancel";

    editActions.append(saveBtn, cancelBtn);
    row.replaceChildren(editor, editActions);
    editor.focus();

    cancelBtn.addEventListener("click", () => {
      row.replaceChildren(text, actions);
    });

    saveBtn.addEventListener("click", async () => {
      const nextText = editor.value.trim();
      if (!nextText) return;
      saveBtn.disabled = true;
      try {
        await onSave(instruction, nextText);
      } finally {
        saveBtn.disabled = false;
      }
    });
  });

  deleteBtn.addEventListener("click", async () => {
    deleteBtn.disabled = true;
    try {
      await onDelete(instruction);
    } finally {
      deleteBtn.disabled = false;
    }
  });

  return row;
}

function createInstructionsSection() {
  const section = document.createElement("section");
  section.className = "os-section os-section--instructions";
  section.append(createSectionHeader("Your instructions", "urgent"));

  const hint = document.createElement("p");
  hint.className = "os-section__hint";
  hint.textContent =
    "Tell Personal OS how to behave in plain language — it's saved and followed every session until you edit or delete it.";
  section.appendChild(hint);

  const form = document.createElement("form");
  form.className = "instruction-form";

  const input = document.createElement("textarea");
  input.className = "instruction-form__input";
  input.placeholder =
    "e.g. Do not read any group messages. Always reply formally to clients. Ignore the Family Group.";
  input.rows = 2;

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "btn btn--primary instruction-form__submit";
  submit.textContent = "Add instruction";

  form.append(input, submit);
  section.appendChild(form);

  const formError = document.createElement("p");
  formError.className = "instruction-form__error";
  formError.hidden = true;
  section.appendChild(formError);

  function showFormError(message) {
    formError.textContent = message;
    formError.hidden = false;
  }

  function clearFormError() {
    formError.hidden = true;
    formError.textContent = "";
  }

  const list = document.createElement("div");
  list.className = "instruction-list";
  section.appendChild(list);

  const emptyState = document.createElement("p");
  emptyState.className = "os-empty-state";
  emptyState.textContent = "Loading…";
  list.appendChild(emptyState);

  async function reload() {
    let instructions = [];
    try {
      const data = await getUserInstructions();
      instructions = data.items ?? [];
    } catch (error) {
      list.replaceChildren();
      const errorState = document.createElement("p");
      errorState.className = "os-empty-state";
      errorState.textContent = `Could not load instructions — ${error.message}`;
      list.appendChild(errorState);
      return;
    }

    list.replaceChildren();
    if (instructions.length === 0) {
      const empty = document.createElement("p");
      empty.className = "os-empty-state";
      empty.textContent = "No instructions yet — add one above.";
      list.appendChild(empty);
      return;
    }

    instructions.forEach((instruction) => {
      list.appendChild(
        createInstructionRow(instruction, {
          onSave: async (item, nextText) => {
            try {
              await updateUserInstruction(item.id, { text: nextText });
              clearFormError();
              await reload();
            } catch (error) {
              showFormError(`Could not save instruction — ${error.message}`);
            }
          },
          onDelete: async (item) => {
            try {
              await deleteUserInstruction(item.id);
              clearFormError();
              await reload();
            } catch (error) {
              showFormError(`Could not delete instruction — ${error.message}`);
            }
          }
        })
      );
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    submit.disabled = true;
    try {
      await createUserInstruction(text);
      input.value = "";
      clearFormError();
      await reload();
    } catch (error) {
      showFormError(`Could not add instruction — ${error.message}`);
    } finally {
      submit.disabled = false;
    }
  });

  reload();

  return section;
}

export function createSettingsPage() {
  const page = document.createElement("section");
  page.className = "os-page os-page--settings";

  const hero = document.createElement("article");
  hero.className = "hero-card";

  const heroTitle = document.createElement("h2");
  heroTitle.className = "hero-card__greeting";
  heroTitle.textContent = "Conversation Settings";

  const heroContext = document.createElement("p");
  heroContext.className = "hero-card__context";
  heroContext.textContent =
    "Choose exactly what Personal OS reads. Turn a conversation or group off and it stops being analyzed — no chips, no summaries.";

  hero.append(heroTitle, heroContext);

  const instructionsSection = createInstructionsSection();

  const section = document.createElement("section");
  section.className = "os-section";
  section.append(createSectionHeader("All conversations & groups", "info"));

  const list = document.createElement("div");
  list.className = "settings-list";
  section.appendChild(list);

  const emptyState = document.createElement("p");
  emptyState.className = "os-empty-state";
  emptyState.textContent = "Loading…";
  list.appendChild(emptyState);

  page.append(hero, instructionsSection, section);

  async function reload() {
    let contacts = [];
    try {
      const data = await getWhatsAppContacts({ limit: 200 });
      contacts = data.items ?? [];
    } catch (error) {
      list.replaceChildren();
      const errorState = document.createElement("p");
      errorState.className = "os-empty-state";
      errorState.textContent = `Could not load conversations — ${error.message}`;
      list.appendChild(errorState);
      return;
    }

    list.replaceChildren();
    if (contacts.length === 0) {
      const empty = document.createElement("p");
      empty.className = "os-empty-state";
      empty.textContent = "No conversations yet.";
      list.appendChild(empty);
      return;
    }

    contacts.forEach((contact) => {
      list.appendChild(
        createContactRow(contact, {
          onToggle: async (item, excluded) => {
            await setContactExcluded(item.wa_id, excluded);
          }
        })
      );
    });
  }

  reload();

  return page;
}
