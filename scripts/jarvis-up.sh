#!/usr/bin/env bash
# One command to start Jarvis on macOS or Linux.
# macOS: starts native Screenpipe + Docker server + Docker client.
# Linux:  starts Docker client (Screenpipe inside container).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="${JARVIS_FRONTEND_DIR:-$(cd "$REPO_DIR/../Jarvis-bot-frontend" 2>/dev/null && pwd || true)}"
PID_FILE="$REPO_DIR/data/.jarvis-screenpipe.pid"
SCREENPIPE_DATA="$HOME/Library/Application Support/jarvis-bot-fe/screenpipe-data"
SCREENPIPE_PORT="${SCREENPIPE_PORT:-3030}"
START_FRONTEND=false
DETACH=true
COMPOSE_ARGS=()

log() { echo "[jarvis-up] $*"; }
die() { echo "[jarvis-up] ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: ./scripts/jarvis-up.sh [options]

One command — starts everything for your OS (no separate Screenpipe command).

Options:
  --foreground, -f     Attach to Docker logs (default: detached -d)
  --with-ui            Also open the Electron frontend (npm run dev)
  --no-frontend        Skip frontend even if --with-ui was set before
  -h, --help           Show this help

macOS:
  Native Screenpipe (:3030) + Docker server (:8000) + Docker client (:8002)

Linux:
  Docker client stack (Screenpipe inside container)

Stop everything:
  ./scripts/jarvis-down.sh
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --foreground|-f) DETACH=false ;;
    --with-ui) START_FRONTEND=true ;;
    --no-frontend) START_FRONTEND=false ;;
    -h|--help) usage; exit 0 ;;
    *) COMPOSE_ARGS+=("$1") ;;
  esac
  shift
done

ensure_env() {
  cd "$REPO_DIR"
  if [ ! -f .env ]; then
    if [ -f .env.server.example ]; then
      cp .env.server.example .env
      log "Created .env from .env.server.example"
    else
      die "Missing .env — copy .env.server.example to .env first"
    fi
  fi
  if grep -qE '^[^#=[:space:]].*\(' .env 2>/dev/null; then
    die "Invalid .env — lines must be KEY=value or # comments (fix line 1)"
  fi
}

resolve_screenpipe_bin() {
  local candidates=(
    "/Applications/Jarvis.app/Contents/Resources/resources/mac/screenpipe-lib/screenpipe"
    "$FRONTEND_DIR/resources/mac/screenpipe-lib/screenpipe"
    "$REPO_DIR/../Jarvis.app/Contents/Resources/resources/mac/screenpipe-lib/screenpipe"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  if command -v screenpipe >/dev/null 2>&1; then
    command -v screenpipe
    return 0
  fi
  return 1
}

screenpipe_healthy() {
  curl -sf --max-time 2 "http://127.0.0.1:${SCREENPIPE_PORT}/health" >/dev/null 2>&1
}

start_screenpipe_mac() {
  if screenpipe_healthy; then
    log "Screenpipe already running on :${SCREENPIPE_PORT}"
    return 0
  fi

  mkdir -p "$REPO_DIR/data" "$SCREENPIPE_DATA"
  mkdir -p "$(dirname "$PID_FILE")"

  local bin
  if ! bin="$(resolve_screenpipe_bin)"; then
    log "No bundled screenpipe — installing via npx (first run may take a few minutes)..."
    nohup npx -y screenpipe@latest record --port "$SCREENPIPE_PORT" \
      --data-dir "$SCREENPIPE_DATA" \
      >"$REPO_DIR/data/screenpipe-host.log" 2>&1 &
    echo $! >"$PID_FILE"
  else
    log "Starting native Screenpipe: $bin"
    xattr -cr "$(dirname "$bin")" 2>/dev/null || true
    nohup "$bin" record --port "$SCREENPIPE_PORT" \
      --data-dir "$SCREENPIPE_DATA" \
      >"$REPO_DIR/data/screenpipe-host.log" 2>&1 &
    echo $! >"$PID_FILE"
  fi

  log "Waiting for Screenpipe on :${SCREENPIPE_PORT} (up to 3 min on first run)..."
  local i=0
  while [ "$i" -lt 90 ]; do
    if screenpipe_healthy; then
      log "Screenpipe is up on :${SCREENPIPE_PORT}"
      return 0
    fi
    sleep 2
    i=$((i + 1))
  done
  die "Screenpipe did not start — see $REPO_DIR/data/screenpipe-host.log"
}

start_docker_mac() {
  cd "$REPO_DIR"
  mkdir -p "$REPO_DIR/data/screenpipe/models"
  if [ "$DETACH" = true ]; then
    if [ "${#COMPOSE_ARGS[@]}" -gt 0 ]; then
      docker compose -f docker-compose.mac.yml up -d --build "${COMPOSE_ARGS[@]}"
    else
      docker compose -f docker-compose.mac.yml up -d --build
    fi
    log "Docker stack started (detached)"
  else
    if [ "${#COMPOSE_ARGS[@]}" -gt 0 ]; then
      docker compose -f docker-compose.mac.yml up --build "${COMPOSE_ARGS[@]}"
    else
      docker compose -f docker-compose.mac.yml up --build
    fi
  fi
}

start_docker_linux() {
  cd "$REPO_DIR"
  if [ -z "${DISPLAY:-}" ]; then
    die "Linux desktop required (DISPLAY unset). Run from a GUI session."
  fi
  if [ "$DETACH" = true ]; then
    if [ "${#COMPOSE_ARGS[@]}" -gt 0 ]; then
      docker compose -f docker-compose.client.yml up -d --build "${COMPOSE_ARGS[@]}"
    else
      docker compose -f docker-compose.client.yml up -d --build
    fi
  else
    if [ "${#COMPOSE_ARGS[@]}" -gt 0 ]; then
      docker compose -f docker-compose.client.yml up --build "${COMPOSE_ARGS[@]}"
    else
      docker compose -f docker-compose.client.yml up --build
    fi
  fi
}

configure_frontend_env() {
  [ -d "$FRONTEND_DIR" ] || return 0
  local env_file="$FRONTEND_DIR/.env"
  local client_port="${CLIENT_PORT:-8002}"
  local server_url="${JARVIS_SERVER_URL:-}"
  if [ -z "$server_url" ] && [ -f "$REPO_DIR/.env" ]; then
    server_url="$(grep -E '^JARVIS_SERVER_URL=' "$REPO_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/^["'\'' ]*//;s/["'\'' ]*$//')"
  fi
  if [ -z "$server_url" ]; then
    server_url="http://127.0.0.1:${PORT:-8000}"
  fi
  if [ ! -f "$env_file" ] && [ -f "$FRONTEND_DIR/.env.example" ]; then
    cp "$FRONTEND_DIR/.env.example" "$env_file"
  fi
  if [ -f "$env_file" ]; then
    sed -i '' "s|^JARVIS_API_URL=.*|JARVIS_API_URL=http://127.0.0.1:${client_port}|" "$env_file" 2>/dev/null \
      || sed -i "s|^JARVIS_API_URL=.*|JARVIS_API_URL=http://127.0.0.1:${client_port}|" "$env_file"
    sed -i '' "s|^JARVIS_SERVER_URL=.*|JARVIS_SERVER_URL=${server_url}|" "$env_file" 2>/dev/null \
      || sed -i "s|^JARVIS_SERVER_URL=.*|JARVIS_SERVER_URL=${server_url}|" "$env_file"
  fi
}

start_frontend() {
  [ "$START_FRONTEND" = true ] || return 0
  [ -d "$FRONTEND_DIR" ] || die "Frontend not found at $FRONTEND_DIR"
  configure_frontend_env
  log "Starting frontend UI..."
  cd "$FRONTEND_DIR"
  if [ ! -d node_modules ]; then
    npm install
  fi
  npm run dev
}

print_status_mac() {
  local client_port="${CLIENT_PORT:-8002}"
  local server_port="${PORT:-8000}"
  cat <<EOF

Jarvis is running (macOS):

  Screenpipe (native)  http://127.0.0.1:${SCREENPIPE_PORT}/health
  Server API (Docker)  http://127.0.0.1:${server_port}/health
  Client API (Docker)  http://127.0.0.1:${client_port}/health
  Client status        http://127.0.0.1:${client_port}/api/v1/services/status

Logs:
  Screenpipe  $REPO_DIR/data/screenpipe-host.log
  Docker      docker logs -f jarvis-bot-server
              docker logs -f jarvis-bot-client-mac

Stop: ./scripts/jarvis-down.sh
EOF
}

main() {
  ensure_env
  case "$(uname -s)" in
    Darwin)
      start_screenpipe_mac
      start_docker_mac
      print_status_mac
      start_frontend
      ;;
    Linux)
      start_docker_linux
      log "Linux client stack started"
      start_frontend
      ;;
    *)
      die "Unsupported OS — use Jarvis.app installer on macOS/Windows"
      ;;
  esac
}

main "$@"
