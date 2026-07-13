#!/bin/sh
set -e
echo "Jarvis macOS client mode: APP_ROLE=${APP_ROLE:-client}"
echo "Screenpipe API: ${SCREENPIPE_API_URL:-http://host.docker.internal:3030}"
echo "Central server: ${JARVIS_SERVER_URL:-unset}"
mkdir -p /app/data /app/media
exec "$@"
