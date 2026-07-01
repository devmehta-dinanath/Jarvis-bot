import {
  catchUpPendingSummaries,
  generateDailySummaryForDate,
  getDailySummaryForDate,
  getPendingSummaries,
  getSummaryStats,
  listDailySummaries
} from "../lib/api.js";
import {
  formatDateTime,
  formatRelativeDayLabel,
  formatShortDate,
  getGreeting,
  toISODateKey
} from "../lib/time.js";
import { createSectionHeader } from "../ui/components/section-header.js";
import { createStatChip } from "../ui/components/stat-chip.js";

const REFRESH_MS = 60000;
const HASH_PREFIX = "summary/";

function formatDayLabel(periodStart) {
  if (!periodStart) {
    return "Select a day";
  }
  return formatDateTime(periodStart, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric"
  });
}

function nextDayLabel(isoDateKey) {
  if (!isoDateKey) {
    return "the next day";
  }
  const date = new Date(`${isoDateKey}T12:00:00`);
  date.setDate(date.getDate() + 1);
  return formatShortDate(date);
}

function readDateFromHash() {
  const raw = window.location.hash.replace(/^#/, "");
  if (!raw.startsWith(HASH_PREFIX)) {
    return null;
  }
  const key = raw.slice(HASH_PREFIX.length);
  return /^\d{4}-\d{2}-\d{2}$/.test(key) ? key : null;
}

function writeDateToHash(isoDateKey) {
  const next = `${HASH_PREFIX}${isoDateKey}`;
  if (window.location.hash.replace(/^#/, "") !== next) {
    window.location.hash = next;
  }
}

function createInsightBlock(title, body, accent) {
  const section = document.createElement("section");
  section.className = "os-section";

  const header = createSectionHeader(title, accent);
  const card = document.createElement("article");
  card.className = "content-card";

  const text = document.createElement("pre");
  text.className = "daily-insights__body";
  text.textContent = body || "(nothing recorded)";

  card.appendChild(text);
  section.append(header, card);
  return section;
}

function createEmptyState(message, hint, action) {
  const wrap = document.createElement("div");
  wrap.className = "os-empty-state-wrap";

  const empty = document.createElement("p");
  empty.className = "os-empty-state";
  empty.textContent = message;
  wrap.appendChild(empty);

  if (hint) {
    const hintEl = document.createElement("p");
    hintEl.className = "os-empty-state os-empty-state--hint";
    hintEl.textContent = hint;
    wrap.appendChild(hintEl);
  }

  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "daily-insights__action-btn";
    btn.textContent = action.label;
    btn.addEventListener("click", action.onClick);
    wrap.appendChild(btn);
  }

  return wrap;
}

function createPendingBanner(count, onCatchUp) {
  const bar = document.createElement("div");
  bar.className = "daily-insights__pending-bar";

  const text = document.createElement("p");
  text.className = "daily-insights__pending-text";
  text.textContent = `${count} older day(s) have activity but no summary yet.`;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "daily-insights__action-btn daily-insights__action-btn--inline";
  btn.textContent = `Generate all ${count}`;
  btn.addEventListener("click", onCatchUp);

  bar.append(text, btn);
  return bar;
}

function createDateNavigator({ onChange }) {
  const bar = document.createElement("div");
  bar.className = "daily-insights__date-bar";

  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "daily-insights__nav-btn";
  prevBtn.setAttribute("aria-label", "Older summary");
  prevBtn.textContent = "‹";

  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "daily-insights__nav-btn";
  nextBtn.setAttribute("aria-label", "Newer summary");
  nextBtn.textContent = "›";

  const dateInput = document.createElement("input");
  dateInput.type = "date";
  dateInput.className = "daily-insights__date-input";
  dateInput.setAttribute("aria-label", "Pick summary date");

  const chipRow = document.createElement("div");
  chipRow.className = "daily-insights__date-chips";

  bar.append(prevBtn, dateInput, nextBtn, chipRow);

  let availableKeys = [];
  let pendingKeys = [];
  let selectedKey = null;

  function setDateKeys({ saved = [], pending = [] }) {
    availableKeys = [...saved];
    pendingKeys = pending.filter((key) => !availableKeys.includes(key));
    const allKeys = [...new Set([...availableKeys, ...pendingKeys])].sort((a, b) =>
      b.localeCompare(a)
    );

    chipRow.replaceChildren();
    allKeys.forEach((key) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "daily-insights__date-chip";
      if (pendingKeys.includes(key)) {
        chip.classList.add("is-pending");
      }
      chip.dataset.date = key;
      const label = formatRelativeDayLabel(key);
      chip.textContent = pendingKeys.includes(key) ? `${label} · pending` : label;
      chip.title = key;
      chip.addEventListener("click", () => onChange(key));
      chipRow.appendChild(chip);
    });
    updateControls(allKeys);
  }

  function setAvailableKeys(keys) {
    setDateKeys({ saved: keys, pending: pendingKeys });
  }

  function setSelectedKey(key) {
    selectedKey = key;
    if (key) {
      dateInput.value = key;
      writeDateToHash(key);
    }
    chipRow.querySelectorAll(".daily-insights__date-chip").forEach((chip) => {
      chip.classList.toggle("is-active", chip.dataset.date === key);
    });
    const allKeys = [...new Set([...availableKeys, ...pendingKeys])].sort((a, b) =>
      b.localeCompare(a)
    );
    updateControls(allKeys);
  }

  function updateControls(allKeys) {
    const index = allKeys.indexOf(selectedKey);
    prevBtn.disabled = index < 0 || index >= allKeys.length - 1;
    nextBtn.disabled = index <= 0;
  }

  prevBtn.addEventListener("click", () => {
    const allKeys = [...new Set([...availableKeys, ...pendingKeys])].sort((a, b) =>
      b.localeCompare(a)
    );
    const index = allKeys.indexOf(selectedKey);
    if (index >= 0 && index < allKeys.length - 1) {
      onChange(allKeys[index + 1]);
    }
  });

  nextBtn.addEventListener("click", () => {
    const allKeys = [...new Set([...availableKeys, ...pendingKeys])].sort((a, b) =>
      b.localeCompare(a)
    );
    const index = allKeys.indexOf(selectedKey);
    if (index > 0) {
      onChange(allKeys[index - 1]);
    }
  });

  dateInput.addEventListener("change", () => {
    if (dateInput.value) {
      onChange(dateInput.value);
    }
  });

  window.addEventListener("hashchange", () => {
    const fromHash = readDateFromHash();
    if (fromHash && fromHash !== selectedKey) {
      onChange(fromHash);
    }
  });

  return {
    element: bar,
    setAvailableKeys,
    setDateKeys,
    setSelectedKey,
    getSelectedKey: () => selectedKey
  };
}

export function createDailyInsightsPage() {
  const page = document.createElement("section");
  page.className = "os-page os-page--insights";

  const hero = document.createElement("article");
  hero.className = "hero-card";

  const greeting = document.createElement("h2");
  greeting.className = "hero-card__greeting";
  greeting.textContent = getGreeting("there");

  const context = document.createElement("p");
  context.className = "hero-card__context";
  context.textContent = "Loading summaries…";

  const stats = document.createElement("div");
  stats.className = "stat-row";

  hero.append(greeting, context, stats);

  const host = document.createElement("div");
  host.className = "daily-insights__host";

  const pendingHost = document.createElement("div");
  pendingHost.className = "daily-insights__pending-host";

  let selectedDateKey = readDateFromHash();
  let availableKeys = [];
  let pendingKeys = [];
  let pendingByDate = new Map();
  let loading = false;
  let generating = false;

  const navigator = createDateNavigator({
    onChange: (key) => {
      selectedDateKey = key;
      navigator.setSelectedKey(key);
      loadSummaryForDate(key);
    }
  });

  page.append(hero, navigator.element, pendingHost, host);

  async function runGenerateForDate(isoDateKey) {
    if (generating || !isoDateKey) {
      return;
    }
    generating = true;
    context.textContent = `Generating summary for ${isoDateKey}… (OpenAI may take a minute)`;
    host.replaceChildren(createEmptyState("Generating…", "Compressing OCR and calling OpenAI.", null));

    try {
      await generateDailySummaryForDate(isoDateKey);
      await loadIndex();
      selectedDateKey = isoDateKey;
      navigator.setSelectedKey(isoDateKey);
      await loadSummaryForDate(isoDateKey);
    } catch (error) {
      host.replaceChildren(
        createEmptyState(`Generate failed — ${error.message}`, null, {
          label: "Try again",
          onClick: () => runGenerateForDate(isoDateKey)
        })
      );
    } finally {
      generating = false;
    }
  }

  async function runCatchUpAll() {
    if (generating) {
      return;
    }
    generating = true;
    context.textContent = "Generating all pending daily summaries…";
    host.replaceChildren(createEmptyState("Generating pending days…", null, null));

    try {
      const result = await catchUpPendingSummaries();
      const count = result?.items?.length ?? 0;
      context.textContent = count
        ? `Generated ${count} summary day(s).`
        : "No pending days left to generate.";
      await loadIndex();
    } catch (error) {
      host.replaceChildren(createEmptyState(`Catch-up failed — ${error.message}`, null));
    } finally {
      generating = false;
    }
  }

  function renderPendingBanner() {
    pendingHost.replaceChildren();
    if (pendingKeys.length === 0) {
      return;
    }
    pendingHost.appendChild(
      createPendingBanner(pendingKeys.length, () => runCatchUpAll())
    );
  }

  async function loadIndex() {
    const [data, pendingData] = await Promise.all([
      listDailySummaries({ limit: 120 }),
      getPendingSummaries().catch(() => ({ items: [] }))
    ]);

    availableKeys = (data.items ?? [])
      .map((item) => toISODateKey(item.period_start))
      .filter(Boolean);

    pendingByDate = new Map();
    pendingKeys = (pendingData.items ?? [])
      .map((item) => {
        const key = toISODateKey(item.period_start);
        if (key) {
          pendingByDate.set(key, item.unsummarized_chunk_count ?? 0);
        }
        return key;
      })
      .filter(Boolean);

    navigator.setDateKeys({ saved: availableKeys, pending: pendingKeys });
    renderPendingBanner();

    if (!selectedDateKey) {
      if (availableKeys.length > 0) {
        selectedDateKey = availableKeys[0];
      } else if (pendingKeys.length > 0) {
        selectedDateKey = pendingKeys[0];
      }
    }
    if (selectedDateKey) {
      navigator.setSelectedKey(selectedDateKey);
      await loadSummaryForDate(selectedDateKey);
    } else {
      renderNoSummaries();
    }
  }

  function renderNoSummaries() {
    context.textContent = "No summaries on the server yet.";
    stats.replaceChildren(createStatChip("—", "Days", "info"));
    const hint =
      pendingKeys.length > 0
        ? `${pendingKeys.length} day(s) have synced activity — generate to preview the UI.`
        : "Activity syncs from your desktop client. The server generates one summary per calendar day.";
    const action =
      pendingKeys.length > 0
        ? { label: "Generate all pending days", onClick: () => runCatchUpAll() }
        : null;
    host.replaceChildren(createEmptyState("No daily summaries yet.", hint, action));
  }

  async function loadSummaryForDate(isoDateKey) {
    if (!isoDateKey || loading || generating) {
      return;
    }
    loading = true;

    let summary = null;
    let summaryStats = null;

    try {
      [summary, summaryStats] = await Promise.all([
        getDailySummaryForDate(isoDateKey).catch((error) => {
          if (
            String(error.message).includes("404") ||
            String(error.message).includes("No daily summary")
          ) {
            return null;
          }
          throw error;
        }),
        getSummaryStats().catch(() => null)
      ]);
    } catch (error) {
      host.replaceChildren(
        createEmptyState(`Could not load summary — ${error.message}`, null)
      );
      context.textContent = "Server summary API unavailable.";
      loading = false;
      return;
    }

    const relative = formatRelativeDayLabel(isoDateKey);
    greeting.textContent = getGreeting("there");

    if (!summary) {
      const chunkCount = pendingByDate.get(isoDateKey) ?? 0;
      context.textContent = `${relative} · ${isoDateKey} — not generated yet`;
      stats.replaceChildren(
        createStatChip(chunkCount || "—", "Chunks ready", chunkCount ? "info" : "urgent"),
        createStatChip(availableKeys.length, "Saved days", "info")
      );
      const hint = chunkCount
        ? `${chunkCount} activity group(s) synced for this day. Generate now to preview the summary UI.`
        : "No synced activity for this date on the server — pick a pending day or sync from the desktop client.";
      host.replaceChildren(
        createEmptyState(
          `No summary for ${formatShortDate(`${isoDateKey}T12:00:00`)}.`,
          hint,
          chunkCount
            ? {
                label: "Generate now",
                onClick: () => runGenerateForDate(isoDateKey)
              }
            : null
        )
      );
      loading = false;
      return;
    }

    const dayLabel = formatDayLabel(summary.period_start);
    context.textContent = `${relative} · ${dayLabel} · ${summary.chunk_count} groups`;

    stats.replaceChildren(
      createStatChip(summary.chunk_count, "Chunks", "info"),
      createStatChip(
        summary.prompt_tokens + summary.completion_tokens,
        "Tokens",
        "success"
      ),
      createStatChip(availableKeys.length, "Days saved", "info")
    );

    host.replaceChildren(
      createInsightBlock(`What you did · ${dayLabel}`, summary.summary_text, "success"),
      createInsightBlock(
        `Outlook for ${nextDayLabel(isoDateKey)}`,
        summary.predictions_text || "No outlook generated for the next day.",
        "info"
      )
    );
    loading = false;
  }

  async function reload() {
    try {
      await loadIndex();
    } catch (error) {
      host.replaceChildren(
        createEmptyState(`Could not load summaries — ${error.message}`, null)
      );
      context.textContent = "Server summary API unavailable.";
    }
  }

  reload();
  const timer = window.setInterval(reload, REFRESH_MS);
  page.addEventListener("jarvis:destroy", () => window.clearInterval(timer));

  return page;
}
