#!/usr/bin/env node
/**
 * CI helper: download @screenpipe/cli-* platform packages via npm pack (no EBADPLATFORM)
 * and write JARVIS_SCREENPIPE_* paths to GITHUB_ENV.
 */
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const projectRoot = path.join(__dirname, "..");
const runtimeTarget = process.env.RUNTIME_TARGET || process.platform;
const githubEnv = process.env.GITHUB_ENV;

const PACKAGES = {
  windows: ["@screenpipe/cli-win32-x64"],
  darwin: ["@screenpipe/cli-darwin-arm64", "@screenpipe/cli-darwin-x64"],
  linux: ["@screenpipe/cli-linux-x64"]
};

const BIN_NAME = runtimeTarget === "windows" ? "screenpipe.exe" : "screenpipe";

function log(msg) {
  console.log(`[install-screenpipe] ${msg}`);
}

function appendGithubEnv(key, value) {
  if (!githubEnv) {
    log(`(no GITHUB_ENV) ${key}=${value}`);
    return;
  }
  fs.appendFileSync(githubEnv, `${key}=${value}\n`);
}

function npmPackExtract(pkgName, destDir) {
  const workDir = path.join(projectRoot, ".ci-tmp", pkgName.replace(/[@/]/g, "_"));
  fs.mkdirSync(workDir, { recursive: true });

  log(`packing ${pkgName}...`);
  const tgzName = execSync(`npm pack "${pkgName}"`, {
    cwd: workDir,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"]
  }).trim();

  const tgzPath = path.join(workDir, tgzName);
  if (!fs.existsSync(tgzPath)) {
    throw new Error(`npm pack did not create ${tgzPath}`);
  }

  fs.mkdirSync(destDir, { recursive: true });
  execSync(`tar -xzf "${tgzPath}" -C "${workDir}"`, { stdio: "inherit" });

  const extractedBin = path.join(workDir, "package", "bin");
  if (!fs.existsSync(extractedBin)) {
    throw new Error(`no bin/ in packed ${pkgName} (looked at ${extractedBin})`);
  }

  for (const entry of fs.readdirSync(extractedBin)) {
    const src = path.join(extractedBin, entry);
    const dest = path.join(destDir, entry);
    fs.copyFileSync(src, dest);
    if (BIN_NAME === "screenpipe" && !entry.endsWith(".exe")) {
      try {
        fs.chmodSync(dest, 0o755);
      } catch (_err) {
        // Windows CI may not support chmod on all files.
      }
    }
  }

  const binaryPath = path.join(destDir, BIN_NAME);
  if (!fs.existsSync(binaryPath)) {
    throw new Error(`binary missing after extract: ${binaryPath}`);
  }

  log(`extracted ${pkgName} → ${destDir} (${fs.readdirSync(destDir).join(", ")})`);
  return path.resolve(destDir);
}

function findBinDir(rootDir) {
  const binaryPath = path.join(rootDir, BIN_NAME);
  if (fs.existsSync(binaryPath)) {
    return path.resolve(rootDir);
  }
  return null;
}

function main() {
  const packages = PACKAGES[runtimeTarget];
  if (!packages) {
    throw new Error(`unsupported RUNTIME_TARGET: ${runtimeTarget}`);
  }

  const extractRoot = path.join(projectRoot, ".ci-screenpipe");
  fs.mkdirSync(extractRoot, { recursive: true });

  const extracted = {};

  for (const pkg of packages) {
    const shortName = pkg.split("/").pop();
    const destDir = path.join(extractRoot, shortName);
    try {
      extracted[shortName] = npmPackExtract(pkg, destDir);
    } catch (err) {
      log(`WARN: failed to pack ${pkg}: ${err.message}`);
    }
  }

  const arm64Dir = extracted["cli-darwin-arm64"] || findBinDir(path.join(extractRoot, "cli-darwin-arm64"));
  const x64Dir = extracted["cli-darwin-x64"] || findBinDir(path.join(extractRoot, "cli-darwin-x64"));
  const winDir = extracted["cli-win32-x64"] || findBinDir(path.join(extractRoot, "cli-win32-x64"));
  const linuxDir = extracted["cli-linux-x64"] || findBinDir(path.join(extractRoot, "cli-linux-x64"));

  let primaryDir = null;
  if (runtimeTarget === "darwin") {
    primaryDir = arm64Dir || x64Dir;
    if (!primaryDir) {
      throw new Error("no macOS screenpipe binary extracted (need cli-darwin-arm64 or cli-darwin-x64)");
    }
    if (arm64Dir) {
      appendGithubEnv("JARVIS_SCREENPIPE_ARM64_DIR_SRC", arm64Dir);
    }
    if (x64Dir) {
      appendGithubEnv("JARVIS_SCREENPIPE_X64_DIR_SRC", x64Dir);
    }
  } else if (runtimeTarget === "windows") {
    primaryDir = winDir;
    if (!primaryDir) {
      throw new Error("no Windows screenpipe binary extracted");
    }
  } else if (runtimeTarget === "linux") {
    primaryDir = linuxDir;
    if (!primaryDir) {
      throw new Error("no Linux screenpipe binary extracted");
    }
  }

  appendGithubEnv("JARVIS_SCREENPIPE_BIN_DIR_SRC", primaryDir);
  appendGithubEnv(
    "JARVIS_SCREENPIPE_BIN_SRC",
    path.join(primaryDir, BIN_NAME)
  );

  log(`primary=${primaryDir}`);
  log("done");
}

main();
