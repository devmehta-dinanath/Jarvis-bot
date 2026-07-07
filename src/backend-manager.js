const { app, dialog } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const DEFAULT_SERVER_URL = process.env.JARVIS_SERVER_URL || "https://jarvis-api.lilium.co.in";
const DEFAULT_SYNC_API_KEY =
  process.env.SYNC_API_KEY ||
  "b5967ac012fe968bc12b70f31d7d17be1d5912f9fb9e76bf97819ff0c8d6366b";
const BACKEND_WAIT_MS = 60_000;
const HEALTH_URL = "http://127.0.0.1:8000/health";

let backendProcess = null;
let screenpipeProcess = null;

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function logsDir() {
  const dir = path.join(app.getPath("userData"), "logs");
  ensureDir(dir);
  return dir;
}

function runtimeResourceRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "resources");
  }
  return path.join(__dirname, "..", "resources");
}

function platformResourceSubdir() {
  if (process.platform === "darwin") {
    return "mac";
  }
  if (process.platform === "win32") {
    return "win";
  }
  if (process.platform === "linux") {
    return "linux";
  }
  return "common";
}

function resolveBinaryPath(kind) {
  const envPath =
    kind === "backend" ? process.env.JARVIS_BACKEND_BIN : process.env.JARVIS_SCREENPIPE_BIN;
  if (envPath && fs.existsSync(envPath)) {
    return envPath;
  }

  const root = runtimeResourceRoot();
  const platformDir = path.join(root, platformResourceSubdir());
  const commonDir = path.join(root, "common");
  const ext = process.platform === "win32" ? ".exe" : "";

  const candidates =
    kind === "backend"
      ? [path.join(platformDir, `backend${ext}`), path.join(commonDir, `backend${ext}`)]
      : [
          path.join(platformDir, `screenpipe${ext}`),
          path.join(platformDir, "screenpipe-lib", `screenpipe${ext}`),
          path.join(commonDir, `screenpipe${ext}`)
        ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function resolveScreenpipeCwd(screenpipeBin) {
  const libDir = path.join(path.dirname(screenpipeBin), "screenpipe-lib");
  if (fs.existsSync(libDir)) {
    return libDir;
  }
  return path.dirname(screenpipeBin);
}

function clientDataDir() {
  const dir = path.join(app.getPath("userData"), "data");
  ensureDir(dir);
  return dir;
}

function appendLog(fileName, message) {
  const filePath = path.join(logsDir(), fileName);
  fs.appendFileSync(filePath, `${new Date().toISOString()} ${message}\n`);
}

function spawnManagedProcess(command, args, options = {}) {
  const processRef = spawn(command, args, {
    shell: false,
    windowsHide: true,
    ...options
  });
  return processRef;
}

function waitForBackendHealth(timeoutMs = BACKEND_WAIT_MS) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    const check = async () => {
      try {
        const response = await fetch(HEALTH_URL);
        if (response.ok) {
          resolve(true);
          return;
        }
      } catch (_err) {
        // Keep retrying until timeout.
      }

      if (Date.now() - startedAt >= timeoutMs) {
        reject(new Error(`Backend did not become healthy at ${HEALTH_URL} within ${timeoutMs}ms`));
        return;
      }
      setTimeout(check, 1500);
    };
    setTimeout(check, 400);
  });
}

function buildBackendEnv() {
  const env = { ...process.env };
  const dataDir = clientDataDir();
  const dbPath = path.join(dataDir, "client-buffer.db").replace(/\\/g, "/");

  env.APP_ROLE = env.APP_ROLE || "client";
  env.RUNNING_IN_DOCKER = "false";
  env.AUTO_START_SERVICES = "true";
  env.WHATSAPP_ENABLED = "false";
  env.SUMMARY_ENABLED = "false";
  env.CHROMA_ENABLED = "false";
  env.SCREENPIPE_ENABLED = "true";
  env.SCREENPIPE_START_CLI = "false";
  env.SCREENPIPE_API_URL = env.SCREENPIPE_API_URL || "http://127.0.0.1:3030";
  env.JARVIS_SERVER_URL = env.JARVIS_SERVER_URL || DEFAULT_SERVER_URL;
  env.SYNC_ENABLED = env.SYNC_ENABLED || "true";
  env.DATABASE_URL = env.DATABASE_URL || `sqlite:///${dbPath}`;
  if (DEFAULT_SYNC_API_KEY && !env.SYNC_API_KEY) {
    env.SYNC_API_KEY = DEFAULT_SYNC_API_KEY;
  }
  return env;
}

function startScreenpipe() {
  if (screenpipeProcess) {
    return;
  }
  const screenpipeBin = resolveBinaryPath("screenpipe");
  if (!screenpipeBin) {
    appendLog("runtime.log", "[runtime] screenpipe binary missing, skipping launch");
    return;
  }
  const env = { ...process.env };
  const args = ["record"];
  screenpipeProcess = spawnManagedProcess(screenpipeBin, args, {
    env,
    cwd: resolveScreenpipeCwd(screenpipeBin)
  });
  screenpipeProcess.stdout?.on("data", (chunk) =>
    appendLog("screenpipe.log", chunk.toString().trimEnd())
  );
  screenpipeProcess.stderr?.on("data", (chunk) =>
    appendLog("screenpipe.log", chunk.toString().trimEnd())
  );
  screenpipeProcess.on("exit", (code) => {
    appendLog("runtime.log", `[runtime] screenpipe exited code=${code}`);
    screenpipeProcess = null;
  });
}

function startBackend() {
  if (backendProcess) {
    return;
  }

  const backendBin = resolveBinaryPath("backend");
  if (!backendBin) {
    appendLog("runtime.log", "[runtime] backend binary missing, skipping launch");
    return;
  }

  const env = buildBackendEnv();
  backendProcess = spawnManagedProcess(backendBin, [], { env });
  backendProcess.stdout?.on("data", (chunk) =>
    appendLog("backend.log", chunk.toString().trimEnd())
  );
  backendProcess.stderr?.on("data", (chunk) =>
    appendLog("backend.log", chunk.toString().trimEnd())
  );
  backendProcess.on("exit", (code) => {
    appendLog("runtime.log", `[runtime] backend exited code=${code}`);
    backendProcess = null;
  });
}

function showRuntimeError(message) {
  appendLog("runtime.log", `[runtime] error: ${message}`);
  dialog.showErrorBox(
    "Jarvis could not start",
    `${message}\n\nLogs: ${logsDir()}`
  );
}

async function startRuntime() {
  // In dev mode, preserve existing flow unless explicitly requested.
  if (!app.isPackaged && process.env.JARVIS_LAUNCH_RUNTIME !== "1") {
    return;
  }
  appendLog("runtime.log", "[runtime] starting managed runtime processes");

  const backendBin = resolveBinaryPath("backend");
  const screenpipeBin = resolveBinaryPath("screenpipe");
  if (!backendBin) {
    showRuntimeError(
      "The local backend is missing from this installer. Re-download the latest Windows build from GitHub Actions (jarvis-windows-installers)."
    );
    throw new Error("backend binary missing");
  }
  if (!screenpipeBin) {
    showRuntimeError(
      "Screenpipe is missing from this installer. Re-download the latest Windows build from GitHub Actions (jarvis-windows-installers)."
    );
    throw new Error("screenpipe binary missing");
  }

  startScreenpipe();
  startBackend();
  await waitForBackendHealth();
  appendLog("runtime.log", "[runtime] backend health check passed");
}

function stopRuntime() {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
  if (screenpipeProcess) {
    screenpipeProcess.kill();
    screenpipeProcess = null;
  }
}

module.exports = {
  startRuntime,
  stopRuntime
};
