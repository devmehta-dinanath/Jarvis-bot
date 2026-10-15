#!/bin/sh
set -e
echo "Jarvis server mode: APP_ROLE=${APP_ROLE:-server}"
mkdir -p /app/data /app/media
# One-shot, before uvicorn. Default off. A release marker stops a second run.
if [ "${WHATSAPP_REPROCESS_ON_DEPLOY:-false}" = "true" ]; then
  echo "WhatsApp deploy refresh: release=${WHATSAPP_REPROCESS_RELEASE:-unset} dry_run=${WHATSAPP_REPROCESS_DRY_RUN:-false}"
  python -m app.services.whatsapp.deploy_refresh || echo "WhatsApp deploy refresh failed; server will still start"
fi
exec "$@"
