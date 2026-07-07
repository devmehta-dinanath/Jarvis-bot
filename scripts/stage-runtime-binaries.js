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
  if (!srcPath) {
    return false;
  }
  if (!fs.existsSync(srcPath)) {
    return false;
  }
  ensureDir(path.dirname(destPath));
  fs.copyFileSync(srcPath, destPath);
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

const backendCopied = copyIfPresent(process.env.JARVIS_BACKEND_BIN_SRC, backendDest);
const screenpipeCopied = copyIfPresent(process.env.JARVIS_SCREENPIPE_BIN_SRC, screenpipeDest);

if (!backendCopied) {
  writePlaceholderIfMissing(
    path.join(platformRoot, "backend.MISSING.txt"),
    "Backend binary missing. Set JARVIS_BACKEND_BIN_SRC in CI before packaging."
  );
}

if (!screenpipeCopied) {
  writePlaceholderIfMissing(
    path.join(platformRoot, "screenpipe.MISSING.txt"),
    "Screenpipe binary missing. Set JARVIS_SCREENPIPE_BIN_SRC in CI before packaging."
  );
}

console.log(
  `[stage-runtime] target=${target} folder=${folder} backendCopied=${backendCopied} screenpipeCopied=${screenpipeCopied}`
);
