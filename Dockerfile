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

ENV CALIBRE_DB_PATH=/calibre/metadata.db
ENV CALIBRE_LIBRARY_PATH=/calibre
ENV SYNC_INTERVAL_MINUTES=60
ENV LOOKUP_INTERVAL_MINUTES=4320
ENV DATA_DIR=/app/data
ENV CALIBRE_WEB_URL=""
ENV CALIBRE_URL=""
ENV VERBOSE_LOGGING="false"
ENV WEBUI_PORT=8787

EXPOSE 8787

HEALTHCHECK --interval=120s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8787/api/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${WEBUI_PORT} --log-level warning --access-log"]
