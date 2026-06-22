# ============================================================================
# Stage 1: Builder — install deps + pip packages
# ============================================================================
FROM python:3.13-slim AS builder

# Build dependencies (needed for ctranslate2, torch, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc g++ make \
       ffmpeg \
       libgomp1 \
       ca-certificates \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python packages into /venv
COPY requirements.txt ./
RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install -r requirements.txt

# ============================================================================
# Stage 2: Runner — minimal production image
# ============================================================================
FROM python:3.13-slim AS runner

# Runtime only — no build tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       libgomp1 \
       ca-certificates \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/app/data/cache \
    HF_HOME=/app/data/cache/huggingface \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Copy virtual env from builder (all pip packages)
COPY --from=builder /venv /venv

# ── Playwright system deps (cached layer — rarely changes) ──
# System libraries for Chromium (~150MB). Installed BEFORE app code
# so Docker caches this layer independently of src/ changes.
ENV PLAYWRIGHT_BROWSERS_PATH=/app/data/playwright-browsers
RUN playwright install-deps chromium

# Copy application code (frequently changes — keep AFTER heavy layers)
COPY src/ ./src/
COPY main.py healthcheck.py ./
COPY alembic.ini .
COPY alembic/ alembic/

# Copy skills and docs if they exist (optional, may be empty)
COPY skills/ ./skills/
COPY docs/ ./docs/

# Copy entrypoint script
COPY docker-entrypoint.sh /entrypoint.sh

# Safety: ensure .env did NOT accidentally leak into the image
RUN test ! -f /app/.env || (echo "ERROR: .env in image!" && exit 1)

# data — mounted as volume (DB, sessions, qdrant, media, model cache)
RUN mkdir -p /app/data \
    && useradd -m appuser \
    && chmod +x /entrypoint.sh \
    && chown -R appuser:appuser /app

# NOTE: Chromium browser (~300MB) is installed at first run by entrypoint.sh
# into PLAYWRIGHT_BROWSERS_PATH (mounted volume), keeping the image slim.

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python healthcheck.py || exit 1

STOPSIGNAL SIGTERM

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
