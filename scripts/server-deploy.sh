#!/usr/bin/env bash
# Deploy Jarvis central server (WhatsApp, DB, AI, sync API) — no Screenpipe required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLONE_URL=""
INSTALL_DIR="${JARVIS_INSTALL_DIR:-$REPO_DIR}"

log() { echo "[server-deploy] $*"; }
die() { echo "[server-deploy] ERROR: $*" >&2; exit 1; }
warn() { echo "[server-deploy] WARN: $*" >&2; }

env_file_get() {
  local file="$1" key="$2"
  grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- \
    | sed 's/^["'\'' ]*//;s/["'\'' ]*$//'
}

is_placeholder_value() {
  local value="${1:-}"
  case "$value" in
    ""|*your-*|*change-me*|*YOUR_*|*placeholder*)
      return 0
      ;;
  esac
  return 1
}

check_whatsapp_env() {
  local env_file=".env"
  local enabled phone_id token verify_token
  enabled="$(env_file_get "$env_file" WHATSAPP_ENABLED)"
  case "$enabled" in
    0|false|no) return 0 ;;
  esac

  phone_id="$(env_file_get "$env_file" WHATSAPP_PHONE_NUMBER_ID)"
  token="$(env_file_get "$env_file" WHATSAPP_ACCESS_TOKEN)"
  verify_token="$(env_file_get "$env_file" WHATSAPP_VERIFY_TOKEN)"

  if is_placeholder_value "$phone_id"; then
    die "WHATSAPP_PHONE_NUMBER_ID is required on server (Meta → WhatsApp → API Setup → Phone number ID)"
  fi
  if is_placeholder_value "$token"; then
    die "WHATSAPP_ACCESS_TOKEN is required on server (Meta long-lived token)"
  fi
  if is_placeholder_value "$verify_token"; then
    die "WHATSAPP_VERIFY_TOKEN is required on server (must match Meta webhook config)"
  fi
  log "WhatsApp env OK (phone_number_id set)"
}

ensure_linux() {
  [ "$(uname -s)" = "Linux" ] || die "Linux is required for server deployment"
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

clone_repo_if_needed() {
  if [ -f "$INSTALL_DIR/docker-compose.server.yml" ]; then
    cd "$INSTALL_DIR"
    return
  fi
  if [ -z "$CLONE_URL" ]; then
    die "Not in jarvis-bot-be repo. Re-run with: --clone https://github.com/YOUR_ORG/jarvis-bot-be.git"
  fi
  log "Cloning $CLONE_URL → $INSTALL_DIR"
  git clone "$CLONE_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
}

wait_for_health() {
  local tries=30
  while [ "$tries" -gt 0 ]; do
    if curl -sf "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 2
  done
  die "Server did not become healthy on :8000. Check: docker compose -f docker-compose.server.yml logs"
}

doctor() {
  docker info >/dev/null 2>&1 || die "Docker daemon is not running"
  [ -f .env ] || die ".env missing — create it on the server: cp .env.server.example .env"
  mkdir -p data media
  check_whatsapp_env
  log "Doctor OK"
}

usage() {
  cat <<EOF
Usage: $0 [--clone GIT_URL]

Deploys the Jarvis server container (WhatsApp + DB + AI + sync API).

Stop:  docker compose -f docker-compose.server.yml down
Logs:  docker compose -f docker-compose.server.yml logs -f
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --clone) CLONE_URL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

ensure_linux
ensure_docker
clone_repo_if_needed
cd "$INSTALL_DIR"
doctor

log "Building and starting server container..."
docker compose -f docker-compose.server.yml up -d --build

wait_for_health

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "=========================================="
echo " Jarvis server is running"
echo " Health:  http://127.0.0.1:8000/health"
echo " Status:  http://127.0.0.1:8000/api/v1/services/status"
if [ -n "$HOST_IP" ]; then
  echo ""
  echo " Give desktop users this URL:"
  echo "   http://${HOST_IP}:8000"
  echo ""
  echo " Desktop setup:"
  echo "   ./scripts/desktop-client-setup.sh --server-url http://${HOST_IP}:8000"
fi
echo "=========================================="
