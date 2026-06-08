#!/usr/bin/env bash
# Quick checks for Docker-only deployment on a new Linux machine.
set -euo pipefail

cd "$(dirname "$0")/.."

export UID="${UID:-$(id -u)}"
export DISPLAY="${DISPLAY:-:0}"

ok() { echo "  OK   $1"; }
warn() { echo "  WARN $1"; }
fail() { echo "  FAIL $1"; }

echo "=== Jarvis Docker doctor ==="

if command -v docker >/dev/null 2>&1; then
  ok "docker installed ($(docker --version | head -1))"
else
  fail "docker not installed"
fi

if docker compose version >/dev/null 2>&1; then
  ok "docker compose available"
else
  fail "docker compose not available"
fi

if [ -f .env ]; then
  ok ".env exists"
else
  warn ".env missing — run: cp .env.example .env"
fi

if [ -n "${DISPLAY:-}" ]; then
  ok "DISPLAY=${DISPLAY}"
else
  warn "DISPLAY not set — screen capture may fail"
fi

if [ -S "/run/user/${UID}/pulse/native" ]; then
  ok "PulseAudio socket /run/user/${UID}/pulse/native"
else
  warn "PulseAudio socket missing — meeting audio will use OCR fallback"
fi

if [ -e /tmp/.X11-unix ]; then
  ok "X11 socket dir mounted target exists on host"
else
  warn "/tmp/.X11-unix missing — are you in a GUI session?"
fi

if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  ok "API http://127.0.0.1:8000/health"
  curl -sf http://127.0.0.1:8000/api/v1/services/status 2>/dev/null \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
sp=d.get('screenpipe',{})
h=sp.get('health',{})
print('  INFO screenpipe audio_status:', h.get('audio_status','unknown'))
print('  INFO live_recording_id:', sp.get('live_recording_id'))
" 2>/dev/null || true
else
  warn "API not running on :8000 (start with ./scripts/docker-up.sh)"
fi

if curl -sf http://127.0.0.1:3030/health >/dev/null 2>&1; then
  curl -sf http://127.0.0.1:3030/health 2>/dev/null \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('  INFO screenpipe frame_status:', d.get('frame_status'))
print('  INFO screenpipe audio_status:', d.get('audio_status'))
" 2>/dev/null || ok "Screenpipe API http://127.0.0.1:3030/health"
else
  warn "Screenpipe API not on :3030 yet (starts inside container)"
fi

echo "=== done ==="
