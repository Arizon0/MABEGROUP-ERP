"""Ingestão: persiste dados canônicos de forma idempotente.

Ponto de convergência das três camadas de atualização (webhook, polling e
reconciliação diária). Todas chamam as mesmas funções daqui, e todas produzem o
mesmo estado final — é essa propriedade que torna a redundância barata em vez de
perigosa: processar o mesmo pedido três vezes é inofensivo.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    CanonicalListing,
    CanonicalOrder,
    CanonicalPayment,
    CanonicalCampaign,
    CanonicalClaim,
    CanonicalQuestion,
    CanonicalShipment,
)
from app.core.crypto import hash_comprador
from app.events import bus
from app.models.catalog import Listing, ListingVariation, Product, SkuLink, SkuPendency
from app.models.channel import ChannelAccount
from app.models.enums import StatusPedido
from app.models.finance import Payment, PaymentFee, Refund
from app.models.order import Order, OrderEvent, OrderItem, Shipment, ShipmentEvent
from app.models.marketing import Campaign, CampaignItem
from app.models.support import Claim, Question
from app.services import finance

log = structlog.get_logger(__name__)
ZERO = Decimal("0")


class ResultadoIngestao:
    """Contagem do que entrou, para log e para a resposta da sincronização."""

    def __init__(self) -> None:
        self.criados = 0
        self.atualizados = 0
        self.ignorados = 0
        self.pendencias_sku = 0

    def como_dict(self) -> dict[str, int]:
        return {
            "criados": self.criados,
            "atualizados": self.atualizados,
            "ignorados": self.ignorados,
            "pendencias_sku": self.pendencias_sku,
        }


# --- Pedidos -----------------------------------------------------------------

async def salvar_pedido(
    db: AsyncSession,
    conta: ChannelAccount,
    canonico: CanonicalOrder,
    *,
    publicar_evento: bool = True,
) -> tuple[Order, bool]:
    """UPSERT de um pedido pela chave natural ``(conta, external_id)``.

    Devolve ``(pedido, criado)``. A idempotência vem da chave natural, não de
    controle na aplicação: reentrega de webhook e varredura de polling convergem
    no mesmo registro.
    """
    existente = await db.scalar(
        select(Order).where(
            Order.channel_account_id == conta.id, Order.external_id == canonico.external_id
        )
    )

    bruto = finance.calcular_bruto(canonico)
    liquido, fonte = finance.calcular_liquido(canonico)
    criado = existente is None
    status_anterior = existente.status if existente else ""

    pedido = existente or Order(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=canonico.external_id,
    )

    pedido.external_pack_id = canonico.external_pack_id
    pedido.status = canonico.status
    pedido.status_raw = canonico.status_raw
    pedido.status_detail = canonico.status_detail
    pedido.date_created = _aware(canonico.date_created)
    pedido.date_closed = _aware(canonico.date_closed)
    pedido.date_last_updated = _aware(canonico.date_last_updated)
    pedido.currency = canonico.currency
    pedido.gross_amount = bruto
    pedido.shipping_revenue = canonico.shipping_revenue
    pedido.shipping_cost = canonico.shipping_cost
    pedido.platform_fee = canonico.platform_fee
    pedido.payment_fee = canonico.payment_fee
    pedido.discount_amount = canonico.discount_amount
    pedido.refund_amount = canonico.refund_amount
    pedido.tax_amount = canonico.tax_amount
    pedido.net_amount = liquido
    pedido.net_source = fonte
    pedido.buyer_hash = hash_comprador(canonico.buyer_external_id)
    pedido.buyer_nickname = canonico.buyer_nickname
    pedido.ship_state = canonico.ship_state
    pedido.ship_city = canonico.ship_city
    pedido.logistic_type = str(canonico.logistic_type or "")
    pedido.has_multiple_items = len(canonico.items) > 1
    pedido.raw = canonico.raw or {}

    if criado:
        db.add(pedido)
    await db.flush()

    cmv = await _sincronizar_itens(db, conta, pedido, canonico)
    pedido.cogs = cmv
    await db.flush()

    # Timeline: só registra transição real de status, para não poluir com
    # ruído de re-sincronizações que não mudaram nada.
    if criado or status_anterior != pedido.status:
        db.add(
            OrderEvent(
                tenant_id=conta.tenant_id,
                order_id=pedido.id,
                event_type="order.created" if criado else "order.status_changed",
                from_status=status_anterior,
                to_status=pedido.status,
                source="sync",
                occurred_at=datetime.now(UTC),
            )
        )

    if publicar_evento:
        tipo = (
            bus.TipoEvento.PEDIDO_CRIADO
            if criado
            else (
                bus.TipoEvento.PEDIDO_CANCELADO
                if pedido.status == StatusPedido.CANCELADO
                else bus.TipoEvento.PEDIDO_ATUALIZADO
            )
        )
        await bus.publicar(
            tipo,
            conta.tenant_id,
            {
                "order_id": pedido.id,
                "external_id": pedido.external_id,
                "status": pedido.status,
                "gross_amount": pedido.gross_amount,
                "net_amount": pedido.net_amount,
                "items": len(canonico.items),
                "ship_state": pedido.ship_state,
                "title": canonico.items[0].title if canonico.items else "",
            },
            channel=conta.channel,
            account_id=conta.id,
        )

    return pedido, criado


async def _sincronizar_itens(
    db: AsyncSession, conta: ChannelAccount, pedido: Order, canonico: CanonicalOrder
) -> Decimal:
    """Regrava os itens do pedido e devolve o CMV total.

    Substitui em vez de fazer merge: o marketplace pode remover um item numa
    edição de pedido, e um merge deixaria o item fantasma somando receita.
    """
    anteriores = await db.execute(select(OrderItem).where(OrderItem.order_id == pedido.id))
    # Preserva o custo já congelado: o custo do produto pode ter mudado desde a
    # primeira ingestão, e a margem histórica não pode se reescrever sozinha.
    custos_congelados = {
        (i.external_item_id, i.sku_channel): (i.unit_cost, i.product_id)
        for i in anteriores.scalars()
    }
    for item in (await db.execute(select(OrderItem).where(OrderItem.order_id == pedido.id))).scalars():
        await db.delete(item)
    await db.flush()

    cmv_total = ZERO
    for linha in canonico.items:
        chave = (linha.external_item_id, linha.sku_channel)
        custo_anterior, produto_anterior = custos_congelados.get(chave, (None, None))

        produto_id = produto_anterior
        sku_base = None
        if produto_id is None:
            produto_id, sku_base = await resolver_sku(
                db, conta, linha.sku_channel, linha.title
            )
        else:
            produto = await db.get(Product, produto_id)
            sku_base = produto.sku if produto else None

        if custo_anterior is not None:
            custo = custo_anterior
        elif produto_id:
            produto = await db.get(Product, produto_id)
            # Embalagem entra no custo unitário: em item de baixo valor, ela é
            # a diferença entre margem positiva e negativa.
            custo = (
                (produto.unit_cost + produto.packaging_cost) if produto else ZERO
            )
        else:
            custo = ZERO

        cmv_linha = (custo * linha.quantity).quantize(Decimal("0.0001"))
        cmv_total += cmv_linha

        db.add(
            OrderItem(
                tenant_id=conta.tenant_id,
                order_id=pedido.id,
                external_item_id=linha.external_item_id,
                product_id=produto_id,
                sku_channel=linha.sku_channel,
                sku_base=sku_base,
                title=linha.title,
                variation_name=linha.variation_name,
                quantity=linha.quantity,
                unit_price=linha.unit_price,
                gross_amount=linha.gross_amount,
                platform_fee=linha.platform_fee,
                discount_amount=linha.discount_amount,
                unit_cost=custo,
                cogs=cmv_linha,
            )
        )

    await db.flush()
    return cmv_total


async def resolver_sku(
    db: AsyncSession, conta: ChannelAccount, sku_canal: str, titulo: str = ""
) -> tuple[int | None, str | None]:
    """Resolve ``sku_channel`` → produto interno pelo de-para.

    Sem mapeamento, registra pendência e segue. **Nunca bloqueia a ingestão**: o
    dinheiro precisa entrar no sistema mesmo sem o de-para; o que fica
    indisponível é apenas a margem daquele item — e isso é sinalizado ao usuário
    em vez de virar um silencioso custo zero (que exibiria 100% de margem).
    """
    if not sku_canal:
        return None, None

    vinculo = await db.scalar(
        select(SkuLink).where(
            SkuLink.tenant_id == conta.tenant_id,
            SkuLink.channel == conta.channel,
            SkuLink.sku_channel == sku_canal,
        )
    )
    if vinculo:
        produto = await db.get(Product, vinculo.product_id)
        return vinculo.product_id, (produto.sku if produto else None)

    # Correspondência direta pelo próprio código, que cobre a maioria dos casos.
    produto = await db.scalar(
        select(Product).where(Product.tenant_id == conta.tenant_id, Product.sku == sku_canal)
    )
    if produto:
        db.add(
            SkuLink(
                tenant_id=conta.tenant_id,
                channel=conta.channel,
                sku_channel=sku_canal,
                product_id=produto.id,
                confidence="auto_exact",
            )
        )
        await db.flush()
        return produto.id, produto.sku

    pendencia = await db.scalar(
        select(SkuPendency).where(
            SkuPendency.tenant_id == conta.tenant_id,
            SkuPendency.channel == conta.channel,
            SkuPendency.sku_channel == sku_canal,
        )
    )
    if pendencia:
        pendencia.occurrences += 1
        pendencia.last_seen_at = datetime.now(UTC)
    else:
        db.add(
            SkuPendency(
                tenant_id=conta.tenant_id,
                channel=conta.channel,
                sku_channel=sku_canal,
                sample_title=titulo[:300],
                last_seen_at=datetime.now(UTC),
            )
        )
    await db.flush()
    return None, None


# --- Envios ------------------------------------------------------------------

async def salvar_envio(
    db: AsyncSession, conta: ChannelAccount, canonico: CanonicalShipment
) -> Shipment:
    """UPSERT de envio, com cálculo de atraso e vínculo ao pedido."""
    envio = await db.scalar(
        select(Shipment).where(
            Shipment.channel_account_id == conta.id, Shipment.external_id == canonico.external_id
        )
    )
    criado = envio is None
    envio = envio or Shipment(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=canonico.external_id,
    )

    if canonico.external_order_id:
        pedido = await db.scalar(
            select(Order).where(
                Order.channel_account_id == conta.id,
                Order.external_id == canonico.external_order_id,
            )
        )
        if pedido:
            envio.order_id = pedido.id

    envio.status = canonico.status
    envio.status_raw = canonico.status_raw
    envio.substatus = canonico.substatus
    envio.tracking_number = canonico.tracking_number
    envio.carrier = canonico.carrier
    envio.logistic_type = canonico.logistic_type
    envio.date_shipped = _aware(canonico.date_shipped)
    envio.date_delivered = _aware(canonico.date_delivered)
    envio.estimated_delivery = _aware(canonico.estimated_delivery)
    envio.cost_seller = canonico.cost_seller
    envio.cost_buyer = canonico.cost_buyer
    envio.receiver_state = canonico.receiver_state
    envio.receiver_city = canonico.receiver_city
    envio.delay_days = _calcular_atraso(envio)
    envio.raw = canonico.raw or {}

    if criado:
        db.add(envio)
    await db.flush()

    for evento in canonico.events or []:
        quando = _aware(evento.get("occurred_at"))
        if not quando:
            continue
        ja_existe = await db.scalar(
            select(ShipmentEvent).where(
                ShipmentEvent.shipment_id == envio.id,
                ShipmentEvent.occurred_at == quando,
                ShipmentEvent.status == str(evento.get("status") or ""),
            )
        )
        if not ja_existe:
            db.add(
                ShipmentEvent(
                    tenant_id=conta.tenant_id,
                    shipment_id=envio.id,
                    status=str(evento.get("status") or ""),
                    description=str(evento.get("description") or ""),
                    occurred_at=quando,
                )
            )

    # Repassa o custo real do frete para o pedido: sem isso o líquido fica
    # superestimado, porque o frete só aparece na chamada de custos do envio.
    if envio.order_id and envio.cost_seller:
        pedido = await db.get(Order, envio.order_id)
        if pedido and pedido.shipping_cost != envio.cost_seller:
            pedido.shipping_cost = envio.cost_seller
            liquido, fonte = finance.calcular_liquido(
                _pedido_para_canonico(pedido)
            )
            if pedido.net_source == "computed":
                pedido.net_amount = liquido

    await db.flush()
    return envio


def _calcular_atraso(envio: Shipment) -> int:
    if not envio.estimated_delivery:
        return 0
    referencia = envio.date_delivered or datetime.now(UTC)
    prevista = _aware(envio.estimated_delivery)
    if not prevista:
        return 0
    dias = (referencia - prevista).days
    return max(0, dias)


# --- Pagamentos --------------------------------------------------------------

async def salvar_pagamento(
    db: AsyncSession, conta: ChannelAccount, canonico: CanonicalPayment
) -> Payment:
    """UPSERT de pagamento com suas taxas detalhadas e reembolsos."""
    pagamento = await db.scalar(
        select(Payment).where(
            Payment.channel_account_id == conta.id, Payment.external_id == canonico.external_id
        )
    )
    criado = pagamento is None
    pagamento = pagamento or Payment(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        external_id=canonico.external_id,
        provider=canonico.provider,
    )

    if canonico.external_order_id:
        pedido = await db.scalar(
            select(Order).where(
                Order.channel_account_id == conta.id,
                Order.external_id == canonico.external_order_id,
            )
        )
        if pedido:
            pagamento.order_id = pedido.id

    pagamento.status = canonico.status
    pagamento.status_raw = canonico.status_raw
    pagamento.status_detail = canonico.status_detail
    pagamento.payment_method = canonico.payment_method
    pagamento.installments = canonico.installments
    pagamento.currency = canonico.currency
    pagamento.transaction_amount = canonico.transaction_amount
    pagamento.total_paid_amount = canonico.total_paid_amount
    pagamento.shipping_amount = canonico.shipping_amount
    pagamento.taxes_amount = canonico.taxes_amount
    pagamento.fee_amount = canonico.fee_total
    pagamento.net_received_amount = canonico.net_received_amount
    pagamento.date_approved = _aware(canonico.date_approved)
    pagamento.money_release_date = _aware(canonico.money_release_date)
    pagamento.money_release_status = canonico.money_release_status
    pagamento.raw = canonico.raw or {}

    if criado:
        db.add(pagamento)
    await db.flush()

    for taxa in (await db.execute(select(PaymentFee).where(PaymentFee.payment_id == pagamento.id))).scalars():
        await db.delete(taxa)
    for taxa in canonico.fees:
        db.add(
            PaymentFee(
                tenant_id=conta.tenant_id,
                payment_id=pagamento.id,
                fee_type=taxa.fee_type,
                fee_type_raw=taxa.fee_type_raw,
                amount=taxa.amount,
                payer=taxa.payer,
            )
        )

    for reembolso in canonico.refunds or []:
        externo = str(reembolso.get("id") or "")
        if externo and not await db.scalar(
            select(Refund).where(Refund.payment_id == pagamento.id, Refund.external_id == externo)
        ):
            db.add(
                Refund(
                    tenant_id=conta.tenant_id,
                    payment_id=pagamento.id,
                    external_id=externo,
                    amount=Decimal(str(reembolso.get("amount") or 0)),
                    reason=str(reembolso.get("reason") or ""),
                    status=str(reembolso.get("status") or ""),
                    date_created=_aware(reembolso.get("date_created")) or datetime.now(UTC),
                )
            )

    await db.flush()

    # O líquido oficial do provedor tem precedência sobre a estimativa — mas
    # somando TODOS os pagamentos do pedido, não substituindo pelo último.
    # Pedido parcelado em dois pagamentos, ou com pagamento complementar de
    # frete, teria o líquido truncado ao valor de um só deles.
    if pagamento.order_id and canonico.net_received_amount > ZERO:
        pedido = await db.get(Order, pagamento.order_id)
        if pedido and pedido.status != StatusPedido.CANCELADO:
            await _consolidar_financeiro_do_pedido(db, pedido)
            await bus.publicar(
                bus.TipoEvento.PAGAMENTO_APROVADO,
                conta.tenant_id,
                {
                    "order_id": pedido.id,
                    "payment_id": pagamento.id,
                    "net_amount": pedido.net_amount,
                    "release_date": pagamento.money_release_date,
                },
                channel=conta.channel,
                account_id=conta.id,
            )

    await db.flush()
    return pagamento


async def _consolidar_financeiro_do_pedido(db: AsyncSession, pedido: Order) -> None:
    """Recalcula o financeiro do pedido a partir de todos os seus pagamentos.

    Duas proteções importantes aqui:

    * **Soma** os pagamentos aprovados em vez de sobrescrever com o último —
      pedido parcelado ou com pagamento complementar de frete teria o líquido
      truncado.
    * **Rejeita valor implausível.** Um líquido maior que o bruto mais o frete
      só acontece por pagamento associado ao pedido errado. Aceitar esse número
      produziria margem acima de 100% no painel — o tipo de erro que destrói a
      confiança do usuário em todos os outros números da tela.
    """
    from sqlalchemy import func

    pagamentos = list(
        (
            await db.execute(
                select(Payment).where(
                    Payment.order_id == pedido.id,
                    Payment.status.in_(["approved", "authorized"]),
                )
            )
        ).scalars()
    )
    if not pagamentos:
        return

    liquido = sum((Decimal(str(p.net_received_amount or 0)) for p in pagamentos), ZERO)
    if liquido <= ZERO:
        return

    teto = Decimal(str(pedido.gross_amount or 0)) + Decimal(str(pedido.shipping_revenue or 0))
    if teto > ZERO and liquido > teto:
        log.warning(
            "liquido_implausivel_ignorado",
            pedido=pedido.id,
            externo=pedido.external_id,
            liquido=str(liquido),
            teto=str(teto),
        )
        return

    total_taxas = await db.scalar(
        select(func.coalesce(func.sum(PaymentFee.amount), 0))
        .join(Payment, Payment.id == PaymentFee.payment_id)
        .where(
            Payment.order_id == pedido.id,
            PaymentFee.fee_type != "marketplace_fee",
        )
    )

    pedido.net_amount = liquido
    pedido.payment_fee = Decimal(str(total_taxas or 0))
    # `settled` só quando todo o dinheiro foi efetivamente liberado.
    pedido.net_source = (
        "settled"
        if all(p.money_release_status == "released" for p in pagamentos)
        else "api_reported"
    )


# --- Catálogo ----------------------------------------------------------------

async def salvar_anuncio(
    db: AsyncSession, conta: ChannelAccount, canonico: CanonicalListing
) -> Listing:
    """UPSERT de anúncio e suas variações."""
    anuncio = await db.scalar(
        select(Listing).where(
            Listing.channel_account_id == conta.id, Listing.external_id == canonico.external_id
        )
    )
    criado = anuncio is None
    anuncio = anuncio or Listing(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=canonico.external_id,
    )

    anuncio.title = canonico.title
    anuncio.status = canonico.status
    anuncio.listing_type = canonico.listing_type
    anuncio.category_id = canonico.category_id
    anuncio.sku_channel = canonico.sku_channel
    anuncio.price = canonico.price
    anuncio.available_quantity = canonico.available_quantity
    anuncio.sold_quantity = canonico.sold_quantity
    anuncio.permalink = canonico.permalink
    anuncio.thumbnail = canonico.thumbnail
    anuncio.logistic_type = canonico.logistic_type
    anuncio.health = canonico.health
    anuncio.visits_30d = canonico.visits_30d
    anuncio.raw = canonico.raw or {}

    if canonico.sku_channel:
        produto_id, _ = await resolver_sku(db, conta, canonico.sku_channel, canonico.title)
        anuncio.product_id = produto_id

    if criado:
        db.add(anuncio)
    await db.flush()

    for variacao in canonico.variations:
        existente = await db.scalar(
            select(ListingVariation).where(
                ListingVariation.listing_id == anuncio.id,
                ListingVariation.external_variation_id == variacao.external_variation_id,
            )
        )
        alvo = existente or ListingVariation(
            tenant_id=conta.tenant_id,
            listing_id=anuncio.id,
            external_variation_id=variacao.external_variation_id,
        )
        alvo.sku_channel = variacao.sku_channel
        alvo.name = variacao.name
        alvo.attributes = variacao.attributes
        alvo.price = variacao.price
        alvo.available_quantity = variacao.available_quantity
        if existente is None:
            db.add(alvo)

    await db.flush()
    return anuncio


async def salvar_pergunta(
    db: AsyncSession, conta: ChannelAccount, canonico: CanonicalQuestion
) -> Question:
    """UPSERT de pergunta, calculando o tempo de resposta."""
    pergunta = await db.scalar(
        select(Question).where(
            Question.channel_account_id == conta.id, Question.external_id == canonico.external_id
        )
    )
    criado = pergunta is None
    pergunta = pergunta or Question(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=canonico.external_id,
        date_created=_aware(canonico.date_created) or datetime.now(UTC),
    )

    pergunta.text = canonico.text
    pergunta.answer_text = canonico.answer_text
    pergunta.status = canonico.status
    pergunta.external_listing_id = canonico.external_listing_id
    pergunta.asker_hash = hash_comprador(canonico.asker_external_id)
    pergunta.date_answered = _aware(canonico.date_answered)
    pergunta.raw = canonico.raw or {}

    if pergunta.date_answered and pergunta.date_created:
        pergunta.response_time_seconds = int(
            (pergunta.date_answered - _aware(pergunta.date_created)).total_seconds()
        )

    if criado:
        db.add(pergunta)
        await db.flush()
        if pergunta.status == "unanswered":
            await bus.publicar(
                bus.TipoEvento.PERGUNTA_RECEBIDA,
                conta.tenant_id,
                {"question_id": pergunta.id, "text": pergunta.text[:160]},
                channel=conta.channel,
                account_id=conta.id,
            )
    await db.flush()
    return pergunta


async def salvar_reclamacao(
    db: AsyncSession, conta: ChannelAccount, canonico: CanonicalClaim
) -> Claim:
    """UPSERT de reclamação, mediação ou devolução."""
    reclamacao = await db.scalar(
        select(Claim).where(
            Claim.channel_account_id == conta.id, Claim.external_id == canonico.external_id
        )
    )
    criado = reclamacao is None
    reclamacao = reclamacao or Claim(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=canonico.external_id,
        opened_at=_aware(canonico.opened_at) or datetime.now(UTC),
    )

    if canonico.external_order_id:
        pedido = await db.scalar(
            select(Order).where(
                Order.channel_account_id == conta.id,
                Order.external_id == canonico.external_order_id,
            )
        )
        if pedido:
            reclamacao.order_id = pedido.id

    reclamacao.type = canonico.type
    reclamacao.stage = canonico.stage
    reclamacao.status = canonico.status
    reclamacao.reason_code = canonico.reason_code
    reclamacao.reason_text = canonico.reason_text
    reclamacao.resolution = canonico.resolution
    reclamacao.amount_involved = canonico.amount_involved
    reclamacao.closed_at = _aware(canonico.closed_at)
    reclamacao.raw = canonico.raw or {}

    if criado:
        db.add(reclamacao)
        await db.flush()
        await bus.publicar(
            bus.TipoEvento.RECLAMACAO_ABERTA,
            conta.tenant_id,
            {
                "claim_id": reclamacao.id,
                "order_id": reclamacao.order_id,
                "type": reclamacao.type,
                "status": reclamacao.status,
            },
            channel=conta.channel,
            account_id=conta.id,
        )
    await db.flush()
    return reclamacao


async def salvar_campanha(
    db: AsyncSession, conta: ChannelAccount, canonico: CanonicalCampaign
) -> Campaign:
    """UPSERT de campanha e dos anúncios participantes."""
    campanha = await db.scalar(
        select(Campaign).where(
            Campaign.channel_account_id == conta.id,
            Campaign.external_id == canonico.external_id,
        )
    )
    criado = campanha is None
    campanha = campanha or Campaign(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=canonico.external_id,
    )

    campanha.name = canonico.name
    campanha.type = canonico.type
    campanha.status = canonico.status
    campanha.start_at = _aware(canonico.start_at)
    campanha.end_at = _aware(canonico.end_at)
    campanha.budget = canonico.budget
    campanha.raw = canonico.raw or {}

    if criado:
        db.add(campanha)
    await db.flush()

    # Os itens são regravados: o vendedor adiciona e remove produtos de uma
    # promoção em andamento, e um merge deixaria participante fantasma.
    for antigo in (
        await db.execute(select(CampaignItem).where(CampaignItem.campaign_id == campanha.id))
    ).scalars():
        await db.delete(antigo)
    await db.flush()

    for item in canonico.items or []:
        externo = str(item.get("item_id") or item.get("id") or "")
        if not externo:
            continue
        anuncio = await db.scalar(
            select(Listing).where(
                Listing.channel_account_id == conta.id, Listing.external_id == externo
            )
        )
        db.add(
            CampaignItem(
                tenant_id=conta.tenant_id,
                campaign_id=campanha.id,
                listing_id=anuncio.id if anuncio else None,
                external_listing_id=externo,
                original_price=Decimal(str(item.get("original_price") or 0)),
                promo_price=Decimal(str(item.get("promotion_price") or item.get("promo_price") or 0)),
                stock_limit=int(item.get("promotion_stock") or item.get("stock") or 0),
            )
        )

    await db.flush()
    return campanha


# --- Auxiliares --------------------------------------------------------------

def _aware(valor: Any) -> datetime | None:
    """Garante ``datetime`` com fuso.

    O SQLite devolve datas sem ``tzinfo``; comparar uma dessas com um
    ``datetime.now(UTC)`` levanta ``TypeError`` em tempo de execução. Normalizar
    aqui evita esse erro espalhado por dezenas de comparações.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    return None


def _pedido_para_canonico(pedido: Order) -> CanonicalOrder:
    """Adapta um pedido persistido para reaproveitar o cálculo financeiro."""
    return CanonicalOrder(
        external_id=pedido.external_id,
        channel=pedido.channel,
        date_created=pedido.date_created,
        status=pedido.status,
        gross_amount=pedido.gross_amount,
        shipping_revenue=pedido.shipping_revenue,
        shipping_cost=pedido.shipping_cost,
        platform_fee=pedido.platform_fee,
        payment_fee=pedido.payment_fee,
        discount_amount=pedido.discount_amount,
        refund_amount=pedido.refund_amount,
        tax_amount=pedido.tax_amount,
    )
