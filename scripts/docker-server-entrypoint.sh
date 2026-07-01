#!/bin/sh
set -e
echo "Jarvis server mode: APP_ROLE=${APP_ROLE:-server}"
mkdir -p /app/data /app/media
exec "$@"
