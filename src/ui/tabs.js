import { startGreetingClock } from "../lib/time.js";
import { createMeetingPage } from "../pages/meeting-page.js";
import { createSmartMessagingPage } from "../pages/smart-messaging-page.js";
import { createSummaryPage } from "../pages/summary-page.js";
import { createMenuIcon } from "./icons.js";

const MENU_ITEMS = [
  {
    id: "summary",
    label: "Summary",
    description: "Daily & hourly overview",
    renderPanel: createSummaryPage
  },
  {
    id: "messaging",
    label: "WhatsApp",
    description: "Smart messaging",
    renderPanel: createSmartMessagingPage
  },
  {
    id: "meetings",
    label: "Meetings",
    description: "Schedule & prep",
    renderPanel: createMeetingPage
  }
];

export function createTabSystem() {
  const wrapper = document.createElement("div");
  wrapper.className = "os-layout";

  const sidebar = document.createElement("aside");
  sidebar.className = "os-sidebar";

  const brand = document.createElement("div");
  brand.className = "os-sidebar__brand";

  const logo = document.createElement("span");
  logo.className = "os-sidebar__logo";
  logo.setAttribute("aria-hidden", "true");
  logo.textContent = "P";

  const brandText = document.createElement("div");
  brandText.className = "os-sidebar__brand-text";

  const title = document.createElement("h1");
  title.className = "os-sidebar__title";
  title.textContent = "Personal OS";

  const greeting = document.createElement("p");
  greeting.className = "os-sidebar__greeting";
  startGreetingClock(greeting, "Sujay");

  const status = document.createElement("p");
  status.className = "os-sidebar__status";
  status.innerHTML = '<span class="status-dot"></span> Watching';

  brandText.append(title, greeting, status);
  brand.append(logo, brandText);

  const menuLabel = document.createElement("p");
  menuLabel.className = "os-sidebar__menu-label";
  menuLabel.textContent = "Menu";

  const menu = document.createElement("nav");
  menu.className = "os-sidebar__menu";
  menu.setAttribute("role", "tablist");
  menu.setAttribute("aria-label", "Personal OS navigation");

  const main = document.createElement("main");
  main.className = "os-main";

  const panelHost = document.createElement("div");
  panelHost.className = "os-content";

  const buttons = [];
  const panels = [];

  MENU_ITEMS.forEach((item, index) => {
    const button = document.createElement("button");
    button.className = "os-sidebar__item";
    button.id = `tab-${item.id}`;
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `panel-${item.id}`);
    button.dataset.tabTarget = `panel-${item.id}`;

    const icon = createMenuIcon(item.id);

    const textWrap = document.createElement("span");
    textWrap.className = "os-sidebar__item-text";

    const label = document.createElement("span");
    label.className = "os-sidebar__item-label";
    label.textContent = item.label;

    const desc = document.createElement("span");
    desc.className = "os-sidebar__item-desc";
    desc.textContent = item.description;

    textWrap.append(label, desc);
    button.append(icon, textWrap);

    const panel = item.renderPanel();
    panel.id = `panel-${item.id}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", button.id);

    if (index !== 0) {
      panel.hidden = true;
      button.tabIndex = -1;
      button.setAttribute("aria-selected", "false");
    } else {
      button.classList.add("is-active");
      button.setAttribute("aria-selected", "true");
      panel.classList.add("is-active");
    }

    button.addEventListener("click", () => activateTab(buttons, panels, panel.id));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
        return;
      }

      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const nextIndex = (index + direction + MENU_ITEMS.length) % MENU_ITEMS.length;
      buttons[nextIndex].focus();
      activateTab(buttons, panels, panels[nextIndex].id);
    });

    buttons.push(button);
    panels.push(panel);
    menu.appendChild(button);
    panelHost.appendChild(panel);
  });

  sidebar.append(brand, menuLabel, menu);
  main.appendChild(panelHost);
  wrapper.append(sidebar, main);

  return wrapper;
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
