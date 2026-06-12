import { createTabSystem } from "./tabs.js";

export function renderInsightsApp(root) {
  root.replaceChildren();
  root.appendChild(createTabSystem());
}
