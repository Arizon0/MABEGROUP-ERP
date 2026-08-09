"""Painel ao vivo: stream SSE e séries de curtíssimo prazo.

SSE em vez de WebSocket porque o tráfego é unidirecional (servidor → cliente).
SSE roda sobre HTTP comum, atravessa proxy corporativo, reconecta sozinho e sabe
retomar pelo cabeçalho ``Last-Event-ID``. WebSocket exigiria upgrade de
protocolo, sessão fixa no balanceador e heartbeat manual — o dobro de
complexidade para o mesmo resultado.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.deps import CtxDep, DbDep
from app.events import bus
from app.models.enums import StatusPedido
from app.models.order import Order
from app.services import analytics

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/live", tags=["Painel ao vivo"])

#: Sem tráfego, proxies derrubam a conexão ociosa. O comentário SSE mantém o
#: canal aberto sem poluir o fluxo de eventos do cliente.
INTERVALO_HEARTBEAT = 15


@router.get(
    "/stream",
    summary="Stream SSE de eventos em tempo real",
    response_description="text/event-stream com eventos nomeados por tipo",
)
async def stream(ctx: CtxDep) -> StreamingResponse:
    """Abre o canal de eventos do tenant.

    O navegador não permite cabeçalhos personalizados em ``EventSource``, então
    o token vai por query string — tratado em ``core/deps.py``.
    """

    async def gerar() -> AsyncGenerator[str, None]:
        yield _sse(
            "connected",
            {"tenant_id": ctx.tenant_id, "at": datetime.now(UTC).isoformat()},
        )

        fila: asyncio.Queue[str] = asyncio.Queue(maxsize=200)

        async def consumir() -> None:
            try:
                async for evento in bus.obter_barramento().assinar(ctx.tenant_id):
                    try:
                        fila.put_nowait(_sse(evento.type, json.loads(evento.to_json())))
                    except asyncio.QueueFull:
                        pass  # cliente lento: descarta em vez de travar o barramento
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("assinatura_sse_encerrada", erro=str(exc))

        tarefa = asyncio.create_task(consumir())
        try:
            while True:
                try:
                    yield await asyncio.wait_for(fila.get(), timeout=INTERVALO_HEARTBEAT)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            tarefa.cancel()

    return StreamingResponse(
        gerar(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Desliga o buffer do nginx: sem isso o proxy segura os eventos e o
            # "tempo real" chega em blocos de vários segundos.
            "X-Accel-Buffering": "no",
        },
    )


def _sse(evento: str, dados: dict) -> str:
    return f"event: {evento}\ndata: {json.dumps(dados, ensure_ascii=False, default=str)}\n\n"


@router.get("/feed", summary="Últimos pedidos (carga inicial do feed)")
async def feed(ctx: CtxDep, db: DbDep, limite: int = Query(30, le=100)) -> list[dict]:
    """Preenche o feed no primeiro carregamento.

    O SSE só entrega o que acontecer daqui para frente; sem esta carga inicial,
    a tela abriria vazia até a próxima venda — que pode levar horas.
    """
    resultado = await db.execute(
        select(Order)
        .where(Order.tenant_id == ctx.tenant_id)
        .order_by(Order.date_created.desc())
        .limit(limite)
    )
    return [
        {
            "id": p.id,
            "type": "order.created",
            "channel": p.channel,
            "external_id": p.external_id,
            "status": p.status,
            "gross_amount": str(p.gross_amount),
            "net_amount": str(p.net_amount),
            "ship_state": p.ship_state,
            "occurred_at": p.date_created,
            "title": p.items[0].title if p.items else "",
        }
        for p in resultado.scalars()
    ]


@router.get("/pulse", summary="Contadores do dia e volume por minuto")
async def pulse(ctx: CtxDep, db: DbDep) -> dict:
    """Números do cabeçalho do painel ao vivo.

    Usa o índice parcial de 7 dias (``ix_orders_live``), o que mantém a resposta
    em milissegundos mesmo com milhões de pedidos históricos na tabela.
    """
    inicio_dia = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    ultima_hora = datetime.now(UTC) - timedelta(hours=1)

    hoje = (
        await db.execute(
            select(
                func.count(Order.id).filter(Order.status != StatusPedido.CANCELADO),
                func.coalesce(
                    func.sum(Order.gross_amount).filter(Order.status != StatusPedido.CANCELADO),
                    0,
                ),
                func.coalesce(
                    func.sum(Order.net_amount).filter(Order.status != StatusPedido.CANCELADO), 0
                ),
            ).where(Order.tenant_id == ctx.tenant_id, Order.date_created >= inicio_dia)
        )
    ).one()

    na_hora = await db.scalar(
        select(func.count(Order.id)).where(
            Order.tenant_id == ctx.tenant_id, Order.date_created >= ultima_hora
        )
    )

    return {
        "hoje": {
            "pedidos": int(hoje[0] or 0),
            "receita_bruta": str(hoje[1] or 0),
            "receita_liquida": str(hoje[2] or 0),
        },
        "ultima_hora": {"pedidos": int(na_hora or 0)},
        "por_minuto": await analytics.volume_por_minuto(db, ctx.tenant_id, minutos=60),
        "agora": datetime.now(UTC),
    }
