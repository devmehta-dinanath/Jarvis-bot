#!/bin/sh
set -e
echo "Jarvis server mode: APP_ROLE=${APP_ROLE:-server}"
mkdir -p /app/data /app/media
# Start the API first. A blocking refresh leaves port 8000 closed and Cloudflare
# reports an invalid origin response. The release marker still runs this once.
if [ "${WHATSAPP_REPROCESS_ON_DEPLOY:-false}" = "true" ]; then
  echo "WhatsApp deploy refresh: release=${WHATSAPP_REPROCESS_RELEASE:-unset} dry_run=${WHATSAPP_REPROCESS_DRY_RUN:-false} (background)"
  (
    sleep 15
    python -m app.services.whatsapp.deploy_refresh || echo "WhatsApp deploy refresh failed; server keeps running"
  ) >/proc/1/fd/1 2>/proc/1/fd/2 &
fi
exec "$@"
