#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

const projectRoot = path.join(__dirname, "..");
const resourcesRoot = path.join(projectRoot, "resources");
const target = process.env.RUNTIME_TARGET || process.platform;

function platformFolder(targetPlatform) {
  if (targetPlatform === "darwin" || targetPlatform === "mac") {
    return "mac";
  }
  if (targetPlatform === "win32" || targetPlatform === "windows") {
    return "win";
  }
  if (targetPlatform === "linux") {
    return "linux";
  }
  return "common";
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function resolveExistingPath(candidate) {
  if (!candidate) {
    return null;
  }
  const resolved = path.resolve(candidate);
  return fs.existsSync(resolved) ? resolved : null;
}

function firstExistingPath(candidates) {
  for (const candidate of candidates) {
    const resolved = resolveExistingPath(candidate);
    if (resolved) {
      return resolved;
    }
  }
  return null;
}

function copyIfPresent(srcPath, destPath) {
  const resolvedSrc = resolveExistingPath(srcPath);
  if (!resolvedSrc) {
    return false;
  }
  ensureDir(path.dirname(destPath));
  fs.copyFileSync(resolvedSrc, destPath);
  return true;
}

function copyDirIfPresent(srcDir, destDir) {
  const resolvedSrc = resolveExistingPath(srcDir);
  if (!resolvedSrc || !fs.statSync(resolvedSrc).isDirectory()) {
    return false;
  }
  ensureDir(destDir);
  for (const entry of fs.readdirSync(resolvedSrc, { withFileTypes: true })) {
    const src = path.join(resolvedSrc, entry.name);
    const dest = path.join(destDir, entry.name);
    if (entry.isDirectory()) {
      copyDirIfPresent(src, dest);
    } else {
      fs.copyFileSync(src, dest);
    }
  }
  return true;
}

function makeExecutable(filePath) {
  if (!filePath || !fs.existsSync(filePath)) {
    return;
  }
  if (process.platform === "win32") {
    return;
  }
  try {
    const mode = fs.statSync(filePath).mode;
    fs.chmodSync(filePath, mode | 0o755);
  } catch (error) {
    console.warn(`[stage-runtime] chmod failed for ${filePath}: ${error.message}`);
  }
}

function clearQuarantineFlag(filePath) {
  if (process.platform !== "darwin") {
    return;
  }
  try {
    const { execSync } = require("child_process");
    execSync(`xattr -d com.apple.quarantine "${filePath}" 2>/dev/null || true`, {
      stdio: "ignore"
    });
  } catch (error) {
    console.warn(`[stage-runtime] failed to clear quarantine on ${filePath}: ${error.message}`);
  }
}

function makeTreeExecutable(dirPath) {
  if (!dirPath || !fs.existsSync(dirPath)) {
    return;
  }
  for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      makeTreeExecutable(fullPath);
    } else {
      makeExecutable(fullPath);
      clearQuarantineFlag(fullPath);
    }
  }
}

function writePlaceholderIfMissing(destPath, message) {
  if (fs.existsSync(destPath)) {
    return;
  }
  ensureDir(path.dirname(destPath));
  fs.writeFileSync(destPath, `${message}\n`, "utf8");
}

function screenpipeExeName(isWindows) {
  return isWindows ? "screenpipe.exe" : "screenpipe";
}

const folder = platformFolder(target);
const platformRoot = path.join(resourcesRoot, folder);
const commonRoot = path.join(resourcesRoot, "common");
ensureDir(platformRoot);
ensureDir(commonRoot);

const isWindows = folder === "win";
const backendDest = path.join(platformRoot, `backend${isWindows ? ".exe" : ""}`);
const screenpipeLibDir = path.join(platformRoot, "screenpipe-lib");
const screenpipeExe = path.join(screenpipeLibDir, screenpipeExeName(isWindows));

function hasScreenpipeBinary() {
  const dirs = [
    screenpipeLibDir,
    path.join(platformRoot, "screenpipe-lib-arm64"),
    path.join(platformRoot, "screenpipe-lib-x64")
  ];
  const exeName = screenpipeExeName(isWindows);
  return dirs.some((dir) => fs.existsSync(path.join(dir, exeName)));
}

const backendSrc = firstExistingPath([
  process.env.JARVIS_BACKEND_BIN_SRC,
  path.join(projectRoot, "backend-src", "dist", isWindows ? "backend.exe" : "backend")
]);
const backendCopied = copyIfPresent(backendSrc, backendDest);
if (backendCopied) {
  makeExecutable(backendDest);
  clearQuarantineFlag(backendDest);
}

let screenpipeCopied = false;
const screenpipeSrcCandidates = [
  process.env.JARVIS_SCREENPIPE_BIN_DIR_SRC,
  process.env.JARVIS_SCREENPIPE_ARM64_DIR_SRC,
  process.env.JARVIS_SCREENPIPE_X64_DIR_SRC,
  path.join(projectRoot, ".ci-screenpipe", "cli-win32-x64"),
  path.join(projectRoot, ".ci-screenpipe", "cli-darwin-arm64"),
  path.join(projectRoot, ".ci-screenpipe", "cli-darwin-x64"),
  path.join(projectRoot, ".ci-screenpipe", "cli-linux-x64"),
  process.env.JARVIS_SCREENPIPE_BIN_SRC
    ? path.dirname(process.env.JARVIS_SCREENPIPE_BIN_SRC)
    : null
].filter(Boolean);

for (const src of screenpipeSrcCandidates) {
  if (copyDirIfPresent(src, screenpipeLibDir)) {
    screenpipeCopied = true;
    console.log(`[stage-runtime] screenpipe copied from ${src}`);
    break;
  }
}

if (folder === "mac") {
  const arm64Dir = path.join(platformRoot, "screenpipe-lib-arm64");
  const x64Dir = path.join(platformRoot, "screenpipe-lib-x64");
  if (process.env.JARVIS_SCREENPIPE_ARM64_DIR_SRC) {
    copyDirIfPresent(process.env.JARVIS_SCREENPIPE_ARM64_DIR_SRC, arm64Dir);
    if (!screenpipeCopied) {
      screenpipeCopied = copyDirIfPresent(process.env.JARVIS_SCREENPIPE_ARM64_DIR_SRC, screenpipeLibDir);
    }
  }
  if (process.env.JARVIS_SCREENPIPE_X64_DIR_SRC) {
    copyDirIfPresent(process.env.JARVIS_SCREENPIPE_X64_DIR_SRC, x64Dir);
  }
  makeTreeExecutable(arm64Dir);
  makeTreeExecutable(x64Dir);
}

if (screenpipeCopied) {
  makeTreeExecutable(screenpipeLibDir);
}
screenpipeCopied = hasScreenpipeBinary();

if (!backendCopied) {
  writePlaceholderIfMissing(
    path.join(platformRoot, "backend.MISSING.txt"),
    "Backend binary missing. CI must build backend-client.spec before packaging."
  );
}

if (!screenpipeCopied) {
  writePlaceholderIfMissing(
    path.join(platformRoot, "screenpipe.MISSING.txt"),
    "Screenpipe binary missing. CI must install @screenpipe/cli-* before packaging."
  );
}

console.log(
  `[stage-runtime] target=${target} folder=${folder} backendSrc=${backendSrc || "missing"} backendCopied=${backendCopied} screenpipeCopied=${screenpipeCopied} screenpipeExe=${screenpipeCopied ? screenpipeExe : "missing"}`
);

// List what was staged for screenpipe (helps debug DLL issues)
if (fs.existsSync(screenpipeLibDir)) {
  const stagedFiles = fs.readdirSync(screenpipeLibDir);
  console.log(`[stage-runtime] screenpipe-lib contents (${stagedFiles.length} files): ${stagedFiles.join(", ")}`);
} else {
  console.log("[stage-runtime] screenpipe-lib directory does not exist");
}

if (process.env.REQUIRE_RUNTIME_BINARIES === "1" && (!backendCopied || !screenpipeCopied)) {
  console.error("[stage-runtime] ERROR: Required binaries missing, failing build");
  process.exit(1);
}
