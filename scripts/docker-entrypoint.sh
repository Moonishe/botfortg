#!/bin/bash
set -e
echo "=== TelegramHelper Docker Entrypoint ==="

# ── Chromium first-run install (cached in mounted volume) ──
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/app/data/playwright-browsers}"
# Use find to safely check for any chromium-* directory with a chrome binary.
# Avoids fragile [ -d glob ] which breaks with multiple matches.
CHROMIUM_COUNT=$(find "$PLAYWRIGHT_BROWSERS_PATH" -maxdepth 2 -type d -name "chromium-*" 2>/dev/null | head -1 | wc -l)
if [ "$CHROMIUM_COUNT" -eq 0 ]; then
    echo "Chromium not found in $PLAYWRIGHT_BROWSERS_PATH — installing..."
    playwright install chromium
    echo "Chromium installed."
else
    echo "Chromium found — skipping install."
fi

# ── Alembic migrations + DB init handled by main.py ──
echo "Starting application..."
exec "$@"
