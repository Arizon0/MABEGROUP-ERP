"""Ingestão e processamento de notificações dos marketplaces.

Divisão de responsabilidade rígida:

* :func:`registrar` roda **dentro da requisição HTTP** e faz o mínimo absoluto —
  o Mercado Livre exige resposta em 500 ms e suspende aplicações lentas.
* :func:`processar` roda **no worker**, onde há tempo para buscar o detalhe na
  API, normalizar e persistir.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import obter_conector
from app.core.config import settings
from app.core.crypto import chave_idempotencia
from app.events import bus
from app.models.channel import ChannelAccount, WebhookEvent
from app.models.enums import Canal, StatusWebhook
from app.services import ingest, tokens

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RegistroWebhook:
    evento_id: int | None
    criado_agora: bool


async def registrar(
    db: AsyncSession,
    canal: str,
    corpo_cru: bytes,
    headers: dict[str, str],
    url: str = "",
) -> RegistroWebhook:
    """Persiste a notificação e devolve o controle imediatamente.

    Nenhuma chamada externa acontece aqui: buscar o detalhe do pedido custaria
    centenas de milissegundos e estouraria o SLA do Mercado Livre.
    """
    try:
        corpo = json.loads(corpo_cru or b"{}")
    except ValueError:
        corpo = {"_corpo_invalido": corpo_cru.decode("utf-8", errors="replace")[:2000]}

    conector = obter_conector(canal)
    assinatura_ok = conector.verify_signature(corpo_cru, headers, url)
    notificacao = conector.parse_webhook(corpo, headers)

    chave = chave_idempotencia(
        canal,
        notificacao.topic,
        notificacao.resource,
        notificacao.external_event_id,
        # Sem identificador de evento, a versão do recurso separa duas
        # notificações legítimas do mesmo pedido em momentos diferentes.
        str(corpo.get("_version") or corpo.get("timestamp") or ""),
    )

    evento = WebhookEvent(
        channel=canal,
        topic=notificacao.topic,
        resource=notificacao.resource,
        external_event_id=notificacao.external_event_id,
        idempotency_key=chave,
        payload=corpo,
        signature_valid=assinatura_ok,
        status=StatusWebhook.PENDENTE if assinatura_ok else StatusWebhook.FALHOU,
        received_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
        error="" if assinatura_ok else "Assinatura inválida — evento não processado.",
    )

    conta = None
    if notificacao.external_account_id:
        conta = await db.scalar(
            select(ChannelAccount).where(
                ChannelAccount.channel == canal,
                ChannelAccount.external_account_id == notificacao.external_account_id,
            )
        )
        if conta:
            evento.channel_account_id = conta.id
            evento.tenant_id = conta.tenant_id

    db.add(evento)
    try:
        await db.commit()
    except IntegrityError:
        # Reentrega: os três marketplaces reenviam notificações. O UNIQUE da
        # chave de idempotência transforma isso num no-op.
        await db.rollback()
        log.debug("webhook_duplicado_ignorado", canal=canal, chave=chave[:16])
        return RegistroWebhook(evento_id=None, criado_agora=False)

    return RegistroWebhook(evento_id=evento.id, criado_agora=assinatura_ok)


async def processar(db: AsyncSession, evento_id: int) -> bool:
    """Processa um evento pendente. Devolve ``True`` se concluído com sucesso."""
    evento = await db.get(WebhookEvent, evento_id)
    if evento is None or evento.status == StatusWebhook.CONCLUIDO:
        return True
    if not evento.signature_valid:
        return False

    evento.status = StatusWebhook.PROCESSANDO
    evento.attempts += 1
    await db.commit()

    try:
        await _despachar(db, evento)
        evento.status = StatusWebhook.CONCLUIDO
        evento.processed_at = datetime.now(UTC)
        evento.error = ""
        await db.commit()
        return True
    except Exception as exc:
        await db.rollback()
        evento = await db.get(WebhookEvent, evento_id)
        if evento is None:
            return False
        evento.error = str(exc)[:2000]
        if evento.attempts >= settings.WEBHOOK_MAX_ATTEMPTS:
            # Fim da linha: vai para a DLQ, consultável e reprocessável pela
            # tela de Configurações. Não some silenciosamente.
            evento.status = StatusWebhook.MORTO
            log.error(
                "webhook_esgotou_tentativas",
                evento=evento_id,
                canal=evento.channel,
                topico=evento.topic,
                erro=str(exc),
            )
        else:
            evento.status = StatusWebhook.PENDENTE
            evento.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=min(2**evento.attempts * 30, 3600)
            )
        await db.commit()
        return False


async def _despachar(db: AsyncSession, evento: WebhookEvent) -> None:
    """Roteia o evento para o handler do recurso correspondente."""
    conta = (
        await db.get(ChannelAccount, evento.channel_account_id)
        if evento.channel_account_id
        else None
    )
    if conta is None:
        # Notificação de conta que este tenant não conectou (ou já revogou).
        # Não é erro: só não temos o que fazer com ela.
        log.info("webhook_sem_conta_correspondente", canal=evento.channel, topico=evento.topic)
        return

    token = await tokens.obter_access_token(db, conta)
    conector = obter_conector(conta.channel)
    externo = _id_do_recurso(evento.resource)
    topico = evento.topic

    if topico in ("orders_v2", "orders", "order_status", "created_order"):
        await _processar_pedido(db, conta, conector, token, externo)
    elif topico in ("shipments", "tracking_number", "tracking_update"):
        envio = await conector.fetch_shipment(
            token, externo, shop_id=conta.external_account_id
        )
        if envio:
            await ingest.salvar_envio(db, conta, envio)
    elif topico in ("payments", "payment"):
        pagamento = await conector.fetch_payment(token, externo)
        if pagamento:
            await ingest.salvar_pagamento(db, conta, pagamento)
    elif topico == "questions":
        perguntas = await conector.fetch_questions(
            token, seller_id=conta.external_account_id
        )
        for pergunta in perguntas:
            await ingest.salvar_pergunta(db, conta, pergunta)
    elif topico == "items":
        anuncios = await conector.fetch_listings(
            token, seller_id=conta.external_account_id, shop_id=conta.external_account_id
        )
        for anuncio in anuncios:
            if anuncio.external_id == externo or not externo:
                await ingest.salvar_anuncio(db, conta, anuncio)
    elif topico == "shop_deauthorization":
        conta.status = "revoked"
        await bus.publicar(
            bus.TipoEvento.ALERTA,
            conta.tenant_id,
            {
                "severity": "critical",
                "title": "Autorização revogada",
                "message": f"A loja {conta.nickname} revogou o acesso do aplicativo.",
            },
            channel=conta.channel,
        )
    else:
        log.debug("webhook_topico_sem_handler", topico=topico, canal=evento.channel)

    await db.commit()


async def _processar_pedido(
    db: AsyncSession, conta: ChannelAccount, conector: Any, token: str, externo: str
) -> None:
    """Busca o pedido e seus recursos vinculados.

    O payload do webhook é só um ponteiro; o detalhe exige chamadas extras. Envio
    e pagamento são buscados na sequência porque é neles que estão o custo real
    do frete e o líquido — sem os dois, o pedido entra com valores incompletos.
    """
    pedido = await conector.fetch_order(token, externo, shop_id=conta.external_account_id)
    if pedido is None:
        return

    if pedido.external_shipment_id:
        try:
            envio = await conector.fetch_shipment(
                token, pedido.external_shipment_id, shop_id=conta.external_account_id
            )
            if envio:
                pedido.shipping_cost = envio.cost_seller
        except Exception as exc:
            log.debug("envio_indisponivel_no_webhook", pedido=externo, erro=str(exc))
            envio = None
    else:
        envio = None

    pagamentos = []
    for id_pagamento in pedido.external_payment_ids:
        try:
            pagamento = await conector.fetch_payment(token, id_pagamento)
            if pagamento:
                pagamentos.append(pagamento)
        except Exception as exc:
            log.debug("pagamento_indisponivel_no_webhook", pagamento=id_pagamento, erro=str(exc))

    if conta.channel == Canal.SHOPEE:
        try:
            escrow = await conector.fetch_escrow(
                token, pedido.external_id, shop_id=conta.external_account_id
            )
            if escrow and escrow.net_received_amount:
                pagamentos.append(escrow)
        except Exception:
            pass  # escrow só existe após a conclusão do pedido

    if pagamentos:
        from app.services import finance

        finance.aplicar_pagamentos(pedido, pagamentos)

    await ingest.salvar_pedido(db, conta, pedido)

    if envio:
        await ingest.salvar_envio(db, conta, envio)
    for pagamento in pagamentos:
        await ingest.salvar_pagamento(db, conta, pagamento)


def _id_do_recurso(recurso: str) -> str:
    """Extrai o identificador de ``/orders/2000012345`` → ``2000012345``."""
    if not recurso:
        return ""
    return recurso.rstrip("/").split("/")[-1]


async def pendentes(db: AsyncSession, limite: int = 100) -> list[int]:
    """IDs de eventos prontos para processar (respeitando o backoff)."""
    resultado = await db.execute(
        select(WebhookEvent.id)
        .where(
            WebhookEvent.status == StatusWebhook.PENDENTE,
            WebhookEvent.signature_valid.is_(True),
            (WebhookEvent.next_attempt_at.is_(None))
            | (WebhookEvent.next_attempt_at <= datetime.now(UTC)),
        )
        .order_by(WebhookEvent.received_at)
        .limit(limite)
    )
    return list(resultado.scalars())


async def reprocessar(db: AsyncSession, evento_id: int) -> bool:
    """Recoloca um evento morto na fila (ação manual pela interface)."""
    evento = await db.get(WebhookEvent, evento_id)
    if evento is None:
        return False
    evento.status = StatusWebhook.PENDENTE
    evento.attempts = 0
    evento.next_attempt_at = datetime.now(UTC)
    evento.error = ""
    await db.commit()
    return await processar(db, evento_id)
