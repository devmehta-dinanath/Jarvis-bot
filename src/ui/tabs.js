import { createMeetingPage } from "../pages/meeting-page.js";
import { createSmartMessagingPage } from "../pages/smart-messaging-page.js";
import { createSummaryPage } from "../pages/summary-page.js";

const TAB_DEFINITIONS = [
  {
    id: "smart-messaging",
    label: "Smart Messaging",
    renderPanel: createSmartMessagingPage
  },
  {
    id: "summary",
    label: "Summary",
    renderPanel: createSummaryPage
  },
  {
    id: "meeting",
    label: "Meeting",
    renderPanel: createMeetingPage
  }
];

export function createTabSystem(source) {
  const wrapper = document.createElement("section");
  wrapper.className = "sidebar-tabs";

  const tabList = document.createElement("div");
  tabList.className = "tabs";
  tabList.setAttribute("role", "tablist");
  tabList.setAttribute("aria-label", "Workspace insight tabs");

  const panelHost = document.createElement("div");
  panelHost.className = "panel-host";

  const buttons = [];
  const panels = [];

  TAB_DEFINITIONS.forEach((tab, index) => {
    const button = document.createElement("button");
    button.className = "tab-button";
    button.id = `tab-${tab.id}`;
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `panel-${tab.id}`);
    button.dataset.tabTarget = `panel-${tab.id}`;
    button.textContent = tab.label;

    const panel = tab.renderPanel(source);
    panel.id = `panel-${tab.id}`;
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
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
        return;
      }

      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (index + direction + TAB_DEFINITIONS.length) % TAB_DEFINITIONS.length;
      buttons[nextIndex].focus();
      activateTab(buttons, panels, panels[nextIndex].id);
    });

    buttons.push(button);
    panels.push(panel);
    tabList.appendChild(button);
    panelHost.appendChild(panel);
  });

  wrapper.append(tabList, panelHost);

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
