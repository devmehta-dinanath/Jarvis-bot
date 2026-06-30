#!/usr/bin/env bash
# First-time setup on a new machine — Docker only, no host Python/screenpipe required.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

export UID="${UID:-$(id -u)}"
export DISPLAY="${DISPLAY:-:0}"

# shellcheck source=scripts/docker-x11.sh
source "$(dirname "$0")/docker-x11.sh"
prepare_xauthority_mount "data/.jarvis-xauthority"

if [ "$(uname -s)" = "Linux" ]; then
  xhost +local:docker 2>/dev/null || true
fi

echo "Building Docker image (first time may take several minutes)..."
docker compose build

mkdir -p data media "${HOME}/.screenpipe"

if ! grep -q 'SCREENPIPE_API_TOKEN=sp-' .env 2>/dev/null; then
  echo ""
  echo "Getting Screenpipe API token from container..."
  TOKEN="$(docker compose run --rm --no-deps jarvis-bot screenpipe auth token 2>/dev/null | tail -1 || true)"
  if [ -n "$TOKEN" ] && [ "${TOKEN#sp-}" != "$TOKEN" ]; then
    if grep -q '^SCREENPIPE_API_TOKEN=' .env; then
      sed -i "s|^SCREENPIPE_API_TOKEN=.*|SCREENPIPE_API_TOKEN=${TOKEN}|" .env
    else
      echo "SCREENPIPE_API_TOKEN=${TOKEN}" >> .env
    fi
    echo "Saved SCREENPIPE_API_TOKEN to .env"
  else
    echo "Could not auto-generate token."
    echo "After first start, run inside container:"
    echo "  docker compose exec jarvis-bot screenpipe auth token"
    echo "Then set SCREENPIPE_API_TOKEN in .env"
  fi
fi

echo ""
echo "Setup complete. Start with:"
echo "  ./scripts/docker-up.sh"
