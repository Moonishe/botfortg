#!/bin/bash
set -e
echo "=== TelegramHelper Docker Entrypoint ==="

# ── Pre-flight: check required env vars ──
REQUIRED_VARS=("BOT_TOKEN" "OWNER_TELEGRAM_ID" "ENCRYPTION_KEY")
MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        MISSING+=("$var")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "❌ ОШИБКА: отсутствуют обязательные переменные окружения:"
    for v in "${MISSING[@]}"; do
        echo "   • $v"
    done
    echo ""
    echo "📋 Что нужно сделать в Railway dashboard:"
    echo "   Settings → Variables → New Variable"
    echo "   • BOT_TOKEN      — токен бота от @BotFather"
    echo "   • OWNER_TELEGRAM_ID — твой Telegram ID (узнать у @userinfobot)"
    echo "   • ENCRYPTION_KEY   — любой секретный ключ (32+ символов)"
    echo ""
    echo "⏳ Ожидание 60 сек перед выходом (чтобы избежать crash-loop)..."
    sleep 60
    exit 1
fi

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
