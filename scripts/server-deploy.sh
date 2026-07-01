#!/usr/bin/env bash
# Deploy Jarvis central server (WhatsApp, DB, AI, sync API) — no Screenpipe required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLONE_URL=""
INSTALL_DIR="${JARVIS_INSTALL_DIR:-$REPO_DIR}"

log() { echo "[server-deploy] $*"; }
die() { echo "[server-deploy] ERROR: $*" >&2; exit 1; }

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
                                                          
prompt_env() {
  if [ ! -f .env ]; then
    cp .env.server.example .env
    log "Created .env from .env.server.example"
  fi
  if grep -q 'change-me-to-a-long-random-string' .env 2>/dev/null; then
    KEY="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    sed -i "s|^SYNC_API_KEY=.*|SYNC_API_KEY=${KEY}|" .env
    log "Generated SYNC_API_KEY — share this with desktop clients"
    echo "  SYNC_API_KEY=${KEY}"
  fi
  if grep -q 'YOUR_SERVER_IP' .env 2>/dev/null; then
    HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -n "$HOST_IP" ]; then
      sed -i "s|http://YOUR_SERVER_IP:8000|http://${HOST_IP}:8000|g" .env
    fi
  fi
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
  [ -f .env ] || die ".env missing"
  mkdir -p data media
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
prompt_env
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
