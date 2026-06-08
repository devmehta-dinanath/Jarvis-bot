import { createHeader } from "./header.js";
import { createTabSystem } from "./tabs.js";

const SOURCE_DEFINITIONS = [
  {
    id: "whatsapp",
    label: "WhatsApp",
    icon: "W",
    tagline: "Smart messaging workspace for WhatsApp conversations.",
    frameTitle: "WhatsApp Web",
    frameHtml: `
      <html>
        <body style="margin:0;font-family:Segoe UI,Tahoma,sans-serif;background:#0b141a;color:#e9edef;">
          <div style="height:100vh;display:grid;grid-template-rows:64px 1fr;background:linear-gradient(180deg,#111b21,#0b141a);">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:0 20px;background:#202c33;border-bottom:1px solid rgba(255,255,255,0.06);">
              <strong>WhatsApp</strong>
              <span style="color:#8696a0;font-size:12px;">Preview iframe</span>
            </div>
            <div style="padding:18px;display:grid;grid-template-columns:280px 1fr;gap:16px;">
              <div style="border-radius:18px;background:#111b21;padding:14px;">
                <div style="height:42px;border-radius:12px;background:#202c33;margin-bottom:12px;"></div>
                <div style="height:64px;border-radius:14px;background:#202c33;margin-bottom:10px;"></div>
                <div style="height:64px;border-radius:14px;background:#202c33;margin-bottom:10px;"></div>
                <div style="height:64px;border-radius:14px;background:#202c33;"></div>
              </div>
              <div style="border-radius:18px;background:#0f1720;padding:18px;display:flex;flex-direction:column;gap:14px;">
                <div style="align-self:flex-start;max-width:58%;padding:14px 16px;border-radius:16px;background:#202c33;">Client asked for a quick approval update.</div>
                <div style="align-self:flex-end;max-width:58%;padding:14px 16px;border-radius:16px;background:#005c4b;">Draft response will appear in Smart Messaging.</div>
                <div style="margin-top:auto;height:48px;border-radius:14px;background:#202c33;"></div>
              </div>
            </div>
          </div>
        </body>
      </html>
    `
  },
  {
    id: "gmail",
    label: "Gmail",
    icon: "G",
    tagline: "Smart messaging workspace for email replies and summaries.",
    frameTitle: "Gmail Workspace",
    frameHtml: `
      <html>
        <body style="margin:0;font-family:Segoe UI,Tahoma,sans-serif;background:#f6f8fc;color:#1f1f1f;">
          <div style="height:100vh;display:grid;grid-template-rows:64px 1fr;">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:0 20px;background:#ffffff;border-bottom:1px solid rgba(32,33,36,0.08);">
              <strong>Gmail</strong>
              <span style="color:#5f6368;font-size:12px;">Preview iframe</span>
            </div>
            <div style="padding:18px;display:grid;grid-template-columns:240px 1fr;gap:16px;background:#f6f8fc;">
              <div style="border-radius:18px;background:#ffffff;padding:14px;box-shadow:0 8px 20px rgba(60,64,67,0.08);">
                <div style="height:18px;width:60%;border-radius:999px;background:#e8eaed;margin-bottom:18px;"></div>
                <div style="height:56px;border-radius:14px;background:#f1f3f4;margin-bottom:10px;"></div>
                <div style="height:56px;border-radius:14px;background:#f1f3f4;margin-bottom:10px;"></div>
                <div style="height:56px;border-radius:14px;background:#f1f3f4;"></div>
              </div>
              <div style="border-radius:18px;background:#ffffff;padding:18px;box-shadow:0 8px 20px rgba(60,64,67,0.08);display:flex;flex-direction:column;gap:14px;">
                <div style="height:20px;width:42%;border-radius:999px;background:#e8eaed;"></div>
                <div style="height:14px;width:100%;border-radius:999px;background:#f1f3f4;"></div>
                <div style="height:14px;width:92%;border-radius:999px;background:#f1f3f4;"></div>
                <div style="height:14px;width:88%;border-radius:999px;background:#f1f3f4;"></div>
                <div style="margin-top:auto;height:48px;border-radius:14px;background:#f1f3f4;"></div>
              </div>
            </div>
          </div>
        </body>
      </html>
    `
  }
];

export function renderInsightsApp(root, options) {
  root.replaceChildren();

  const layout = document.createElement("section");
  layout.className = "workspace-layout";
  layout.setAttribute("aria-label", "Messaging workspace");

  let activeSource = SOURCE_DEFINITIONS[0];
  const rightPanel = document.createElement("aside");
  rightPanel.className = "workspace-sidebar";
  const previewPanel = document.createElement("section");
  previewPanel.className = "workspace-preview";

  const sourceBar = createSourceTabs(SOURCE_DEFINITIONS, activeSource.id, (sourceId) => {
    activeSource = SOURCE_DEFINITIONS.find((source) => source.id === sourceId) ?? SOURCE_DEFINITIONS[0];
    updatePreview(previewPanel, activeSource);
    renderSidebar(rightPanel, activeSource, options);
  });

  layout.append(sourceBar, previewPanel, rightPanel);

  updatePreview(previewPanel, activeSource);
  renderSidebar(rightPanel, activeSource, options);

  root.appendChild(layout);
}

function createSourceTabs(sources, activeId, onChange) {
  const wrapper = document.createElement("header");
  wrapper.className = "source-bar";

  const intro = document.createElement("div");
  intro.className = "source-bar__intro";

  const label = document.createElement("p");
  label.className = "section-label";
  label.textContent = "Personal OS";

  const title = document.createElement("h1");
  title.textContent = "Messaging Workspace";

  intro.append(label, title);

  const tabs = document.createElement("div");
  tabs.className = "source-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Messaging sources");

  const buttons = sources.map((source) => {
    const button = document.createElement("button");
    button.className = "source-tab";
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(source.id === activeId));
    button.classList.toggle("is-active", source.id === activeId);

    const icon = document.createElement("span");
    icon.className = `source-tab__icon source-tab__icon--${source.id}`;
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = source.icon;

    const label = document.createElement("span");
    label.className = "source-tab__label";
    label.textContent = source.label;

    button.append(icon, label);

    button.addEventListener("click", () => {
      buttons.forEach((item) => {
        const isActive = item === button;
        item.classList.toggle("is-active", isActive);
        item.setAttribute("aria-selected", String(isActive));
      });
      onChange(source.id);
    });

    return button;
  });

  buttons.forEach((button) => tabs.appendChild(button));
  wrapper.append(intro, tabs);

  return wrapper;
}

function updatePreview(container, source) {
  container.replaceChildren();

  const shell = document.createElement("div");
  shell.className = "preview-shell";

  const header = document.createElement("div");
  header.className = "preview-shell__header";

  const textWrap = document.createElement("div");
  textWrap.className = "preview-shell__title-group";

  const badge = document.createElement("span");
  badge.className = `preview-shell__badge preview-shell__badge--${source.id}`;
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = source.icon;

  const title = document.createElement("h2");
  title.textContent = source.frameTitle;
  const copy = document.createElement("p");
  copy.textContent = source.tagline;
  textWrap.append(badge, title, copy);

  header.appendChild(textWrap);

  const frame = document.createElement("iframe");
  frame.className = "preview-frame";
  frame.title = `${source.label} preview`;
  frame.setAttribute("loading", "lazy");
  frame.srcdoc = source.frameHtml;

  shell.append(header, frame);
  container.appendChild(shell);
}

function renderSidebar(container, source, options) {
  container.replaceChildren();
  container.appendChild(createHeader({ ...options, source }));
  container.appendChild(createTabSystem(source));
}
