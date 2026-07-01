#!/usr/bin/env bash
# Generate yesterday's daily summary on the Jarvis server (run from cron at night).
#
# Example crontab (1:00 AM server local time, after the day ends):
#   0 1 * * * /path/to/jarvis-bot-be/scripts/summary-nightly-cron.sh >> /var/log/jarvis-summary.log 2>&1
#
# Requires the server container to be up and SUMMARY_ENABLED=true with OPENAI_API_KEY set.

set -euo pipefail

JARVIS_URL="${JARVIS_SERVER_URL:-http://127.0.0.1:8000}"
LOG_PREFIX="[summary-cron $(date -Iseconds)]"

log() { echo "$LOG_PREFIX $*"; }

if ! curl -sf "${JARVIS_URL}/health" >/dev/null; then
  log "ERROR: server not healthy at ${JARVIS_URL}"
  exit 1
fi

response="$(curl -sf -X POST "${JARVIS_URL}/api/v1/summaries/generate" 2>&1)" || {
  log "ERROR: generate failed — ${response:-unknown}"
  exit 1
}

if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$response" | python3 - <<'PY'
import json, sys
d = json.load(sys.stdin)
print(
    f"OK id={d.get('id')} day={str(d.get('period_start', ''))[:10]} "
    f"chunks={d.get('chunk_count')} tokens={d.get('prompt_tokens', 0) + d.get('completion_tokens', 0)}"
)
PY
else
  log "OK summary generated"
fi
