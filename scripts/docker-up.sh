#!/usr/bin/env bash
# Docker-only startup — use this on any Linux machine with Docker + a desktop session.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env found. Running first-time setup..."
  ./scripts/docker-setup.sh
fi

export UID="${UID:-$(id -u)}"
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

if [ "$(uname -s)" = "Linux" ]; then
  xhost +local:docker 2>/dev/null || true
  # Free port 3030 so the container runs its own screenpipe (with PulseAudio mounts).
  pkill -f "screenpipe record" 2>/dev/null || true
fi

mkdir -p data media "${HOME}/.screenpipe"

if [ ! -S "/run/user/${UID}/pulse/native" ]; then
  echo "WARN: PulseAudio socket missing at /run/user/${UID}/pulse/native"
  echo "      Meeting audio will use OCR fallback until host audio is available."
fi

echo "Starting Jarvis (Docker-only)..."
echo "  API:       http://127.0.0.1:8000"
echo "  Status:    http://127.0.0.1:8000/api/v1/services/status"
echo "  Doctor:    ./scripts/docker-doctor.sh"
echo ""

docker compose build "$@"
docker compose up "$@"
