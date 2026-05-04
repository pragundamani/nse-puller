#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PULLER_BIN="$PROJECT_DIR/.venv/bin/nse-puller"
SYMBOLS_FILE="$PROJECT_DIR/stocks.txt"
OUT_DIR="$PROJECT_DIR/out"

if [[ ! -x "$PULLER_BIN" ]]; then
  printf 'nse-puller is not installed in %s\n' "$PROJECT_DIR/.venv" >&2
  exit 1
fi

exec "$PULLER_BIN" \
  --symbols-file "$SYMBOLS_FILE" \
  --out-dir "$OUT_DIR" \
  --resume \
  --workers 1 \
  --requests-per-second 1.5
