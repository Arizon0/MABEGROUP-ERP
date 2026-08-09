"""Recepção de notificações dos marketplaces.

O caminho mais sensível a latência de todo o sistema. O Mercado Livre exige
resposta em **até 500 ms** e suspende o envio de notificações para aplicações
que não cumprem — o que afetaria todos os vendedores conectados, não só um.

Por isso estes endpoints fazem exatamente três coisas: validar assinatura,
persistir e enfileirar. Nenhuma chamada externa, nenhum processamento.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.enums import Canal

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


async def _receber(canal: str, request: Request) -> Response:
    corpo = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    url = str(request.url)

    async with SessionLocal() as db:
        registro = await webhooks_registrar(db, canal, corpo, headers, url)

    if registro.criado_agora and registro.evento_id:
        await _enfileirar(registro.evento_id)

    # Sempre 200 — inclusive em assinatura inválida. Devolver erro faria o
    # marketplace reenviar em backoff e, se persistisse, cortar as notificações.
    # E responder diferente para payload rejeitado daria a um atacante um
    # oráculo para descobrir o que o sistema aceita.
    return Response(status_code=status.HTTP_200_OK, content=b'{"ok":true}',
                    media_type="application/json")


async def webhooks_registrar(db, canal, corpo, headers, url):
    from app.services import webhooks as servico

    return await servico.registrar(db, canal, corpo, headers, url)


async def _enfileirar(evento_id: int) -> None:
    """Coloca o evento na fila; se a fila estiver fora, o cron o recupera."""
    if settings.REDIS_URL:
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            await pool.enqueue_job("processar_webhook", evento_id)
            await pool.aclose()
            return
        except Exception as exc:
            # Não é fatal: o evento está persistido como `pending` e o job
            # `drenar_webhooks` o processa na próxima varredura.
            log.warning("enfileirar_webhook_falhou", evento=evento_id, erro=str(exc))

    if not settings.REDIS_URL:
        import asyncio

        from app.services import webhooks as servico

        async def _processar() -> None:
            async with SessionLocal() as db:
                try:
                    await servico.processar(db, evento_id)
                except Exception as exc:
                    log.warning("processamento_inline_falhou", evento=evento_id, erro=str(exc))

        # Em desenvolvimento não há worker: processa fora do ciclo da resposta
        # para que o ACK continue rápido.
        asyncio.create_task(_processar())


@router.post(
    "/mercadolivre",
    status_code=status.HTTP_200_OK,
    summary="Notificações do Mercado Livre",
    description=(
        "Tópicos suportados: orders_v2, shipments, payments, items, questions, "
        "messages, post_purchase, invoices, stock-locations."
    ),
)
async def mercadolivre(request: Request) -> Response:
    return await _receber(Canal.MERCADO_LIVRE, request)


@router.post(
    "/mercadopago",
    status_code=status.HTTP_200_OK,
    summary="Notificações do Mercado Pago",
    description="Tópicos: payment, merchant_order, chargebacks. Valida `x-signature`.",
)
async def mercadopago(request: Request) -> Response:
    return await _receber(Canal.MERCADO_PAGO, request)


@router.post(
    "/shopee",
    status_code=status.HTTP_200_OK,
    summary="Push da Shopee",
    description=(
        "Códigos: 1 autorização, 2 desautorização, 3 status de pedido, "
        "4 rastreio, 9 promoção, 10 chat. Valida o header `Authorization`."
    ),
)
async def shopee(request: Request) -> Response:
    return await _receber(Canal.SHOPEE, request)
