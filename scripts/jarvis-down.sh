#!/usr/bin/env bash
# Stop Jarvis stack started by jarvis-up.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$REPO_DIR/data/.jarvis-screenpipe.pid"

log() { echo "[jarvis-down] $*"; }

stop_screenpipe() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      log "Stopped Screenpipe (pid $pid)"
    fi
    rm -f "$PID_FILE"
  fi
  pkill -f "screenpipe.*record.*--port" 2>/dev/null || true
}

stop_docker() {
  cd "$REPO_DIR"
  case "$(uname -s)" in
    Darwin)
      docker compose -f docker-compose.mac.yml down "$@" 2>/dev/null || true
      ;;
    Linux)
      docker compose -f docker-compose.client.yml down "$@" 2>/dev/null || true
      ;;
  esac
  docker compose -f docker-compose.server.yml down "$@" 2>/dev/null || true
}

stop_screenpipe
stop_docker "$@"
log "Jarvis stopped"
