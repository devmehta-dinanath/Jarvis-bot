const { app, BrowserWindow, screen, ipcMain, shell } = require("electron");
const fs = require("fs");
const path = require("path");

function loadDotEnv() {
  const envPath = path.join(__dirname, "..", ".env");
  if (!fs.existsSync(envPath)) {
    return;
  }
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) {
      continue;
    }
    const eq = trimmed.indexOf("=");
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

loadDotEnv();

function getRightPanelBounds() {
  const { workArea } = screen.getPrimaryDisplay();
  const width = Math.round(workArea.width * 0.25);
  const height = workArea.height;

  return {
    width,
    height,
    x: workArea.x + workArea.width - width,
    y: workArea.y
  };
}

function createWindow() {
  const bounds = getRightPanelBounds();

  const mainWindow = new BrowserWindow({
    ...bounds,
    minWidth: 320,
    minHeight: 480,
    maxWidth: Math.round(screen.getPrimaryDisplay().workArea.width * 0.4),
    backgroundColor: "#121420",
    title: "Personal OS",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.loadFile(path.join(__dirname, "..", "index.html"));
}

app.whenReady().then(() => {
  ipcMain.handle("open-external", (_event, url) => {
    if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
      return false;
    }

    return shell.openExternal(url);
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
