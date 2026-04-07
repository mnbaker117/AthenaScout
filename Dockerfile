# ─── Stage 1: Build Frontend ─────────────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: Production Image ──────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend-build /frontend/dist ./frontend/dist/

RUN mkdir -p /app/data

LABEL org.opencontainers.image.title="AthenaScout"
LABEL org.opencontainers.image.description="A self-hosted book library completionist tracker"
LABEL org.opencontainers.image.source="https://github.com/mnbaker117/AthenaScout"
LABEL org.opencontainers.image.url="https://github.com/mnbaker117/AthenaScout"
LABEL org.opencontainers.image.version="2.0.0"

# ─── Library Discovery ───────────────────────────────────────
# CALIBRE_PATH is the primary multi-library discovery root. AthenaScout
# scans this directory (and its immediate subdirectories) for any folder
# containing a metadata.db, registering each as a separate library.
# Mount your Calibre library tree at /calibre to use the default.
#
# CALIBRE_EXTRA_PATHS is a comma-separated list of additional locations
# the user can pick from in the Settings UI. Each path listed here must
# also have a matching volume mount.
ENV CALIBRE_PATH=/calibre
ENV CALIBRE_EXTRA_PATHS=""

# ─── Sync / Scan Intervals ───────────────────────────────────
ENV SYNC_INTERVAL_MINUTES=60
ENV LOOKUP_INTERVAL_MINUTES=4320
ENV MAM_SCAN_INTERVAL_MINUTES=360

# ─── Data + Runtime ──────────────────────────────────────────
ENV DATA_DIR=/app/data
ENV WEBUI_PORT=8787
ENV VERBOSE_LOGGING="false"

# ─── Optional Integrations (overridden at run time) ──────────
ENV CALIBRE_WEB_URL=""
ENV CALIBRE_URL=""
# HARDCOVER_API_KEY, MAM_SESSION_ID, ATHENASCOUT_AUTH_SECRET are NOT set
# here on purpose — they should be supplied per-deployment via Docker
# secrets, env files, or compose. See SECURITY.md for the auth secret
# precedence rules (env var → file → in-memory fallback).

EXPOSE 8787

HEALTHCHECK --interval=120s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8787/api/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${WEBUI_PORT} --log-level warning"]
