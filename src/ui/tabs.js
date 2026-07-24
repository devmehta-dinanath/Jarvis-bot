import { createDailyInsightsPage } from "../pages/daily-insights-page.js";
import { createMeetingPage } from "../pages/meeting-page.js";
import { createSettingsPage } from "../pages/settings-page.js";
import { createSummaryPage } from "../pages/summary-page.js";
import { createCommandBar } from "./components/command-bar.js";

const ICON_SVG_ATTRS =
  'width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';

const TAB_ICONS = {
  whatsapp:
    `<svg ${ICON_SVG_ATTRS}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  calendar:
    `<svg ${ICON_SVG_ATTRS}><rect x="3" y="4" width="18" height="18" rx="2"/>` +
    '<line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/>' +
    '<line x1="3" y1="10" x2="21" y2="10"/></svg>',
  summary:
    `<svg ${ICON_SVG_ATTRS}><line x1="18" y1="20" x2="18" y2="10"/>` +
    '<line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
  settings:
    `<svg ${ICON_SVG_ATTRS}><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>` +
    '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>' +
    '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>' +
    '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>' +
    '<line x1="17" y1="16" x2="23" y2="16"/></svg>'
};

const MENU_ITEMS = [
  {
    id: "whatsapp",
    label: "Inbox",
    renderPanel: createSummaryPage
  },
  {
    id: "calendar",
    label: "Calendar",
    renderPanel: createMeetingPage
  },
  {
    id: "summary",
    label: "Summary",
    renderPanel: createDailyInsightsPage
  },
  {
    id: "settings",
    label: "Settings",
    renderPanel: createSettingsPage
  }
];

export function createTabSystem() {
  const panel = document.createElement("div");
  panel.className = "os-panel";

  const header = document.createElement("header");
  header.className = "os-panel__header";

  const brand = document.createElement("div");
  brand.className = "os-panel__brand";

  const logo = document.createElement("span");
  logo.className = "os-panel__logo";
  logo.setAttribute("aria-hidden", "true");
  logo.textContent = "P";

  const brandText = document.createElement("div");
  brandText.className = "os-panel__brand-text";

  const title = document.createElement("h1");
  title.className = "os-panel__title";
  title.textContent = "Personal OS";

  const status = document.createElement("p");
  status.className = "os-panel__status";
  status.innerHTML =
    '<span class="status-dot"></span> Watching <span class="os-panel__status-sep">·</span> Silent mode';

  brandText.append(title, status);
  brand.append(logo, brandText);

  const tabs = document.createElement("nav");
  tabs.className = "os-panel__tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Personal OS navigation");

  const body = document.createElement("div");
  body.className = "os-panel__body";

  const panelHost = document.createElement("div");
  panelHost.className = "os-content";

  const buttons = [];
  const panels = [];

  MENU_ITEMS.forEach((item, index) => {
    const button = document.createElement("button");
    button.className = "os-panel__tab";
    button.id = `tab-${item.id}`;
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `panel-${item.id}`);
    button.dataset.tabTarget = `panel-${item.id}`;

    const icon = document.createElement("span");
    icon.className = "os-panel__tab-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = TAB_ICONS[item.id] ?? "";

    const label = document.createElement("span");
    label.className = "os-panel__tab-label";
    label.textContent = item.label;

    button.append(icon, label);

    if (item.badge) {
      const dot = document.createElement("span");
      dot.className = "os-panel__tab-badge";
      dot.setAttribute("aria-label", "Needs attention");
      button.appendChild(dot);
    }

    const panelEl = item.renderPanel();
    panelEl.id = `panel-${item.id}`;
    panelEl.setAttribute("role", "tabpanel");
    panelEl.setAttribute("aria-labelledby", button.id);

    if (index !== 0) {
      panelEl.hidden = true;
      button.tabIndex = -1;
      button.setAttribute("aria-selected", "false");
    } else {
      button.classList.add("is-active");
      button.setAttribute("aria-selected", "true");
      panelEl.classList.add("is-active");
    }

    button.addEventListener("click", () => activateTab(buttons, panels, panelEl.id));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
        return;
      }

      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (index + direction + MENU_ITEMS.length) % MENU_ITEMS.length;
      buttons[nextIndex].focus();
      activateTab(buttons, panels, panels[nextIndex].id);
    });

    buttons.push(button);
    panels.push(panelEl);
    tabs.appendChild(button);
    panelHost.appendChild(panelEl);
  });

  header.append(brand, tabs);
  body.appendChild(panelHost);
  panel.append(header, body, createCommandBar());

  if (window.location.hash.replace(/^#/, "").startsWith("summary/")) {
    activateTab(buttons, panels, "panel-summary");
  }

  return panel;
}

function activateTab(buttons, panels, targetId) {
  buttons.forEach((button) => {
    const isActive = button.dataset.tabTarget === targetId;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", String(isActive));
    button.tabIndex = isActive ? 0 : -1;
  });

  panels.forEach((panel) => {
    const isActive = panel.id === targetId;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });
}
