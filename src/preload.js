const { contextBridge, ipcRenderer } = require("electron");
const DEFAULT_LOCAL_API_URL = "http://127.0.0.1:8000";
const DEFAULT_SERVER_URL = "https://jarvis-api.lilium.co.in";

contextBridge.exposeInMainWorld("jarvisApp", {
  platform: process.platform,
  apiBase: process.env.JARVIS_API_URL ?? DEFAULT_LOCAL_API_URL,
  serverUrl: process.env.JARVIS_SERVER_URL ?? DEFAULT_SERVER_URL,
  openExternal(url) {
    return ipcRenderer.invoke("open-external", url);
  }
});
