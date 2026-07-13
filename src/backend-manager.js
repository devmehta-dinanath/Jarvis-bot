const { app, dialog } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const DEFAULT_SERVER_URL = process.env.JARVIS_SERVER_URL || "https://jarvis-api.lilium.co.in";
const DEFAULT_SYNC_API_KEY =
  process.env.SYNC_API_KEY ||
  "b5967ac012fe968bc12b70f31d7d17be1d5912f9fb9e76bf97819ff0c8d6366b";
const BACKEND_WAIT_MS = 120_000;
// macOS first launch downloads ffmpeg + ML models; 90s is too short for packaged builds.
const SCREENPIPE_WAIT_MS =
  process.platform === "darwin" ? 240_000 : 120_000;
const HEALTH_URL = "http://127.0.0.1:8000/health";
const SCREENPIPE_HEALTH_URL = "http://127.0.0.1:3030/health";

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

function clientDataDir() {
  const dir = path.join(app.getPath("userData"), "data");
  ensureDir(dir);
  return dir;
}

function clientMediaDir() {
  const dir = path.join(app.getPath("userData"), "media");
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

function binaryName(baseName) {
  return process.platform === "win32" ? `${baseName}.exe` : baseName;
}

function screenpipeLibCandidates(platformDir, commonDir) {
  const name = binaryName("screenpipe");
  return [
    path.join(platformDir, "screenpipe-lib", name),
    path.join(platformDir, `screenpipe-lib-${process.arch}`, name),
    path.join(platformDir, name),
    path.join(commonDir, "screenpipe-lib", name),
    path.join(commonDir, name)
  ];
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

  const candidates =
    kind === "backend"
      ? [
          path.join(platformDir, binaryName("backend")),
          path.join(commonDir, binaryName("backend"))
        ]
      : screenpipeLibCandidates(platformDir, commonDir);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function sqliteDatabaseUrl(dbPath) {
  const normalized = dbPath.replace(/\\/g, "/");
  if (normalized.startsWith("/")) {
    // SQLAlchemy absolute SQLite paths need four slashes: sqlite:////absolute/path
    return `sqlite:///${normalized}`;
  }
  return `sqlite:///${normalized}`;
}

function appendLog(fileName, message) {
  const filePath = path.join(logsDir(), fileName);
  fs.appendFileSync(filePath, `${new Date().toISOString()} ${message}\n`);
}

function spawnManagedProcess(name, command, args, options = {}) {
  appendLog("runtime.log", `[runtime] spawning ${name}: ${command} ${args.join(" ")}`);
  const processRef = spawn(command, args, {
    shell: false,
    windowsHide: true,
    ...options
  });
  processRef.on("error", (error) => {
    appendLog("runtime.log", `[runtime] ${name} spawn error: ${error.message}`);
  });
  return processRef;
}

function waitForUrl(url, timeoutMs, label, { acceptAnyHttpStatus = false } = {}) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    const check = async () => {
      try {
        const response = await fetch(url);
        if (response.ok || acceptAnyHttpStatus) {
          resolve(true);
          return;
        }
      } catch (_err) {
        // Keep retrying until timeout.
      }

      if (Date.now() - startedAt >= timeoutMs) {
        reject(new Error(`${label} did not become healthy at ${url} within ${timeoutMs}ms`));
        return;
      }
      setTimeout(check, 1500);
    };
    setTimeout(check, 400);
  });
}

function ensureScreenpipeCacheDirs() {
  if (process.platform !== "darwin") {
    return;
  }
  const home = app.getPath("home");
  ensureDir(path.join(home, "Library", "Caches", "screenpipe", "models"));
}

function ensureBundledFfmpeg(screenpipeCwd) {
  if (process.platform !== "darwin") {
    return;
  }

  const bundledFfmpeg = path.join(screenpipeCwd, "ffmpeg");
  if (fs.existsSync(bundledFfmpeg)) {
    return;
  }

  const candidates = [
    process.env.JARVIS_FFMPEG_SRC,
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    path.join(app.getPath("home"), ".local", "bin", "ffmpeg")
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (!candidate || !fs.existsSync(candidate)) {
      continue;
    }
    try {
      fs.copyFileSync(candidate, bundledFfmpeg);
      fs.chmodSync(bundledFfmpeg, 0o755);
      clearMacOsQuarantine(bundledFfmpeg);
      appendLog("runtime.log", `[runtime] bundled ffmpeg from ${candidate}`);
      return;
    } catch (error) {
      appendLog("runtime.log", `[runtime] failed to bundle ffmpeg from ${candidate}: ${error.message}`);
    }
  }

  appendLog(
    "runtime.log",
    "[runtime] ffmpeg not bundled; screenpipe may download it on first launch (slow)"
  );
}

function buildScreenpipeEnv(screenpipeCwd) {
  const env = { ...process.env };

  // Suppress CLI reminders about downloading the desktop app
  env.SCREENPIPE_NO_REMINDERS = "1";

  const pathEntries = [
    screenpipeCwd,
    path.join(app.getPath("home"), ".local", "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin"
  ];

  // Ensure the screenpipe binary directory is in PATH/LD_LIBRARY_PATH for DLL/lib loading
  if (process.platform === "linux") {
    env.LD_LIBRARY_PATH = `${screenpipeCwd}${path.delimiter}${env.LD_LIBRARY_PATH || ""}`;
  }

  env.PATH = [...pathEntries, env.PATH || ""].join(path.delimiter);

  // On macOS, also set DYLD_LIBRARY_PATH
  if (process.platform === "darwin") {
    env.DYLD_LIBRARY_PATH = `${screenpipeCwd}${path.delimiter}${env.DYLD_LIBRARY_PATH || ""}`;
  }

  return env;
}

function clearMacOsQuarantine(binaryPath) {
  if (process.platform !== "darwin") {
    return;
  }
  try {
    const { execSync } = require("child_process");
    execSync(`xattr -d com.apple.quarantine "${binaryPath}" 2>/dev/null || true`, {
      stdio: "ignore"
    });
    appendLog("runtime.log", `[runtime] cleared quarantine flag on ${binaryPath}`);
  } catch (err) {
    appendLog("runtime.log", `[runtime] failed to clear quarantine: ${err.message}`);
  }
}

function buildBackendEnv() {
  const env = { ...process.env };
  const dataDir = clientDataDir();
  const mediaDir = clientMediaDir();
  const dbPath = path.join(dataDir, "client-buffer.db");

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
  // Packaged client must use the app data dir — ignore inherited dev DATABASE_URL.
  env.DATABASE_URL = app.isPackaged
    ? sqliteDatabaseUrl(dbPath)
    : env.DATABASE_URL || sqliteDatabaseUrl(dbPath);
  env.JARVIS_DATA_DIR = dataDir;
  env.JARVIS_MEDIA_ROOT = mediaDir;
  if (DEFAULT_SYNC_API_KEY && !env.SYNC_API_KEY) {
    env.SYNC_API_KEY = DEFAULT_SYNC_API_KEY;
  }
  return env;
}

function screenpipeDataDir() {
  const dir = path.join(app.getPath("userData"), "screenpipe-data");
  ensureDir(dir);
  return dir;
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

  // On macOS, clear Gatekeeper quarantine flag before running
  clearMacOsQuarantine(screenpipeBin);
  ensureScreenpipeCacheDirs();

  const screenpipeCwd = path.dirname(screenpipeBin);
  ensureBundledFfmpeg(screenpipeCwd);
  const dataDir = screenpipeDataDir();
  
  appendLog("runtime.log", `[runtime] screenpipe binary=${screenpipeBin}`);
  appendLog("runtime.log", `[runtime] screenpipe cwd=${screenpipeCwd}`);
  appendLog("runtime.log", `[runtime] screenpipe dataDir=${dataDir}`);
  
  // List files in screenpipe directory to verify DLLs are present
  try {
    const files = fs.readdirSync(screenpipeCwd);
    appendLog("runtime.log", `[runtime] screenpipe dir contents: ${files.join(", ")}`);
  } catch (err) {
    appendLog("runtime.log", `[runtime] failed to list screenpipe dir: ${err.message}`);
  }

  // Build args - screenpipe CLI uses "record" subcommand with options
  const args = [
    "record",
    "--data-dir", dataDir,
    "--port", "3030"
  ];
  
  appendLog("runtime.log", `[runtime] screenpipe command: ${screenpipeBin} ${args.join(" ")}`);

  screenpipeProcess = spawnManagedProcess("screenpipe", screenpipeBin, args, {
    env: buildScreenpipeEnv(screenpipeCwd),
    cwd: screenpipeCwd
  });
  screenpipeProcess.stdout?.on("data", (chunk) =>
    appendLog("screenpipe.log", chunk.toString().trimEnd())
  );
  screenpipeProcess.stderr?.on("data", (chunk) =>
    appendLog("screenpipe.log", chunk.toString().trimEnd())
  );
  screenpipeProcess.on("exit", (code, signal) => {
    appendLog("runtime.log", `[runtime] screenpipe exited code=${code} signal=${signal || ""}`);
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
  const cwd = clientDataDir();
  backendProcess = spawnManagedProcess("backend", backendBin, [], { env, cwd });
  backendProcess.stdout?.on("data", (chunk) =>
    appendLog("backend.log", chunk.toString().trimEnd())
  );
  backendProcess.stderr?.on("data", (chunk) =>
    appendLog("backend.log", chunk.toString().trimEnd())
  );
  backendProcess.on("exit", (code, signal) => {
    appendLog("runtime.log", `[runtime] backend exited code=${code} signal=${signal || ""}`);
    backendProcess = null;
  });
}

function showRuntimeError(message) {
  appendLog("runtime.log", `[runtime] error: ${message}`);
  dialog.showErrorBox("Jarvis could not start", `${message}\n\nLogs: ${logsDir()}`);
}

function logRuntimeInventory() {
  const root = runtimeResourceRoot();
  const platformDir = path.join(root, platformResourceSubdir());
  appendLog("runtime.log", `[runtime] platform=${process.platform} arch=${process.arch}`);
  appendLog("runtime.log", `[runtime] resourceRoot=${root}`);
  appendLog("runtime.log", `[runtime] platformDir=${platformDir}`);
  if (fs.existsSync(platformDir)) {
    appendLog("runtime.log", `[runtime] platform files: ${fs.readdirSync(platformDir).join(", ")}`);
  } else {
    appendLog("runtime.log", "[runtime] platformDir missing");
  }
}

async function startRuntime() {
  if (!app.isPackaged && process.env.JARVIS_LAUNCH_RUNTIME !== "1") {
    return;
  }

  appendLog("runtime.log", "[runtime] starting managed runtime processes");
  logRuntimeInventory();

  const backendBin = resolveBinaryPath("backend");
  const screenpipeBin = resolveBinaryPath("screenpipe");
  if (!backendBin) {
    showRuntimeError(
      "The local backend is missing from this installer. Download a fresh build from GitHub Actions after the latest CI run passes."
    );
    throw new Error("backend binary missing");
  }
  if (!screenpipeBin) {
    showRuntimeError(
      "Screenpipe is missing from this installer. Download a fresh build from GitHub Actions after the latest CI run passes."
    );
    throw new Error("screenpipe binary missing");
  }

  appendLog("runtime.log", `[runtime] backendBin=${backendBin}`);
  appendLog("runtime.log", `[runtime] screenpipeBin=${screenpipeBin}`);

  appendLog("runtime.log", "[runtime] launching screenpipe...");
  startScreenpipe();
  
  // First macOS launch may download ffmpeg + models — allow extra startup time.
  const warmupMs = process.platform === "darwin" ? 5000 : 2000;
  await new Promise(resolve => setTimeout(resolve, warmupMs));
  
  try {
    appendLog("runtime.log", `[runtime] waiting for screenpipe health check (timeout ${SCREENPIPE_WAIT_MS}ms)...`);
    await waitForUrl(SCREENPIPE_HEALTH_URL, SCREENPIPE_WAIT_MS, "Screenpipe", {
      acceptAnyHttpStatus: true
    });
    appendLog("runtime.log", "[runtime] screenpipe health check passed (port 3030)");
  } catch (error) {
    appendLog("runtime.log", `[runtime] screenpipe health check failed: ${error.message}`);
    showRuntimeError(
      `Screenpipe did not start on port 3030.\n\n${error.message}\n\nCheck screenpipe.log for details.`
    );
    throw error;
  }

  startBackend();
  try {
    await waitForUrl(HEALTH_URL, BACKEND_WAIT_MS, "Backend");
    appendLog("runtime.log", "[runtime] backend health check passed (port 8000)");
  } catch (error) {
    showRuntimeError(
      `Backend did not start on port 8000.\n\n${error.message}\n\nCheck backend.log for details.`
    );
    throw error;
  }
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
