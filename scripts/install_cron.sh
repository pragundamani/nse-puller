#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_TAG="# nse-puller-daily"
CRON_LINE="15 19 * * 1-5 \"$PROJECT_DIR/scripts/daily_update.sh\" >> \"$PROJECT_DIR/out/logs/cron.log\" 2>&1 $CRON_TAG"
TMP_FILE="$(mktemp)"

trap 'rm -f "$TMP_FILE"' EXIT

mkdir -p "$PROJECT_DIR/out/logs"

if crontab -l 2>/dev/null > "$TMP_FILE"; then
  grep -F -v "$CRON_TAG" "$TMP_FILE" > "$TMP_FILE.filtered" || true
  mv "$TMP_FILE.filtered" "$TMP_FILE"
else
  : > "$TMP_FILE"
fi

printf '%s\n' "$CRON_LINE" >> "$TMP_FILE"
crontab "$TMP_FILE"
printf 'Installed cron entry:\n%s\n' "$CRON_LINE"
