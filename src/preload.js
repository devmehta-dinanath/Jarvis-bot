const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("jarvisApp", {
  platform: process.platform
});
 