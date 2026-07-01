const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvisApp", {
  platform: process.platform,
  apiBase: process.env.JARVIS_API_URL ?? "http://127.0.0.1:8000",
  serverUrl: process.env.JARVIS_SERVER_URL ?? process.env.JARVIS_API_URL ?? "http://127.0.0.1:8000",
  openExternal(url) {
    return ipcRenderer.invoke("open-external", url);
  }
});
