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

function copyIfPresent(srcPath, destPath) {
  if (!srcPath || !fs.existsSync(srcPath)) {
    return false;
  }
  ensureDir(path.dirname(destPath));
  fs.copyFileSync(srcPath, destPath);
  return true;
}

function copyDirIfPresent(srcDir, destDir) {
  if (!srcDir || !fs.existsSync(srcDir)) {
    return false;
  }
  ensureDir(destDir);
  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    const src = path.join(srcDir, entry.name);
    const dest = path.join(destDir, entry.name);
    if (entry.isDirectory()) {
      copyDirIfPresent(src, dest);
    } else {
      fs.copyFileSync(src, dest);
    }
  }
  return true;
}

function writePlaceholderIfMissing(destPath, message) {
  if (fs.existsSync(destPath)) {
    return;
  }
  ensureDir(path.dirname(destPath));
  fs.writeFileSync(destPath, `${message}\n`, "utf8");
}

const folder = platformFolder(target);
const platformRoot = path.join(resourcesRoot, folder);
const commonRoot = path.join(resourcesRoot, "common");
ensureDir(platformRoot);
ensureDir(commonRoot);

const isWindows = folder === "win";
const backendDest = path.join(platformRoot, `backend${isWindows ? ".exe" : ""}`);
const screenpipeDest = path.join(platformRoot, `screenpipe${isWindows ? ".exe" : ""}`);
const screenpipeLibDir = path.join(platformRoot, "screenpipe-lib");

const backendCopied = copyIfPresent(process.env.JARVIS_BACKEND_BIN_SRC, backendDest);

let screenpipeCopied = false;
if (process.env.JARVIS_SCREENPIPE_BIN_DIR_SRC) {
  screenpipeCopied = copyDirIfPresent(process.env.JARVIS_SCREENPIPE_BIN_DIR_SRC, screenpipeLibDir);
  if (screenpipeCopied) {
    const exeName = isWindows ? "screenpipe.exe" : "screenpipe";
    const stagedExe = path.join(screenpipeLibDir, exeName);
    if (fs.existsSync(stagedExe)) {
      copyIfPresent(stagedExe, screenpipeDest);
    }
  }
} else {
  screenpipeCopied = copyIfPresent(process.env.JARVIS_SCREENPIPE_BIN_SRC, screenpipeDest);
}

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
  `[stage-runtime] target=${target} folder=${folder} backendCopied=${backendCopied} screenpipeCopied=${screenpipeCopied}`
);

if (process.env.REQUIRE_RUNTIME_BINARIES === "1" && (!backendCopied || !screenpipeCopied)) {
  process.exit(1);
}
