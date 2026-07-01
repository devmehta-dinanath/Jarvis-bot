#!/usr/bin/env bash
# Desktop client setup — clones backend + frontend, runs Screenpipe + OCR in Docker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DEFAULT_REPO_URL="https://github.com/devmehta-dinanath/Jarvis-bot.git"
BACKEND_BRANCH="backend"
FRONTEND_BRANCH="frontend"

CLONE_URL="${JARVIS_REPO_URL:-$DEFAULT_REPO_URL}"
SERVER_URL=""
SYNC_KEY=""
INSTALL_DIR=""
BACKEND_DIR=""
FRONTEND_DIR=""
OS=""
START_FRONTEND=true
LOCAL_ONLY=false
CLONE_ONLY=false

log() { echo "[desktop-setup] $*"; }
die() { echo "[desktop-setup] ERROR: $*" >&2; exit 1; }
warn() { echo "[desktop-setup] WARN: $*" >&2; }

STEP=0
log_step() {
  STEP=$((STEP + 1))
  echo ""
  echo "[desktop-setup] ══ Step ${STEP}: $* ══"
}

log_detail() {
  echo "[desktop-setup]    $*"
}

log_ok() {
  echo "[desktop-setup] ✓ $*"
}

env_file_get() {
  local file="$1" key="$2"
  grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- \
    | sed 's/^["'\'' ]*//;s/["'\'' ]*$//'
}

env_file_set() {
  local file="$1" key="$2" value="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >>"$file"
  fi
}

ensure_env_file() {
  local dest="$1" example="$2" label="$3"
  if [ -f "$dest" ]; then
    return 0
  fi
  if [ ! -f "$example" ]; then
    die "Missing $label .env — neither $(basename "$dest") nor $(basename "$example") found"
  fi
  cp "$example" "$dest"
  log "Created $label .env from $(basename "$example")"
}

is_placeholder_value() {
  local value="${1:-}"
  case "$value" in
    ""|*change-me*|*YOUR_SERVER_IP*|*your-token-here*|*example.internal*)
      return 0
      ;;
  esac
  return 1
}

load_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  set -a
  # shellcheck disable=SC1090
  . "$file"
  set +a
}

resolve_backend_env_example() {
  if [ -f "$BACKEND_DIR/.env.client.example" ]; then
    printf '%s\n' "$BACKEND_DIR/.env.client.example"
  elif [ -f "$BACKEND_DIR/.env.example" ]; then
    printf '%s\n' "$BACKEND_DIR/.env.example"
  else
    printf '%s\n' ""
  fi
}

usage() {
  cat <<EOF
Usage: $0 [options]

Clones Jarvis backend + frontend, then starts Screenpipe capture in Docker (Linux desktop).

Options:
  --local-only        Run locally without a central server (no server health check, sync off)
  --clone-only        Only clone repos + setup .env files, then exit (good for first test)
  --server-url URL    Central server URL (required unless --local-only or --clone-only)
  --clone GIT_URL     Repo to clone (default: $DEFAULT_REPO_URL)
  --install-dir DIR   Install root (default: OS-specific, see below)
  --sync-key KEY      SYNC_API_KEY from server .env
  --no-frontend       Skip auto-starting the Electron UI

Examples:
  # Test clone + env only (no Docker, no server):
  $0 --clone-only --install-dir ~/jarvis-bot-test

  # Full local capture without central server yet:
  $0 --local-only

  # Production desktop client (server must be running):
  $0 --server-url http://192.168.1.50:8000

Branches cloned:
  backend  →  <install-dir>/backend   (Screenpipe + OCR Docker stack)
  frontend →  <install-dir>/frontend  (Electron UI)

Default install dir:
  Linux/macOS:  ~/jarvis-bot
  Windows:      \$USERPROFILE/jarvis-bot  (Git Bash / WSL)

Stop:  docker compose -f docker-compose.client.yml down
Logs:  docker compose -f docker-compose.client.yml logs -f
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --server-url) SERVER_URL="${2%/}"; shift 2 ;;
    --clone) CLONE_URL="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --sync-key) SYNC_KEY="$2"; shift 2 ;;
    --no-frontend) START_FRONTEND=false; shift ;;
    --local-only) LOCAL_ONLY=true; shift ;;
    --clone-only) CLONE_ONLY=true; LOCAL_ONLY=true; START_FRONTEND=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

detect_os() {
  case "$(uname -s)" in
    Linux) OS="linux" ;;
    Darwin) OS="macos" ;;
    MINGW*|MSYS*|CYGWIN*) OS="windows" ;;
    *) OS="unknown" ;;
  esac
  log "Detected OS: $OS ($(uname -s) $(uname -m))"
}

default_install_dir() {
  case "$OS" in
    windows)
      if [ -n "${USERPROFILE:-}" ]; then
        printf '%s/jarvis-bot' "${USERPROFILE//\\//}"
      else
        printf '%s/jarvis-bot' "$HOME"
      fi
      ;;
    *)
      printf '%s/jarvis-bot' "$HOME"
      ;;
  esac
}

resolve_dirs() {
  detect_os

  local explicit_install=false
  if [ -n "$INSTALL_DIR" ]; then
    explicit_install=true
  else
    INSTALL_DIR="${JARVIS_INSTALL_DIR:-$(default_install_dir)}"
  fi

  log_detail "Install root: $INSTALL_DIR"
  log_detail "Clone URL:    $CLONE_URL"
  log_detail "Branches:     $BACKEND_BRANCH (backend), $FRONTEND_BRANCH (frontend)"

  # Developer mode: script lives inside an existing backend checkout.
  if [ "$explicit_install" = false ] && [ -f "$REPO_DIR/docker-compose.client.yml" ]; then
    BACKEND_DIR="$REPO_DIR"
    FRONTEND_DIR="$(dirname "$REPO_DIR")/jarvis-bot-fe"
    if [ ! -d "$FRONTEND_DIR" ]; then
      FRONTEND_DIR="$INSTALL_DIR/frontend"
    fi
    log_ok "Using local backend checkout (pass --install-dir to clone elsewhere)"
    log_detail "Backend:  $BACKEND_DIR"
    log_detail "Frontend: $FRONTEND_DIR"
    return
  fi

  BACKEND_DIR="$INSTALL_DIR/backend"
  FRONTEND_DIR="$INSTALL_DIR/frontend"
  log_detail "Backend:  $BACKEND_DIR"
  log_detail "Frontend: $FRONTEND_DIR"
}

log_repo_info() {
  local dest="$1" label="$2"

  if [ ! -d "$dest/.git" ]; then
    warn "$label repo not found at $dest"
    return 1
  fi

  local branch commit remote
  branch="$(git -C "$dest" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  commit="$(git -C "$dest" rev-parse --short HEAD 2>/dev/null || echo '?')"
  remote="$(git -C "$dest" remote get-url origin 2>/dev/null || echo '?')"

  log_ok "$label cloned/updated"
  log_detail "path:    $dest"
  log_detail "branch:  $branch"
  log_detail "commit:  $commit"
  log_detail "remote:  $remote"

  if [ -f "$dest/docker-compose.client.yml" ]; then
    log_detail "files:   docker-compose.client.yml ✓"
  elif [ -f "$dest/package.json" ]; then
    log_detail "files:   package.json ✓"
  elif [ -f "$dest/app/main.py" ]; then
    log_detail "files:   app/main.py ✓"
  fi
  return 0
}

clone_branch() {
  local branch="$1"
  local dest="$2"

  if [ -d "$dest/.git" ]; then
    log_detail "Repo exists — fetching latest for branch '$branch' ..."
    git -C "$dest" fetch origin "$branch" 2>&1 | while IFS= read -r line; do log_detail "$line"; done || true
    git -C "$dest" checkout "$branch" 2>&1 | while IFS= read -r line; do log_detail "$line"; done
    if git -C "$dest" pull --ff-only origin "$branch" 2>&1 | while IFS= read -r line; do log_detail "$line"; done; then
      :
    elif git -C "$dest" pull --ff-only 2>&1 | while IFS= read -r line; do log_detail "$line"; done; then
      :
    else
      warn "Could not fast-forward $dest — using existing checkout"
    fi
    return 0
  fi

  if [ -d "$dest" ] && [ "$(ls -A "$dest" 2>/dev/null | wc -l)" -gt 0 ]; then
    die "$dest exists but is not a git repo. Remove it or pick another --install-dir"
  fi

  mkdir -p "$(dirname "$dest")"
  log_detail "git clone --branch $branch --single-branch $CLONE_URL $dest"
  if ! git clone --branch "$branch" --single-branch --progress "$CLONE_URL" "$dest"; then
    die "git clone failed for branch '$branch' → $dest"
  fi
  return 0
}

clone_repos_if_needed() {
  local has_backend=false
  if [ -f "$BACKEND_DIR/docker-compose.client.yml" ] || [ -f "$BACKEND_DIR/app/main.py" ]; then
    has_backend=true
  fi

  if [ "$has_backend" = true ] && [ -f "$FRONTEND_DIR/package.json" ]; then
    log_detail "Backend + frontend already present — skipping clone"
    log_repo_info "$BACKEND_DIR" "backend" || true
    if [ -f "$FRONTEND_DIR/package.json" ]; then
      log_repo_info "$FRONTEND_DIR" "frontend" || true
    else
      warn "Frontend not found at $FRONTEND_DIR"
    fi
    return 0
  fi

  if [ -f "$BACKEND_DIR/docker-compose.client.yml" ]; then
    log_detail "Backend present, cloning frontend only ..."
    log_repo_info "$BACKEND_DIR" "backend" || true
    clone_branch "$FRONTEND_BRANCH" "$FRONTEND_DIR"
    log_repo_info "$FRONTEND_DIR" "frontend"
    return 0
  fi

  mkdir -p "$INSTALL_DIR"
  log_detail "Cloning backend branch '$BACKEND_BRANCH' ..."
  clone_branch "$BACKEND_BRANCH" "$BACKEND_DIR"
  log_repo_info "$BACKEND_DIR" "backend"

  log_detail "Cloning frontend branch '$FRONTEND_BRANCH' ..."
  clone_branch "$FRONTEND_BRANCH" "$FRONTEND_DIR"
  log_repo_info "$FRONTEND_DIR" "frontend"
}

install_frontend_deps() {
  if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    warn "No frontend at $FRONTEND_DIR — skipping npm install"
    return
  fi
  if ! command -v npm >/dev/null 2>&1; then
    warn "npm not found — skip frontend install (run: cd $FRONTEND_DIR && npm install)"
    return
  fi
  log_detail "Running npm install in $FRONTEND_DIR ..."
  if (cd "$FRONTEND_DIR" && npm install --no-fund --no-audit); then
    log_ok "Frontend dependencies installed"
  else
    warn "npm install failed — check Node.js/npm and retry"
  fi
}

setup_frontend_env() {
  local env_file="$FRONTEND_DIR/.env"
  local example_file="$FRONTEND_DIR/.env.example"
  local api_url="http://127.0.0.1:8000"

  if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    warn "Frontend package.json not found at $FRONTEND_DIR — skipping frontend .env"
    return 0
  fi

  ensure_env_file "$env_file" "$example_file" "frontend"
  env_file_set "$env_file" "JARVIS_API_URL" "$api_url"
  if is_placeholder_value "$(env_file_get "$env_file" APP_ENV)"; then
    env_file_set "$env_file" "APP_ENV" "development"
  fi

  log_ok "Frontend .env ready"
  log_detail "JARVIS_API_URL=$api_url"
  return 0
}

check_frontend_env() {
  local env_file="$FRONTEND_DIR/.env"
  if [ ! -f "$env_file" ]; then
    if [ "$START_FRONTEND" = true ]; then
      die "Frontend .env missing at $env_file"
    fi
    return 0
  fi

  local api_url
  api_url="$(env_file_get "$env_file" JARVIS_API_URL)"
  if is_placeholder_value "$api_url"; then
    die "Frontend JARVIS_API_URL is missing or still a placeholder in $env_file"
  fi
  log_ok "Frontend .env validated (JARVIS_API_URL=$api_url)"
}

setup_backend_env() {
  local env_file="$BACKEND_DIR/.env"
  local example_file
  example_file="$(resolve_backend_env_example)"
  [ -n "$example_file" ] || die "No backend env example found (.env.client.example or .env.example) in $BACKEND_DIR"

  ensure_env_file "$env_file" "$example_file" "backend"
  env_file_set "$env_file" "APP_ROLE" "client"
  env_file_set "$env_file" "SCREENPIPE_ENABLED" "true"

  if [ "$LOCAL_ONLY" = true ]; then
    SERVER_URL="${SERVER_URL:-http://127.0.0.1:8000}"
    env_file_set "$env_file" "JARVIS_SERVER_URL" "$SERVER_URL"
    env_file_set "$env_file" "SYNC_ENABLED" "false"
    env_file_set "$env_file" "SYNC_API_KEY" "local-dev-not-syncing"
    log_ok "Backend .env ready (local mode — central server sync disabled)"
    log_detail "JARVIS_SERVER_URL=$SERVER_URL"
    log_detail "SYNC_ENABLED=false"
    return 0
  fi

  env_file_set "$env_file" "JARVIS_SERVER_URL" "$SERVER_URL"
  env_file_set "$env_file" "SYNC_ENABLED" "true"

  if [ -n "$SYNC_KEY" ]; then
    env_file_set "$env_file" "SYNC_API_KEY" "$SYNC_KEY"
  elif is_placeholder_value "$(env_file_get "$env_file" SYNC_API_KEY)"; then
    read -r -p "Enter SYNC_API_KEY (from server .env): " SYNC_KEY
    [ -n "$SYNC_KEY" ] || die "SYNC_API_KEY is required"
    env_file_set "$env_file" "SYNC_API_KEY" "$SYNC_KEY"
  fi

  log_ok "Backend .env ready"
  log_detail "JARVIS_SERVER_URL=$SERVER_URL"
}

check_backend_env() {
  local env_file="$BACKEND_DIR/.env"
  [ -f "$env_file" ] || die "Backend .env missing at $env_file"

  if [ "$LOCAL_ONLY" = true ]; then
    log_ok "Backend .env validated (local mode)"
    return 0
  fi

  local server_url sync_key
  server_url="$(env_file_get "$env_file" JARVIS_SERVER_URL)"
  sync_key="$(env_file_get "$env_file" SYNC_API_KEY)"

  if is_placeholder_value "$server_url"; then
    die "Backend JARVIS_SERVER_URL is missing or still a placeholder in $env_file"
  fi
  if is_placeholder_value "$sync_key"; then
    die "Backend SYNC_API_KEY is missing or still a placeholder in $env_file"
  fi

  log_ok "Backend .env validated (JARVIS_SERVER_URL=$server_url, SYNC_API_KEY=set)"
}

check_backend_screenpipe_env() {
  local env_file="$BACKEND_DIR/.env"
  local token
  token="$(env_file_get "$env_file" SCREENPIPE_API_TOKEN)"
  if is_placeholder_value "$token"; then
    warn "SCREENPIPE_API_TOKEN not set — capture may fail until token is fetched"
    return 1
  fi
  log_ok "Backend .env OK (SCREENPIPE_API_TOKEN=set)"
  return 0
}

start_frontend() {
  if [ "$START_FRONTEND" != true ]; then
    log_detail "Frontend auto-start disabled (--no-frontend)"
    return 0
  fi
  if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    warn "Frontend not found at $FRONTEND_DIR — skipping UI launch"
    return 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    warn "npm not found — cannot start frontend"
    return 1
  fi

  local electron_bin="$FRONTEND_DIR/node_modules/.bin/electron"
  if [ ! -x "$electron_bin" ]; then
    log_detail "Electron missing — running npm install in $FRONTEND_DIR ..."
    if ! (cd "$FRONTEND_DIR" && npm install --no-fund --no-audit); then
      warn "npm install failed — cannot start frontend"
      return 1
    fi
  fi
  if [ ! -x "$electron_bin" ]; then
    warn "Electron binary not found at $electron_bin"
    return 1
  fi

  local log_file="$BACKEND_DIR/data/frontend.log"
  local pid_file="$BACKEND_DIR/data/.jarvis-frontend.pid"
  mkdir -p "$BACKEND_DIR/data"

  if [ -f "$pid_file" ]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      log_ok "Frontend already running (pid $old_pid)"
      return 0
    fi
  fi

  export_host_env
  load_env_file "$FRONTEND_DIR/.env"
  export JARVIS_API_URL="${JARVIS_API_URL:-http://127.0.0.1:8000}"

  log_detail "DISPLAY=$DISPLAY"
  log_detail "JARVIS_API_URL=$JARVIS_API_URL"
  log_detail "Launching: $electron_bin . --no-sandbox"
  log_detail "Log file: $log_file"

  : >"$log_file"
  (
    cd "$FRONTEND_DIR"
    export DISPLAY JARVIS_API_URL
    nohup env DISPLAY="$DISPLAY" JARVIS_API_URL="$JARVIS_API_URL" \
      "$electron_bin" . --no-sandbox >>"$log_file" 2>&1 &
    echo $! >"$pid_file"
  )

  local pid="" tries=15
  while [ "$tries" -gt 0 ]; do
    sleep 1
    tries=$((tries - 1))
    [ -f "$pid_file" ] && pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      log_ok "Frontend started (electron pid $pid)"
      log_detail "If the window is not visible, check: tail -f $log_file"
      return 0
    fi
    if pgrep -f "${FRONTEND_DIR}/node_modules/electron" >/dev/null 2>&1; then
      log_ok "Frontend electron process is running"
      return 0
    fi
  done

  warn "Frontend did not stay running — last log lines:"
  tail -30 "$log_file" 2>/dev/null | while IFS= read -r line; do
    log_detail "  $line"
  done
  log_detail "Retry manually: cd $FRONTEND_DIR && DISPLAY=$DISPLAY JARVIS_API_URL=$JARVIS_API_URL npm start"
  return 1
}

ensure_linux_desktop() {
  case "$OS" in
    linux)
      [ -n "${DISPLAY:-}" ] || die "No GUI session (DISPLAY unset). Run from a desktop terminal."
      ;;
    macos)
      die "Screenpipe Docker capture needs a Linux desktop. On macOS, use a Linux machine or VM, or run this script inside Linux (e.g. remote desktop)."
      ;;
    windows)
      die "Screenpipe Docker capture needs a Linux desktop. On Windows, run this script in WSL2 with a GUI (WSLg) or on a Linux host."
      ;;
    *)
      die "Unsupported OS for Screenpipe Docker capture"
      ;;
  esac
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    return
  fi
  log "Docker not found — installing..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER" 2>/dev/null || true
  if ! docker info >/dev/null 2>&1; then
    die "Docker installed but not usable. Log out/in or run: sudo usermod -aG docker \$USER"
  fi
}

prompt_server_url() {
  if [ "$LOCAL_ONLY" = true ]; then
    SERVER_URL="${SERVER_URL:-http://127.0.0.1:8000}"
    log_detail "Local mode — using SERVER_URL=$SERVER_URL (no central server required)"
    return 0
  fi
  if [ -z "$SERVER_URL" ]; then
    read -r -p "Enter server URL (e.g. http://192.168.1.50:8000): " SERVER_URL
    SERVER_URL="${SERVER_URL%/}"
  fi
  [ -n "$SERVER_URL" ] || die "--server-url is required (or use --local-only / --clone-only)"
}

check_server_reachable() {
  if [ "$LOCAL_ONLY" = true ] || [ "$CLONE_ONLY" = true ]; then
    log_detail "Skipping central server health check"
    return 0
  fi
  log "Checking server at $SERVER_URL ..."
  if ! curl -sf "${SERVER_URL}/health" >/dev/null; then
    die "Cannot reach server at ${SERVER_URL}/health — start server-deploy.sh on the server first"
  fi
  log_ok "Central server is online at $SERVER_URL"
}

setup_env() {
  setup_backend_env
  setup_frontend_env
  check_backend_env
  check_frontend_env
}

export_host_env() {
  # UID is readonly in bash — use HOST_UID for Docker Compose interpolation.
  export HOST_UID="${HOST_UID:-$(id -u)}"
  export DISPLAY="${DISPLAY:-:0}"
}

prepare_x11() {
  export_host_env
  local x11_script="$SCRIPT_DIR/docker-x11.sh"
  if [ ! -f "$x11_script" ] && [ -f "$BACKEND_DIR/scripts/docker-x11.sh" ]; then
    x11_script="$BACKEND_DIR/scripts/docker-x11.sh"
  fi
  if [ -f "$x11_script" ]; then
    # shellcheck source=scripts/docker-x11.sh
    source "$x11_script"
    prepare_xauthority_mount "$BACKEND_DIR/data/.jarvis-xauthority"
  else
    warn "docker-x11.sh not found — continuing without X authority copy"
    mkdir -p "$BACKEND_DIR/data"
  fi
  xhost +local:docker 2>/dev/null || true
  pkill -f "screenpipe record" 2>/dev/null || true
  mkdir -p "$BACKEND_DIR/data" "$BACKEND_DIR/media" "${HOME}/.screenpipe"
}

ensure_client_docker_files() {
  if [ -f "$BACKEND_DIR/docker-compose.client.yml" ]; then
    log_ok "Client Docker stack found at $BACKEND_DIR"
    return 0
  fi

  local candidates=() src
  candidates+=("$(cd "$SCRIPT_DIR/.." && pwd)")
  [ -n "${JARVIS_SOURCE_DIR:-}" ] && candidates+=("$JARVIS_SOURCE_DIR")
  candidates+=("$HOME/Projects/jarvis-bot-be" "$HOME/jarvis-bot-be")

  for src in "${candidates[@]}"; do
    [ -f "$src/docker-compose.client.yml" ] || continue
    log_detail "Seeding client Docker files from $src ..."
    cp "$src/docker-compose.client.yml" "$BACKEND_DIR/"
    cp "$src/Dockerfile.client" "$BACKEND_DIR/"
    [ -f "$src/.env.client.example" ] && cp "$src/.env.client.example" "$BACKEND_DIR/"
    mkdir -p "$BACKEND_DIR/scripts"
    [ -f "$src/scripts/docker-entrypoint.sh" ] && cp "$src/scripts/docker-entrypoint.sh" "$BACKEND_DIR/scripts/"
    chmod +x "$BACKEND_DIR/scripts/docker-entrypoint.sh" 2>/dev/null || true
    log_ok "Copied docker-compose.client.yml + Dockerfile.client into $BACKEND_DIR"
    return 0
  done

  die "Missing docker-compose.client.yml in $BACKEND_DIR. Push the backend branch to GitHub or set JARVIS_SOURCE_DIR=/path/to/jarvis-bot-be"
}

fetch_screenpipe_token() {
  cd "$BACKEND_DIR"
  local env_file="$BACKEND_DIR/.env"
  local token out TOKEN
  token="$(env_file_get "$env_file" SCREENPIPE_API_TOKEN)"
  if [ -n "$token" ] && [ "${token#sp-}" != "$token" ]; then
    log_ok "SCREENPIPE_API_TOKEN already in .env"
    return 0
  fi

  export_host_env
  log_detail "Running: screenpipe auth token (inside container) ..."

  if docker compose -f docker-compose.client.yml ps --status running -q jarvis-client 2>/dev/null | grep -q .; then
    out="$(docker compose -f docker-compose.client.yml exec -T jarvis-client screenpipe auth token 2>&1 || true)"
  else
    log_detail "Container not up yet — one-off run for token"
    docker compose -f docker-compose.client.yml build jarvis-client >/dev/null 2>&1 || true
    out="$(docker compose -f docker-compose.client.yml run --rm --no-deps jarvis-client screenpipe auth token 2>&1 || true)"
  fi

  TOKEN="$(printf '%s\n' "$out" | grep -E '^sp-' | tail -1 || true)"
  if [ -z "$TOKEN" ]; then
    TOKEN="$(printf '%s\n' "$out" | grep -Eo 'sp-[a-zA-Z0-9_-]+' | tail -1 || true)"
  fi

  if [ -n "$TOKEN" ] && [ "${TOKEN#sp-}" != "$TOKEN" ]; then
    env_file_set "$env_file" "SCREENPIPE_API_TOKEN" "$TOKEN"
    log_ok "Saved SCREENPIPE_API_TOKEN to backend .env"
    if docker compose -f docker-compose.client.yml ps --status running -q jarvis-client 2>/dev/null | grep -q .; then
      log_detail "Restarting container to load new token ..."
      docker compose -f docker-compose.client.yml up -d
      sleep 3
    fi
    return 0
  fi

  warn "Could not fetch Screenpipe token yet"
  log_detail "auth output: $(printf '%s' "$out" | tr '\n' ' ' | head -c 300)"
}

wait_for_health() {
  local tries=60 elapsed=0
  log_detail "Polling http://127.0.0.1:8000/health ..."
  while [ "$tries" -gt 0 ]; do
    if curl -sf "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
      return 0
    fi
    if [ $((elapsed % 15)) -eq 0 ] && [ "$elapsed" -gt 0 ]; then
      log_detail "  still waiting (${elapsed}s) ..."
    fi
    tries=$((tries - 1))
    elapsed=$((elapsed + 3))
    sleep 3
  done
  die "Client container did not become healthy. Check: docker compose -f $BACKEND_DIR/docker-compose.client.yml logs"
}

print_service_status() {
  local status="${1:-}"
  if [ -z "$status" ]; then
    status="$(curl -sf "http://127.0.0.1:8000/api/v1/services/status" 2>/dev/null || true)"
  fi
  if [ -z "$status" ]; then
    log_detail "  status API not reachable yet"
    return 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$status" | python3 - <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(sys.stdin)
    sp = d.get("screenpipe") or {}
    ocr = d.get("paddle_ocr") or {}
    print(f"  cli_running={sp.get('cli_running')}  screenpipe_running={sp.get('running')}")
    print(f"  paddle_ocr_running={ocr.get('running')}  manager_started={d.get('manager_started')}")
    if d.get("hint"):
        print(f"  hint: {d['hint']}")
except Exception as e:
    print(f"  (could not parse status: {e})")
PY
  else
    log_detail "  $(echo "$status" | tr -d '\n' | head -c 200)"
  fi
}

verify_screenpipe_and_ocr() {
  local max_wait="${SCREENPIPE_VERIFY_SECONDS:-90}"
  local elapsed=0

  log_detail "First Screenpipe start can take 2–5 min (model download)."
  log_detail "Checking for up to ${max_wait}s — setup continues even if still warming up."

  while [ "$elapsed" -lt "$max_wait" ]; do
    local status
    status="$(curl -sf "http://127.0.0.1:8000/api/v1/services/status" 2>/dev/null || true)"

    if [ -n "$status" ]; then
      log_detail "[${elapsed}s] service status:"
      print_service_status "$status"

      if echo "$status" | grep -q '"cli_running": true'; then
        log_ok "Screenpipe CLI is running"
        echo "$status" | grep -q '"paddle_ocr".*"running": true' \
          && log_ok "Paddle OCR worker is running"
        log_detail "Switch apps or click on screen to trigger capture"
        return 0
      fi

      if echo "$status" | grep -q '"paddle_ocr".*"running": true' \
        && echo "$status" | grep -q '"manager_started": true'; then
        log_ok "API + OCR worker up (Screenpipe CLI may still be downloading models)"
        log_detail "Watch logs: docker compose -f $BACKEND_DIR/docker-compose.client.yml logs -f"
        return 0
      fi
    else
      log_detail "[${elapsed}s] waiting for status API ..."
    fi

    sleep 10
    elapsed=$((elapsed + 10))
  done

  warn "Screenpipe verify timed out after ${max_wait}s — container may still be starting"
  log_detail "Tail container logs:"
  (cd "$BACKEND_DIR" && docker compose -f docker-compose.client.yml logs --tail=30) || true
  log_detail "Follow live: docker compose -f $BACKEND_DIR/docker-compose.client.yml logs -f"
  return 0
}

print_clone_summary() {
  echo ""
  echo "=========================================="
  echo " Clone + env setup complete"
  echo "=========================================="
  echo " Install dir:   $INSTALL_DIR"
  echo " Backend:       $BACKEND_DIR"
  echo " Backend .env:  $BACKEND_DIR/.env"
  echo " Frontend:      $FRONTEND_DIR"
  echo " Frontend .env: $FRONTEND_DIR/.env"
  if [ ! -f "$BACKEND_DIR/docker-compose.client.yml" ]; then
    echo ""
    echo " NOTE: docker-compose.client.yml not in cloned backend yet."
    echo "       Push latest backend branch or use your local jarvis-bot-be checkout."
  fi
  echo ""
  echo " Next steps:"
  echo "  1) Test local Docker capture (needs docker-compose.client.yml):"
  echo "     $0 --local-only --install-dir $INSTALL_DIR"
  echo "  2) When server is ready:"
  echo "     $0 --server-url http://YOUR_SERVER:8000 --install-dir $INSTALL_DIR"
  echo "=========================================="
}

print_running_summary() {
  echo ""
  echo "=========================================="
  echo " Desktop client is running"
  echo "=========================================="
  echo " Backend:       $BACKEND_DIR"
  echo " Backend .env:  $BACKEND_DIR/.env"
  echo " Frontend:      $FRONTEND_DIR"
  echo " Frontend .env: $FRONTEND_DIR/.env"
  echo " Local API:     http://127.0.0.1:8000"
  if [ "$LOCAL_ONLY" = true ]; then
    echo " Mode:          local only (sync to central server disabled)"
  else
    echo " Syncing to:    ${SERVER_URL}"
  fi
  echo " Status:        http://127.0.0.1:8000/api/v1/services/status"
  if [ "$START_FRONTEND" = true ]; then
    echo " UI:            Electron launched (log: $BACKEND_DIR/data/frontend.log)"
  else
    echo " Start UI:      cd $FRONTEND_DIR && npm start"
  fi
  echo " Docker logs:   docker compose -f $BACKEND_DIR/docker-compose.client.yml logs -f"
  echo "=========================================="
}

# --- main ---
echo ""
log "Jarvis desktop setup"
if [ "$CLONE_ONLY" = true ]; then
  log_detail "Mode: clone-only (no Docker, no server)"
elif [ "$LOCAL_ONLY" = true ]; then
  log_detail "Mode: local-only (Docker capture, no central server)"
else
  log_detail "Mode: full (requires central server)"
fi

log_step "Resolve paths"
resolve_dirs

log_step "Clone or update repositories"
clone_repos_if_needed

log_step "Install frontend dependencies"
install_frontend_deps

if [ "$CLONE_ONLY" = true ]; then
  log_step "Configure environment files"
  cd "$BACKEND_DIR"
  prompt_server_url
  setup_env
  print_clone_summary
  exit 0
fi

log_step "Check Linux desktop + Docker"
ensure_linux_desktop
ensure_docker
log_ok "Docker is available"

log_step "Configure environment files"
cd "$BACKEND_DIR"
prompt_server_url
check_server_reachable
setup_env

log_step "Prepare display (X11) for Screenpipe"
prepare_x11
log_ok "X11 prepared (DISPLAY=$DISPLAY)"

log_step "Build and start Docker container"
ensure_client_docker_files
export_host_env
log_detail "HOST_UID=$HOST_UID DISPLAY=$DISPLAY"
log_detail "Running: docker compose -f $BACKEND_DIR/docker-compose.client.yml up -d --build"
(cd "$BACKEND_DIR" && docker compose -f docker-compose.client.yml up -d --build)
log_ok "Docker container started"
log_detail "Recent container logs:"
(cd "$BACKEND_DIR" && docker compose -f docker-compose.client.yml logs --tail=50) || true

log_step "Wait for local API health"
wait_for_health
log_ok "Local API healthy at http://127.0.0.1:8000/health"

if [ "$START_FRONTEND" = true ]; then
  log_step "Start Electron frontend"
  check_frontend_env
  start_frontend || warn "Frontend launch failed — see $BACKEND_DIR/data/frontend.log"
fi

log_step "Fetch Screenpipe API token"
fetch_screenpipe_token
check_backend_screenpipe_env || true

log_step "Verify Screenpipe + OCR"
verify_screenpipe_and_ocr

print_running_summary
