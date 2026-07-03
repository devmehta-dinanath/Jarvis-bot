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

is_placeholder_token() {
    case "$1" in
        ""|*your-token*|*changeme*|*replace-me*|*placeholder*)
            return 0
            ;;
    esac
    return 1
}

read_token_from_screenpipe_env() {
    env_path="$1"
    [ -f "$env_path" ] || return 1
    token="$(grep -E '^(SCREENPIPE_API_TOKEN|SCREENPIPE_LOCAL_API_KEY|SCREENPIPE_API_KEY)=' "$env_path" 2>/dev/null \
        | tail -1 | cut -d= -f2- | sed 's/^["'\'' ]*//;s/["'\'' ]*$//')"
    if [ -n "$token" ] && ! is_placeholder_token "$token"; then
        printf '%s' "$token"
        return 0
    fi
    return 1
}

fetch_screenpipe_token_from_cli() {
    out="$(screenpipe auth token 2>&1 || true)"
    token="$(printf '%s\n' "$out" | grep -E '^sp-' | tail -1 || true)"
    if [ -z "$token" ]; then
        token="$(printf '%s\n' "$out" | grep -Eo 'sp-[a-zA-Z0-9_-]+' | tail -1 || true)"
    fi
    if [ -n "$token" ] && ! is_placeholder_token "$token"; then
        printf '%s' "$token"
        return 0
    fi
    return 1
}

ensure_screenpipe_api_token() {
    if [ -n "${SCREENPIPE_API_TOKEN:-}" ] && ! is_placeholder_token "$SCREENPIPE_API_TOKEN"; then
        return 0
    fi

    token="$(read_token_from_screenpipe_env /root/.screenpipe/.env || true)"
    if [ -n "$token" ]; then
        export SCREENPIPE_API_TOKEN="$token"
        echo "Screenpipe API token loaded from ~/.screenpipe/.env"
        return 0
    fi

    echo "Auto-fetching Screenpipe API token..."
    token="$(fetch_screenpipe_token_from_cli || true)"
    if [ -n "$token" ]; then
        export SCREENPIPE_API_TOKEN="$token"
        mkdir -p /root/.screenpipe
        if [ -f /root/.screenpipe/.env ] && grep -q '^SCREENPIPE_API_TOKEN=' /root/.screenpipe/.env 2>/dev/null; then
            sed -i "s|^SCREENPIPE_API_TOKEN=.*|SCREENPIPE_API_TOKEN=${token}|" /root/.screenpipe/.env
        else
            printf 'SCREENPIPE_API_TOKEN=%s\n' "$token" >> /root/.screenpipe/.env
        fi
        echo "Screenpipe API token ready (auto-generated)"
        return 0
    fi

    echo "WARN: Could not auto-fetch Screenpipe API token — retry on next restart"
}

if [ "${SCREENPIPE_ENABLED:-true}" = "true" ]; then
    ensure_screenpipe_api_token
fi

exec "$@"
