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

const backendSrc = firstExistingPath([
  process.env.JARVIS_BACKEND_BIN_SRC,
  path.join(projectRoot, "backend-src", "dist", isWindows ? "backend.exe" : "backend")
]);
const backendCopied = copyIfPresent(backendSrc, backendDest);

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
  `[stage-runtime] target=${target} folder=${folder} backendSrc=${backendSrc || "missing"} backendCopied=${backendCopied} screenpipeCopied=${screenpipeCopied}`
);

if (process.env.REQUIRE_RUNTIME_BINARIES === "1" && (!backendCopied || !screenpipeCopied)) {
  process.exit(1);
}
