# ─── Stage 1: Build Frontend ─────────────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
# Use `npm ci` not `npm install`: refuses to mutate the lockfile, errors
# on lockfile drift, and produces byte-identical installs across runs.
# This is the standard pattern for reproducible Docker builds and plays
# nicely with the layer cache above (only re-runs when the lockfile or
# package.json actually changes).
RUN npm ci --no-audit --no-fund
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

ARG GIT_SHA=unknown
RUN echo "${GIT_SHA}" > /app/VERSION

COPY app/ ./app/
COPY --from=frontend-build /frontend/dist ./frontend/dist/

# ─── Non-root User ───────────────────────────────────────────
# Defense-in-depth hardening: create a dedicated unprivileged user and
# hand ownership of /app + /app/data to it before switching. The data
# directory must be owned by the runtime user because AthenaScout writes
# per-library SQLite databases, the auth secret file (0600), the auth
# users DB, and settings.json into it. UID 1000 matches the default
# Docker / Unraid convention so host-mounted volumes stay consistent.
#
# MIGRATION NOTE for existing deployments: if your host-mounted data
# directory was created by a root-run container, you'll need to `chown
# -R 1000:1000 /path/to/data` on the host once to hand it over to the
# new runtime user. Fresh deployments need no action.
RUN mkdir -p /app/data && \
    useradd --create-home --uid 1000 athenascout && \
    chown -R athenascout:athenascout /app /app/data
USER athenascout

LABEL org.opencontainers.image.title="AthenaScout"
LABEL org.opencontainers.image.description="A self-hosted book library completionist tracker"
LABEL org.opencontainers.image.source="https://github.com/mnbaker117/AthenaScout"
LABEL org.opencontainers.image.url="https://github.com/mnbaker117/AthenaScout"
LABEL org.opencontainers.image.version="1.0.0"

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
