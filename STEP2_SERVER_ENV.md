# STEP 2 server env (add to GCP `.env` then restart jarvis-bot-server)

WHATSAPP_MEETINGS_REMINDERS_ONLY=false
WHATSAPP_AI_DRAFTS_ENABLED=true
WAHA_WEBHOOK_URL=https://jarvis-api.lilium.co.in/api/v1/whatsapp/webhook
WAHA_ENSURE_WEBHOOK_EVENTS=true
# Optional once after deploy:
# WHATSAPP_HISTORY_BACKFILL_ON_START=true

# Or call after deploy:
# curl -X POST "$JARVIS/api/v1/whatsapp/inbox/ensure-waha-webhooks"
# curl -X POST "$JARVIS/api/v1/whatsapp/inbox/backfill-history"
