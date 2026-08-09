"""Aba Pedidos: listagem, detalhe e timeline unificada."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.deps import CtxDep, DbDep
from app.core.errors import NaoEncontrado
from app.models.finance import Payment, Reconciliation
from app.models.order import Order, OrderEvent, OrderItem, Shipment, ShipmentEvent
from app.models.support import Claim, Message

router = APIRouter(prefix="/orders", tags=["Pedidos"])


@router.get("", summary="Lista pedidos com filtros combináveis")
async def listar(
    ctx: CtxDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
    account_id: int | None = None,
    status: str | None = None,
    logistic_type: str | None = None,
    state: str | None = None,
    busca: str | None = Query(None, description="ID do pedido, SKU ou título"),
    limite: int = Query(50, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    consulta = select(Order).where(Order.tenant_id == ctx.tenant_id)
    contagem = select(func.count(Order.id)).where(Order.tenant_id == ctx.tenant_id)

    condicoes = []
    if inicio:
        condicoes.append(Order.date_created >= inicio)
    if fim:
        condicoes.append(Order.date_created <= fim)
    if channel:
        condicoes.append(Order.channel == channel)
    if account_id:
        condicoes.append(Order.channel_account_id == account_id)
    if status:
        condicoes.append(Order.status == status)
    if logistic_type:
        condicoes.append(Order.logistic_type == logistic_type)
    if state:
        condicoes.append(Order.ship_state == state)

    for condicao in condicoes:
        consulta = consulta.where(condicao)
        contagem = contagem.where(condicao)

    if busca:
        termo = f"%{busca.strip()}%"
        # Busca por ID do pedido ou por SKU/título de qualquer item dele.
        sub = select(OrderItem.order_id).where(
            or_(OrderItem.sku_channel.ilike(termo),
                OrderItem.sku_base.ilike(termo),
                OrderItem.title.ilike(termo))
        )
        filtro_busca = or_(Order.external_id.ilike(termo), Order.id.in_(sub))
        consulta = consulta.where(filtro_busca)
        contagem = contagem.where(filtro_busca)

    total = await db.scalar(contagem) or 0
    resultado = await db.execute(
        consulta.options(selectinload(Order.items))
        .order_by(Order.date_created.desc())
        .limit(limite)
        .offset(offset)
    )

    return {
        "itens": [_resumo(p) for p in resultado.scalars()],
        "total": int(total),
        "limite": limite,
        "offset": offset,
    }


@router.get("/{order_id}", summary="Detalhe completo com timeline unificada")
async def detalhe(order_id: int, ctx: CtxDep, db: DbDep) -> dict[str, Any]:
    """Detalhe do pedido.

    A timeline une eventos do pedido, do envio, mensagens e reclamações numa
    sequência cronológica única — que é como a pessoa que atende o cliente
    precisa enxergar, e não como quatro listas separadas.
    """
    pedido = await db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id, Order.tenant_id == ctx.tenant_id)
    )
    if pedido is None:
        raise NaoEncontrado("Pedido não encontrado.")

    envios = list(
        (await db.execute(select(Shipment).where(Shipment.order_id == pedido.id))).scalars()
    )
    pagamentos = list(
        (
            await db.execute(
                select(Payment)
                .options(selectinload(Payment.fees))
                .where(Payment.order_id == pedido.id)
            )
        ).scalars()
    )
    conciliacao = await db.scalar(
        select(Reconciliation).where(Reconciliation.order_id == pedido.id)
    )

    return {
        **_resumo(pedido),
        "status_detail": pedido.status_detail,
        "date_closed": pedido.date_closed,
        "buyer_nickname": pedido.buyer_nickname,
        "ship_city": pedido.ship_city,
        "external_pack_id": pedido.external_pack_id,
        "financeiro": {
            "gross_amount": str(pedido.gross_amount),
            "shipping_revenue": str(pedido.shipping_revenue),
            "shipping_cost": str(pedido.shipping_cost),
            "platform_fee": str(pedido.platform_fee),
            "payment_fee": str(pedido.payment_fee),
            "discount_amount": str(pedido.discount_amount),
            "refund_amount": str(pedido.refund_amount),
            "tax_amount": str(pedido.tax_amount),
            "net_amount": str(pedido.net_amount),
            "net_source": pedido.net_source,
            "cogs": str(pedido.cogs),
            "margem": str(pedido.net_amount - pedido.cogs),
        },
        "itens": [
            {
                "id": i.id,
                "sku_channel": i.sku_channel,
                "sku_base": i.sku_base,
                "title": i.title,
                "variation_name": i.variation_name,
                "quantity": str(i.quantity),
                "unit_price": str(i.unit_price),
                "gross_amount": str(i.gross_amount),
                "platform_fee": str(i.platform_fee),
                "unit_cost": str(i.unit_cost),
                "cogs": str(i.cogs),
                "sem_custo": i.unit_cost == 0,
            }
            for i in pedido.items
        ],
        "envios": [
            {
                "id": e.id,
                "external_id": e.external_id,
                "status": e.status,
                "tracking_number": e.tracking_number,
                "carrier": e.carrier,
                "logistic_type": e.logistic_type,
                "date_shipped": e.date_shipped,
                "date_delivered": e.date_delivered,
                "estimated_delivery": e.estimated_delivery,
                "delay_days": e.delay_days,
                "cost_seller": str(e.cost_seller),
            }
            for e in envios
        ],
        "pagamentos": [
            {
                "id": p.id,
                "external_id": p.external_id,
                "provider": p.provider,
                "status": p.status,
                "payment_method": p.payment_method,
                "installments": p.installments,
                "transaction_amount": str(p.transaction_amount),
                "net_received_amount": str(p.net_received_amount),
                "money_release_date": p.money_release_date,
                "money_release_status": p.money_release_status,
                "taxas": [
                    {"tipo": t.fee_type, "tipo_original": t.fee_type_raw, "valor": str(t.amount)}
                    for t in p.fees
                ],
            }
            for p in pagamentos
        ],
        "conciliacao": {
            "status": conciliacao.status,
            "expected_net": str(conciliacao.expected_net),
            "settled_net": str(conciliacao.settled_net),
            "divergence": str(conciliacao.divergence),
            "notes": conciliacao.notes,
        }
        if conciliacao
        else None,
        "timeline": await _timeline(db, pedido, envios),
    }


async def _timeline(db: DbDep, pedido: Order, envios: list[Shipment]) -> list[dict[str, Any]]:
    linha: list[dict[str, Any]] = []

    for evento in (
        await db.execute(select(OrderEvent).where(OrderEvent.order_id == pedido.id))
    ).scalars():
        linha.append(
            {
                "tipo": "pedido",
                "evento": evento.event_type,
                "descricao": evento.description
                or (
                    f"{evento.from_status or '—'} → {evento.to_status}"
                    if evento.to_status
                    else ""
                ),
                "origem": evento.source,
                "ocorrido_em": evento.occurred_at,
            }
        )

    for envio in envios:
        for evento in (
            await db.execute(
                select(ShipmentEvent).where(ShipmentEvent.shipment_id == envio.id)
            )
        ).scalars():
            linha.append(
                {
                    "tipo": "envio",
                    "evento": evento.status,
                    "descricao": evento.description,
                    "origem": "tracking",
                    "ocorrido_em": evento.occurred_at,
                }
            )

    for msg in (
        await db.execute(select(Message).where(Message.order_id == pedido.id))
    ).scalars():
        linha.append(
            {
                "tipo": "mensagem",
                "evento": f"mensagem_{msg.from_role}",
                "descricao": msg.text[:300],
                "origem": "chat",
                "ocorrido_em": msg.sent_at,
            }
        )

    for claim in (
        await db.execute(select(Claim).where(Claim.order_id == pedido.id))
    ).scalars():
        linha.append(
            {
                "tipo": "reclamacao",
                "evento": claim.type,
                "descricao": f"{claim.status} — {claim.reason_text or claim.reason_code}",
                "origem": "pos_venda",
                "ocorrido_em": claim.opened_at,
            }
        )

    linha.sort(key=lambda e: (e["ocorrido_em"] is None, e["ocorrido_em"]))
    return linha


def _resumo(pedido: Order) -> dict[str, Any]:
    return {
        "id": pedido.id,
        "external_id": pedido.external_id,
        "channel": pedido.channel,
        "channel_account_id": pedido.channel_account_id,
        "status": pedido.status,
        "status_raw": pedido.status_raw,
        "date_created": pedido.date_created,
        "gross_amount": str(pedido.gross_amount),
        "net_amount": str(pedido.net_amount),
        "net_source": pedido.net_source,
        "platform_fee": str(pedido.platform_fee),
        "payment_fee": str(pedido.payment_fee),
        "shipping_cost": str(pedido.shipping_cost),
        "logistic_type": pedido.logistic_type,
        "ship_state": pedido.ship_state,
        "buyer_nickname": pedido.buyer_nickname,
        "itens_count": len(pedido.items) if pedido.items is not None else 0,
        "titulo": pedido.items[0].title if pedido.items else "",
    }
