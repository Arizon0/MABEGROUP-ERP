# Marketplace Hub — imagem única para deploy de serviço só (Render/Railway/VPS).
# A API FastAPI serve também o painel compilado; migrations e o bootstrap de
# produção rodam no start do contêiner, antes do servidor subir. Com uma única
# réplica (plano gratuito) não há risco de migrations concorrentes — a razão
# de, no compose, elas serem um job separado.
#
#   docker build -t marketplace-hub .
#   docker run -p 8000:8000 -e DATABASE_URL=... -e ENVIRONMENT=production ... marketplace-hub

# ---------- Stage 1: build do painel ----------
FROM node:22-alpine AS painel
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# VITE_API_URL vazio => o painel chama a API na MESMA origem (/api/v1/...).
RUN VITE_API_URL="" npm run build

# ---------- Stage 2: dependências Python ----------
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------- Stage 3: runtime ----------
FROM python:3.11-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/frontend/dist

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash aplicacao

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app/backend
COPY --chown=aplicacao:aplicacao backend/ ./
COPY --from=painel --chown=aplicacao:aplicacao /app/frontend/dist /app/frontend/dist

USER aplicacao
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Migrations → bootstrap (organização + proprietário, idempotente) → servidor.
CMD ["sh", "-c", "alembic upgrade head && python -m app.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
