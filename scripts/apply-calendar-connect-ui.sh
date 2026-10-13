#!/usr/bin/env bash
# Fix root-owned settings-page.js: always show Connect Google Calendar.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/settings-page.calendar-connect-fix.js"
DEST="$(cd "$(dirname "$0")/../../Jarvis-bot-frontend" && pwd)/src/pages/settings-page.js"
if [[ ! -f "$SRC" ]]; then
  echo "Missing fixed file: $SRC" >&2
  exit 1
fi
cp "$SRC" "$DEST"
chown "$(logname):staff" "$DEST" 2>/dev/null || chown "$SUDO_USER:staff" "$DEST"
echo "Updated $DEST"
grep -n "You can still try Connect" "$DEST"
