import { startGreetingClock } from "../lib/time.js";
import { createMeetingPage } from "../pages/meeting-page.js";
import { createPlaceholderPage } from "../pages/placeholder-page.js";
import { createSummaryPage } from "../pages/summary-page.js";
import { createCommandBar } from "./components/command-bar.js";

const MENU_ITEMS = [
  {
    id: "now",
    label: "Now",
    renderPanel: createSummaryPage
  },
  {
    id: "patterns",
    label: "Patterns",
    renderPanel: () =>
      createPlaceholderPage(
        "Patterns",
        "Recurring habits and communication patterns will surface here."
      )
  },
  {
    id: "okrs",
    label: "OKRs",
    renderPanel: () =>
      createPlaceholderPage("OKRs", "Track quarterly objectives and key results.")
  },
  {
    id: "people",
    label: "People",
    renderPanel: () =>
      createPlaceholderPage("People", "Your contacts, context, and relationship notes.")
  },
  {
    id: "live",
    label: "Live",
    badge: true,
    renderPanel: createMeetingPage
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

    const label = document.createElement("span");
    label.className = "os-panel__tab-label";
    label.textContent = item.label;

    button.appendChild(label);

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
