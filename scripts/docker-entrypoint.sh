#!/bin/sh
set -e

SCREENPIPE_BIN="$(find /usr/lib/node_modules/screenpipe -path '*/bin/screenpipe' -type f 2>/dev/null | head -1)"

if [ -z "$SCREENPIPE_BIN" ]; then
    echo "ERROR: screenpipe binary not found in image. Rebuild: docker compose build --no-cache"
    exit 1
fi

MISSING_LIBS="$(ldd "$SCREENPIPE_BIN" 2>/dev/null | grep 'not found' || true)"
if [ -n "$MISSING_LIBS" ]; then
    echo "ERROR: screenpipe is missing shared libraries:"
    echo "$MISSING_LIBS"
    exit 1
fi

HOST_UID="${HOST_UID:-1000}"
PULSE_SOCKET="/run/user/${HOST_UID}/pulse/native"

if [ -z "${PULSE_SERVER:-}" ] && [ -S "$PULSE_SOCKET" ]; then
    export PULSE_SERVER="unix:${PULSE_SOCKET}"
fi

echo "Docker-only mode: screenpipe=$(command -v screenpipe) DISPLAY=${DISPLAY:-unset}"
if [ -n "${PULSE_SERVER:-}" ]; then
    echo "PulseAudio: PULSE_SERVER=${PULSE_SERVER}"
    echo "Meeting audio: screenpipe record captures mic + system audio automatically."
else
    echo "WARN: PulseAudio socket not found at ${PULSE_SOCKET}."
    echo "      Meeting transcripts need host PulseAudio/PipeWire for mic + system audio."
    echo "      On host: systemctl --user start pipewire pipewire-pulse"
fi

if [ "${SCREENPIPE_START_CLI:-true}" = "true" ] && [ -z "${DISPLAY:-}" ]; then
    echo "WARN: DISPLAY is not set. Run: xhost +local:docker && docker compose up"
fi

# Host network: free Screenpipe API port if a stale process is still bound.
pkill -f "screenpipe.*record" 2>/dev/null || true
sleep 1

exec "$@"
