"""Aplicação FastAPI do Marketplace Hub."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import registrar_tratadores
from app.core.logging import configurar_logging

log = structlog.get_logger(__name__)

DESCRICAO = """
API do **Marketplace Hub** — consolidação e análise de vendas de Mercado Livre,
Mercado Pago e Shopee a partir das APIs oficiais.

### Como autenticar
1. `POST /api/v1/auth/login` com e-mail e senha.
2. Envie `Authorization: Bearer <access_token>` nas demais chamadas.
3. `POST /api/v1/auth/refresh` rotaciona a sessão quando o token expira.

### Painel ao vivo
`GET /api/v1/live/stream` devolve um fluxo `text/event-stream`. Como o
`EventSource` do navegador não permite cabeçalhos personalizados, esse endpoint
também aceita o token por query string (`?token=...`).

### Webhooks
`POST /api/v1/webhooks/{mercadolivre|mercadopago|shopee}` são públicos e
protegidos por assinatura. Respondem sempre `200` — ver `docs/05-tempo-real.md`.
"""


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    configurar_logging()
    log.info(
        "aplicacao_iniciando",
        ambiente=settings.ENVIRONMENT,
        modo_simulado=settings.USE_MOCK_CONNECTORS,
        banco="sqlite" if settings.is_sqlite else "postgresql",
        barramento="redis" if settings.REDIS_URL else "memoria",
    )

    if not settings.is_production:
        # Em produção o schema é gerido por Alembic, como job separado antes do
        # deploy — nunca no startup de N réplicas simultâneas.
        from app.db.session import SessionLocal, criar_schema

        await criar_schema()
        if settings.SEED_ON_STARTUP:
            from app import seed

            async with SessionLocal() as db:
                try:
                    await seed.executar(db)
                except Exception as exc:
                    log.warning("seed_falhou", erro=str(exc))

    yield

    from app.db.session import engine
    from app.events.bus import obter_barramento

    await obter_barramento().fechar()
    await engine.dispose()
    log.info("aplicacao_encerrada")


app = FastAPI(
    title=settings.APP_NAME,
    description=DESCRICAO,
    version="1.0.0",
    lifespan=ciclo_de_vida,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

_libera_tudo = "*" in settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _libera_tudo else settings.cors_origins_list,
    # A autenticação é por Bearer no cabeçalho, não por cookie: com origem
    # curinga, `allow_credentials` não pode ser ativado (e não é necessário).
    allow_credentials=not _libera_tudo,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

registrar_tratadores(app)


@app.middleware("http")
async def correlacionar(request: Request, call_next) -> Response:
    """Injeta ``request_id`` no contexto de log e mede a duração.

    É o que permite reconstruir tudo que aconteceu numa requisição a partir de um
    único identificador — inclusive nas linhas emitidas lá no fundo dos serviços.
    """
    request_id = request.headers.get("x-request-id") or uuid4().hex[:16]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, caminho=request.url.path)

    inicio = time.monotonic()
    resposta = await call_next(request)
    duracao_ms = int((time.monotonic() - inicio) * 1000)

    resposta.headers["X-Request-ID"] = request_id
    resposta.headers["X-Response-Time-ms"] = str(duracao_ms)

    if not request.url.path.startswith(("/health", "/metrics")):
        log.info(
            "requisicao",
            metodo=request.method,
            status=resposta.status_code,
            ms=duracao_ms,
        )
    return resposta


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next) -> Response:
    resposta = await call_next(request)
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if settings.is_production:
        resposta.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return resposta


app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["Infraestrutura"], summary="Verificação de saúde")
async def health() -> dict:
    """Checagem usada pelo balanceador e pelo monitor de uptime.

    Inclui a defasagem de relógio porque a Shopee rejeita requisições com
    ``timestamp`` fora de ±5 minutos — e o sintoma disso é um erro de
    autenticação genérico, que leva horas para ser diagnosticado sem esta pista.
    """
    from datetime import UTC, datetime

    saude: dict = {
        "status": "ok",
        "ambiente": settings.ENVIRONMENT,
        "versao": app.version,
        "hora_servidor": datetime.now(UTC).isoformat(),
        "modo_simulado": settings.USE_MOCK_CONNECTORS,
    }

    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        saude["banco"] = "ok"
    except Exception as exc:
        saude["banco"] = f"erro: {exc}"
        saude["status"] = "degradado"

    if settings.REDIS_URL:
        try:
            import redis.asyncio as redis

            cliente = redis.from_url(settings.REDIS_URL)
            await cliente.ping()
            await cliente.aclose()
            saude["redis"] = "ok"
        except Exception as exc:
            saude["redis"] = f"erro: {exc}"
            saude["status"] = "degradado"
    else:
        saude["redis"] = "não configurado (barramento em memória)"

    return saude


@app.get("/metrics", tags=["Infraestrutura"], summary="Métricas Prometheus")
async def metrics() -> Response:
    if not settings.METRICS_ENABLED:
        return Response(status_code=404)
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/", tags=["Infraestrutura"], include_in_schema=False)
async def raiz() -> dict:
    return {
        "nome": settings.APP_NAME,
        "versao": app.version,
        "documentacao": "/docs",
        "api": settings.API_V1_PREFIX,
    }


# --- Painel servido pela própria API (deploy de serviço único) ---------------
# Em desenvolvimento o Vite roda à parte com proxy; em produção "um serviço só"
# a API entrega o build do painel. Só monta se o build existir — a API pura
# (testes, worker, compose com frontend separado) continua idêntica.


def _diretorio_do_painel():
    import os
    from pathlib import Path

    candidatos = []
    if os.getenv("STATIC_DIR"):
        candidatos.append(Path(os.environ["STATIC_DIR"]))
    aqui = Path(__file__).resolve()
    candidatos.append(aqui.parents[2] / "frontend" / "dist")  # raiz do repo
    candidatos.append(Path("/app/frontend/dist"))             # imagem Docker
    for c in candidatos:
        if c.is_dir() and (c / "index.html").is_file():
            return c
    return None


def _montar_painel(app: FastAPI) -> None:
    dist = _diretorio_do_painel()
    if dist is None:
        return

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{caminho:path}", include_in_schema=False)
    async def spa(caminho: str):
        # As rotas explícitas (API, docs, health) já foram registradas antes e
        # vencem esta. Se um /api/* inexistente cair aqui, é 404 — devolver o
        # index.html para uma chamada de API mascararia o erro real.
        if caminho.startswith(("api/", "docs", "redoc", "openapi.json", "health", "metrics")):
            raise HTTPException(status_code=404, detail="Não encontrado")
        arquivo = dist / caminho
        if caminho and arquivo.is_file():
            return FileResponse(str(arquivo))
        return FileResponse(str(dist / "index.html"))

    log.info("painel_montado", diretorio=str(dist))


_montar_painel(app)
