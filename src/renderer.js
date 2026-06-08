import { renderInsightsApp } from "./ui/app-shell.js";

const root = document.getElementById("app-root");
const platform = window.jarvisApp?.platform ?? "web";

if (root) {
  renderInsightsApp(root, { platform });
}
