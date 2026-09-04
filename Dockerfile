# Build the React interface once, then serve it from FastAPI at the same origin.
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && python -m playwright install --with-deps chromium

COPY backend/ ./backend/
COPY docs/OFFICE_UAT_SETUP.md ./docs/OFFICE_UAT_SETUP.md
COPY --from=frontend-build /build/frontend/build ./frontend/build

ENV CASEFILE_APP_DIR=/var/data \
    CASEFILE_TEMPLATE_DIR=/app/backend/templates \
    CASEFILE_CUSTOM_TEMPLATE_DIR=/var/data/custom_templates \
    CASEFILE_FRONTEND_BUILD_DIR=/app/frontend/build \
    CASEFILE_SERVE_FRONTEND=true \
    CASEFILE_ENVIRONMENT=PRODUCTION \
    STORAGE_MODE=local
EXPOSE 10000
CMD ["sh", "-c", "uvicorn server:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000}"]
