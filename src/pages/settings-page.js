import {
  createForwardingRule,
  createUserInstruction,
  deleteForwardingRule,
  deleteUserInstruction,
  getCalendarAuthUrl,
  getCalendarStatus,
  getForwardingRules,
  getUserInstructions,
  getWhatsAppContacts,
  revokeCalendarAuth,
  setContactExcluded,
  updateForwardingRule,
  updateUserInstruction
} from "../lib/api.js";
import { applyAvatarGradient } from "../lib/avatar-color.js";
import { CATEGORY_LABELS, WORK_CATEGORIES } from "../lib/whatsapp-categories.js";

function createAccordionSection(title, accent, { defaultOpen = true, hint } = {}) {
  const details = document.createElement("details");
  details.className = "os-accordion";
  details.open = defaultOpen;

  const summary = document.createElement("summary");
  summary.className = "os-accordion__summary";

  const bar = document.createElement("span");
  bar.className = `section-header__accent section-header__accent--${accent}`;
  bar.setAttribute("aria-hidden", "true");

  const label = document.createElement("h3");
  label.className = "section-header__title os-accordion__title";
  label.textContent = title;

  const count = document.createElement("span");
  count.className = "section-header__count os-accordion__count";
  count.hidden = true;

  const chevron = document.createElement("span");
  chevron.className = "os-accordion__chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "⌄";

  summary.append(bar, label, count, chevron);
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "os-accordion__body";
  details.appendChild(body);

  if (hint) {
    const hintEl = document.createElement("p");
    hintEl.className = "os-section__hint";
    hintEl.textContent = hint;
    body.appendChild(hintEl);
  }

  function setCount(n) {
    if (n === null || n === undefined) {
      count.hidden = true;
      return;
    }
    count.hidden = false;
    count.textContent = String(n);
  }

  return { details, body, setCount };
}

function openExternal(url) {
  if (window.jarvisApp?.openExternal) {
    window.jarvisApp.openExternal(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

function createCalendarSection() {
  const { details, body } = createAccordionSection("Google Calendar", "info", {
    defaultOpen: true,
    hint: "Connect Google Calendar so meetings scheduled from WhatsApp are created automatically."
  });

  const statusLine = document.createElement("p");
  statusLine.className = "os-empty-state";
  statusLine.textContent = "Checking connection…";
  body.appendChild(statusLine);

  const connectBtn = document.createElement("button");
  connectBtn.type = "button";
  connectBtn.className = "btn btn--primary";
  connectBtn.textContent = "Connect Google Calendar";
  connectBtn.hidden = true;

  const disconnectBtn = document.createElement("button");
  disconnectBtn.type = "button";
  disconnectBtn.className = "btn btn--ghost btn--warning";
  disconnectBtn.textContent = "Disconnect";
  disconnectBtn.hidden = true;

  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "btn btn--ghost";
  refreshBtn.textContent = "Refresh status";
  refreshBtn.hidden = true;

  const actions = document.createElement("div");
  actions.className = "instruction-row__actions";
  actions.append(connectBtn, refreshBtn, disconnectBtn);
  body.appendChild(actions);

  async function reload() {
    let status;
    try {
      const data = await getCalendarStatus();
      status = data.google_calendar;
    } catch (error) {
      statusLine.textContent = `Could not check calendar status — ${error.message}`;
      connectBtn.hidden = true;
      disconnectBtn.hidden = true;
      refreshBtn.hidden = true;
      return;
    }

    if (!status.credentials_file_exists) {
      statusLine.textContent =
        "Google OAuth credentials are not set up on the server yet.";
      connectBtn.hidden = true;
      disconnectBtn.hidden = true;
      refreshBtn.hidden = true;
      return;
    }

    if (status.authorized) {
      statusLine.textContent = `Connected — events go to "${status.calendar_id}".`;
      connectBtn.hidden = true;
      disconnectBtn.hidden = false;
      refreshBtn.hidden = true;
      return;
    }

    statusLine.textContent = "Not connected yet.";
    connectBtn.hidden = false;
    disconnectBtn.hidden = true;
    refreshBtn.hidden = false;
  }

  connectBtn.addEventListener("click", async () => {
    connectBtn.disabled = true;
    try {
      const { authorization_url: authUrl } = await getCalendarAuthUrl();
      openExternal(authUrl);
      statusLine.textContent =
        "Complete sign-in in your browser, then click \"Refresh status\" below.";
      refreshBtn.hidden = false;
    } catch (error) {
      statusLine.textContent = `Could not start connection — ${error.message}`;
    } finally {
      connectBtn.disabled = false;
    }
  });

  refreshBtn.addEventListener("click", reload);

  disconnectBtn.addEventListener("click", async () => {
    disconnectBtn.disabled = true;
    try {
      await revokeCalendarAuth();
      await reload();
    } catch (error) {
      statusLine.textContent = `Could not disconnect — ${error.message}`;
    } finally {
      disconnectBtn.disabled = false;
    }
  });

  reload();

  return details;
}

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
  applyAvatarGradient(avatar, contactLabel(contact));

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
  const { details, body, setCount } = createAccordionSection("Your instructions", "urgent", {
    defaultOpen: true,
    hint:
      "Tell Personal OS how to behave in plain language — it's saved and followed every session until you edit or delete it."
  });

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
  body.appendChild(form);

  const formError = document.createElement("p");
  formError.className = "instruction-form__error";
  formError.hidden = true;
  body.appendChild(formError);

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
  body.appendChild(list);

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
      setCount(null);
      return;
    }

    setCount(instructions.length);
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

  return details;
}

function createConversationsSection() {
  const { details, body, setCount } = createAccordionSection(
    "All conversations & groups",
    "info",
    {
      defaultOpen: false,
      hint: "Turn a conversation or group off and it stops being analyzed — no chips, no summaries."
    }
  );

  const searchWrap = document.createElement("div");
  searchWrap.className = "settings-search";

  const searchInput = document.createElement("input");
  searchInput.type = "search";
  searchInput.className = "settings-search__input";
  searchInput.placeholder = "Search conversations & groups…";
  searchWrap.appendChild(searchInput);
  body.appendChild(searchWrap);

  const list = document.createElement("div");
  list.className = "settings-list";
  body.appendChild(list);

  const emptyState = document.createElement("p");
  emptyState.className = "os-empty-state";
  emptyState.textContent = "Loading…";
  list.appendChild(emptyState);

  let allContacts = [];

  function render() {
    const query = searchInput.value.trim().toLowerCase();
    const filtered = query
      ? allContacts.filter((contact) =>
          contactLabel(contact).toLowerCase().includes(query) ||
          (contact.wa_id || "").toLowerCase().includes(query)
        )
      : allContacts;

    list.replaceChildren();

    if (allContacts.length === 0) {
      const empty = document.createElement("p");
      empty.className = "os-empty-state";
      empty.textContent = "No conversations yet.";
      list.appendChild(empty);
      return;
    }

    if (filtered.length === 0) {
      const empty = document.createElement("p");
      empty.className = "os-empty-state";
      empty.textContent = `No matches for "${searchInput.value.trim()}".`;
      list.appendChild(empty);
      return;
    }

    filtered.forEach((contact) => {
      list.appendChild(
        createContactRow(contact, {
          onToggle: async (item, excluded) => {
            await setContactExcluded(item.wa_id, excluded);
          }
        })
      );
    });
  }

  searchInput.addEventListener("input", render);

  async function reload() {
    try {
      const data = await getWhatsAppContacts({ limit: 200 });
      allContacts = data.items ?? [];
    } catch (error) {
      list.replaceChildren();
      const errorState = document.createElement("p");
      errorState.className = "os-empty-state";
      errorState.textContent = `Could not load conversations — ${error.message}`;
      list.appendChild(errorState);
      setCount(null);
      return;
    }

    setCount(allContacts.length);
    render();
  }

  reload();

  return details;
}

const FORWARDING_TRIGGER_OPTIONS = WORK_CATEGORIES.filter((c) => c !== "other");

function triggerDescription(rule) {
  const base = CATEGORY_LABELS[rule.trigger_category] || rule.trigger_category;
  if (rule.trigger_category === "payment" && rule.trigger_payment_status) {
    return rule.trigger_payment_status === "received" ? "Payment received" : "Payment overdue";
  }
  return base;
}

function buildTriggerSelect(selectedCategory) {
  const select = document.createElement("select");
  select.className = "instruction-form__input forwarding-form__select";
  FORWARDING_TRIGGER_OPTIONS.forEach((cat) => {
    const option = document.createElement("option");
    option.value = cat;
    option.textContent = CATEGORY_LABELS[cat] || cat;
    if (cat === selectedCategory) {
      option.selected = true;
    }
    select.appendChild(option);
  });
  return select;
}

function buildPaymentStatusSelect(selected) {
  const select = document.createElement("select");
  select.className = "instruction-form__input forwarding-form__select";
  [
    { value: "", label: "Any payment message" },
    { value: "received", label: "Payment received" },
    { value: "overdue", label: "Payment overdue" }
  ].forEach(({ value, label }) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    if (value === (selected || "")) {
      option.selected = true;
    }
    select.appendChild(option);
  });
  return select;
}

function createForwardingRuleForm({ initial, submitLabel, onSubmit, onCancel }) {
  const form = document.createElement("form");
  form.className = "forwarding-form";

  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.className = "instruction-form__input";
  labelInput.placeholder = "Department, e.g. Accounts";
  labelInput.value = initial?.label ?? "";
  labelInput.required = true;

  const triggerSelect = buildTriggerSelect(initial?.trigger_category ?? "payment");
  const paymentStatusSelect = buildPaymentStatusSelect(initial?.trigger_payment_status ?? "");
  paymentStatusSelect.hidden = triggerSelect.value !== "payment";

  triggerSelect.addEventListener("change", () => {
    paymentStatusSelect.hidden = triggerSelect.value !== "payment";
  });

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.className = "instruction-form__input";
  nameInput.placeholder = "Team member name";
  nameInput.value = initial?.team_member_name ?? "";

  const waIdInput = document.createElement("input");
  waIdInput.type = "text";
  waIdInput.className = "instruction-form__input";
  waIdInput.placeholder = "WhatsApp number, e.g. 919876543210";
  waIdInput.value = initial?.team_member_wa_id ?? "";

  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn btn--primary instruction-row__action";
  submitBtn.textContent = submitLabel;

  const buttonRow = document.createElement("div");
  buttonRow.className = "instruction-row__actions";
  buttonRow.appendChild(submitBtn);

  if (onCancel) {
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn--ghost instruction-row__action";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", onCancel);
    buttonRow.appendChild(cancelBtn);
  }

  form.append(labelInput, triggerSelect, paymentStatusSelect, nameInput, waIdInput, buttonRow);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const label = labelInput.value.trim();
    if (!label) return;
    submitBtn.disabled = true;
    try {
      await onSubmit({
        label,
        triggerCategory: triggerSelect.value,
        triggerPaymentStatus: triggerSelect.value === "payment" ? paymentStatusSelect.value || null : null,
        teamMemberName: nameInput.value.trim() || null,
        teamMemberWaId: waIdInput.value.trim() || null
      });
    } finally {
      submitBtn.disabled = false;
    }
  });

  return form;
}

function createForwardingRuleRow(rule, { onSave, onDelete }) {
  const row = document.createElement("div");
  row.className = "instruction-row forwarding-row";

  const info = document.createElement("div");
  info.className = "forwarding-row__info";

  const labelLine = document.createElement("p");
  labelLine.className = "settings-row__name";
  labelLine.textContent = rule.label;

  const badge = document.createElement("span");
  badge.className = "settings-row__badge";
  badge.textContent = triggerDescription(rule);
  labelLine.appendChild(badge);

  const memberLine = document.createElement("p");
  memberLine.className = "settings-row__hint";
  memberLine.textContent = rule.team_member_wa_id
    ? `→ ${rule.team_member_name || "Unnamed"} · ${rule.team_member_wa_id}`
    : "Not assigned yet — tap Edit to add a team member.";

  info.append(labelLine, memberLine);

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
  row.append(info, actions);

  editBtn.addEventListener("click", () => {
    const form = createForwardingRuleForm({
      initial: rule,
      submitLabel: "Save",
      onCancel: () => row.replaceChildren(info, actions),
      onSubmit: async (fields) => {
        await onSave(rule, fields);
      }
    });
    row.replaceChildren(form);
  });

  deleteBtn.addEventListener("click", async () => {
    deleteBtn.disabled = true;
    try {
      await onDelete(rule);
    } finally {
      deleteBtn.disabled = false;
    }
  });

  return row;
}

function createForwardingSection() {
  const { details, body, setCount } = createAccordionSection("Team forwarding", "success", {
    defaultOpen: false,
    hint:
      "Assign a team member to a category and a one-tap Forward button appears on matching messages — no copy/paste, no switching apps."
  });

  const formWrap = document.createElement("div");
  formWrap.className = "forwarding-add";
  body.appendChild(formWrap);

  const formError = document.createElement("p");
  formError.className = "instruction-form__error";
  formError.hidden = true;
  body.appendChild(formError);

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
  body.appendChild(list);

  const emptyState = document.createElement("p");
  emptyState.className = "os-empty-state";
  emptyState.textContent = "Loading…";
  list.appendChild(emptyState);

  function renderAddForm() {
    const form = createForwardingRuleForm({
      submitLabel: "Add rule",
      onSubmit: async (fields) => {
        try {
          await createForwardingRule(fields);
          clearFormError();
          await reload();
          renderAddForm();
        } catch (error) {
          showFormError(`Could not add rule — ${error.message}`);
        }
      }
    });
    formWrap.replaceChildren(form);
  }

  async function reload() {
    let rules = [];
    try {
      const data = await getForwardingRules();
      rules = data.items ?? [];
    } catch (error) {
      list.replaceChildren();
      const errorState = document.createElement("p");
      errorState.className = "os-empty-state";
      errorState.textContent = `Could not load forwarding rules — ${error.message}`;
      list.appendChild(errorState);
      setCount(null);
      return;
    }

    setCount(rules.length);
    list.replaceChildren();
    if (rules.length === 0) {
      const empty = document.createElement("p");
      empty.className = "os-empty-state";
      empty.textContent = "No forwarding rules yet — add one above.";
      list.appendChild(empty);
      return;
    }

    rules.forEach((rule) => {
      list.appendChild(
        createForwardingRuleRow(rule, {
          onSave: async (item, fields) => {
            try {
              await updateForwardingRule(item.id, fields);
              clearFormError();
              await reload();
            } catch (error) {
              showFormError(`Could not save rule — ${error.message}`);
            }
          },
          onDelete: async (item) => {
            try {
              await deleteForwardingRule(item.id);
              clearFormError();
              await reload();
            } catch (error) {
              showFormError(`Could not delete rule — ${error.message}`);
            }
          }
        })
      );
    });
  }

  renderAddForm();
  reload();

  return details;
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
    "Choose exactly what Personal OS reads and how it behaves — grouped below so it's easy to scan.";

  hero.append(heroTitle, heroContext);

  const instructionsSection = createInstructionsSection();
  const calendarSection = createCalendarSection();
  const forwardingSection = createForwardingSection();
  const conversationsSection = createConversationsSection();

  page.append(hero, instructionsSection, calendarSection, forwardingSection, conversationsSection);

  return page;
}
