"""Abas Logística, Atendimento/Reputação e Marketing."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import AnalistaDep, CtxDep, DbDep
from app.core.errors import NaoEncontrado
from app.services import audit
from app.models.enums import StatusEnvio, StatusPedido
from app.models.marketing import Campaign, CampaignItem
from app.models.metrics import MetricSnapshot
from app.models.order import Order, OrderItem, Shipment
from app.models.support import Claim, Question, Review
from app.services.finance import arredondar

router = APIRouter(tags=["Operação"])


# =========================== LOGÍSTICA =======================================

@router.get("/logistics/overview", summary="Panorama logístico")
async def logistica(ctx: CtxDep, db: DbDep, dias: int = Query(30, le=180)) -> dict[str, Any]:
    desde = datetime.now(UTC) - timedelta(days=dias)

    por_status = (
        await db.execute(
            select(Shipment.status, func.count(Shipment.id))
            .where(Shipment.tenant_id == ctx.tenant_id, Shipment.created_at >= desde)
            .group_by(Shipment.status)
        )
    ).all()

    por_canal_logistico = (
        await db.execute(
            select(Order.logistic_type, func.count(Order.id), func.sum(Order.shipping_cost))
            .where(
                Order.tenant_id == ctx.tenant_id,
                Order.date_created >= desde,
                Order.status != StatusPedido.CANCELADO,
            )
            .group_by(Order.logistic_type)
        )
    ).all()

    # Prazo real por estado: o que permite ajustar a promessa de entrega por região.
    prazos = (
        await db.execute(
            select(
                Shipment.receiver_state,
                func.count(Shipment.id),
                func.avg(Shipment.delay_days),
            )
            .where(
                Shipment.tenant_id == ctx.tenant_id,
                Shipment.status == StatusEnvio.ENTREGUE,
                Shipment.date_delivered.is_not(None),
                Shipment.receiver_state != "",
            )
            .group_by(Shipment.receiver_state)
        )
    ).all()

    return {
        "periodo_dias": dias,
        "por_status": [{"status": str(s), "quantidade": int(q or 0)} for s, q in por_status],
        "por_canal_logistico": [
            {
                "canal": str(canal or "—"),
                "pedidos": int(qtd or 0),
                "custo_frete": str(arredondar(Decimal(str(custo or 0)))),
                "custo_medio": str(arredondar(Decimal(str(custo or 0)) / qtd)) if qtd else "0.00",
            }
            for canal, qtd, custo in por_canal_logistico
        ],
        "prazo_por_estado": [
            {
                "estado": str(uf),
                "entregas": int(qtd or 0),
                "atraso_medio_dias": str(round(float(atraso or 0), 1)),
            }
            for uf, qtd, atraso in prazos
        ],
    }


@router.get("/logistics/delayed", summary="Envios em atraso")
async def atrasados(ctx: CtxDep, db: DbDep, limite: int = Query(100, le=500)) -> list[dict[str, Any]]:
    """Envios que passaram do prazo prometido sem entrega confirmada.

    Ordenados pelo maior atraso: é a fila de trabalho de quem cuida da operação,
    não um relatório para leitura passiva.
    """
    agora = datetime.now(UTC)
    resultado = await db.execute(
        select(Shipment)
        .where(
            Shipment.tenant_id == ctx.tenant_id,
            Shipment.estimated_delivery.is_not(None),
            Shipment.estimated_delivery < agora,
            Shipment.status.notin_([StatusEnvio.ENTREGUE, StatusEnvio.CANCELADO]),
        )
        .order_by(Shipment.estimated_delivery)
        .limit(limite)
    )
    envios = list(resultado.scalars())
    return [
        {
            "id": e.id,
            "order_id": e.order_id,
            "external_id": e.external_id,
            "channel": e.channel,
            "status": e.status,
            "tracking_number": e.tracking_number,
            "carrier": e.carrier,
            "estimated_delivery": e.estimated_delivery,
            "dias_de_atraso": (agora - _aware(e.estimated_delivery)).days,
            "destino": f"{e.receiver_city}/{e.receiver_state}".strip("/"),
        }
        for e in envios
    ]


# =========================== ATENDIMENTO =====================================

@router.get("/support/overview", summary="Indicadores de atendimento e reputação")
async def atendimento(ctx: CtxDep, db: DbDep, dias: int = Query(30, le=180)) -> dict[str, Any]:
    """Indicadores de atendimento.

    O tempo de primeira resposta é fator de ranqueamento no Mercado Livre, o que
    torna esse número operacional — não apenas informativo.
    """
    desde = datetime.now(UTC) - timedelta(days=dias)

    perguntas = (
        await db.execute(
            select(
                func.count(Question.id),
                func.count(Question.id).filter(Question.status == "unanswered"),
                func.avg(Question.response_time_seconds),
            ).where(Question.tenant_id == ctx.tenant_id, Question.date_created >= desde)
        )
    ).one()

    reclamacoes = (
        await db.execute(
            select(Claim.status, func.count(Claim.id))
            .where(Claim.tenant_id == ctx.tenant_id, Claim.opened_at >= desde)
            .group_by(Claim.status)
        )
    ).all()

    avaliacoes = (
        await db.execute(
            select(Review.rating, func.count(Review.id))
            .where(Review.tenant_id == ctx.tenant_id, Review.date_created >= desde)
            .group_by(Review.rating)
        )
    ).all()

    pedidos_periodo = await db.scalar(
        select(func.count(Order.id)).where(
            Order.tenant_id == ctx.tenant_id, Order.date_created >= desde
        )
    )
    total_reclamacoes = sum(int(q or 0) for _, q in reclamacoes)
    tempo_medio = float(perguntas[2] or 0)

    return {
        "periodo_dias": dias,
        "perguntas": {
            "total": int(perguntas[0] or 0),
            "nao_respondidas": int(perguntas[1] or 0),
            "tempo_medio_resposta_min": round(tempo_medio / 60, 1) if tempo_medio else None,
        },
        "reclamacoes": {
            "total": total_reclamacoes,
            "por_status": [{"status": str(s), "quantidade": int(q or 0)} for s, q in reclamacoes],
            "taxa_pct": str(
                arredondar(Decimal(total_reclamacoes) / Decimal(pedidos_periodo) * 100)
            )
            if pedidos_periodo
            else "0.00",
        },
        "avaliacoes": {
            "distribuicao": [{"nota": int(n or 0), "quantidade": int(q or 0)} for n, q in avaliacoes],
            "media": str(
                arredondar(
                    Decimal(sum(int(n or 0) * int(q or 0) for n, q in avaliacoes))
                    / Decimal(sum(int(q or 0) for _, q in avaliacoes))
                )
            )
            if avaliacoes and sum(int(q or 0) for _, q in avaliacoes)
            else None,
        },
    }


@router.get("/support/questions", summary="Perguntas, priorizadas pelas não respondidas")
async def perguntas(
    ctx: CtxDep, db: DbDep, apenas_pendentes: bool = True, limite: int = Query(100, le=500)
) -> list[dict[str, Any]]:
    consulta = select(Question).where(Question.tenant_id == ctx.tenant_id)
    if apenas_pendentes:
        consulta = consulta.where(Question.status == "unanswered")

    resultado = await db.execute(consulta.order_by(Question.date_created.desc()).limit(limite))
    agora = datetime.now(UTC)
    return [
        {
            "id": p.id,
            "external_id": p.external_id,
            "channel": p.channel,
            "text": p.text,
            "answer_text": p.answer_text,
            "status": p.status,
            "external_listing_id": p.external_listing_id,
            "date_created": p.date_created,
            "horas_aguardando": round(
                (agora - _aware(p.date_created)).total_seconds() / 3600, 1
            )
            if p.status == "unanswered"
            else None,
        }
        for p in resultado.scalars()
    ]


@router.get(
    "/support/reputation-history",
    summary="Evolução da reputação",
    description=(
        "Construída a partir das fotografias diárias em `metrics_snapshots`. As "
        "APIs só devolvem o estado atual — sem essa captura, o histórico não "
        "existiria em lugar nenhum."
    ),
)
async def reputacao(ctx: CtxDep, db: DbDep, dias: int = Query(90, le=365)) -> list[dict[str, Any]]:
    desde = (datetime.now(UTC) - timedelta(days=dias)).date()
    resultado = await db.execute(
        select(MetricSnapshot)
        .where(MetricSnapshot.tenant_id == ctx.tenant_id, MetricSnapshot.day >= desde)
        .order_by(MetricSnapshot.day)
    )
    return [
        {
            "day": s.day,
            "channel_account_id": s.channel_account_id,
            "metric": s.metric,
            "value_text": s.value_text,
            "value_num": str(s.value_num) if s.value_num is not None else None,
        }
        for s in resultado.scalars()
    ]


# =========================== MARKETING =======================================

@router.get(
    "/marketing/campaigns",
    summary="Campanhas com rentabilidade estimada",
    description=(
        "Responde 'essa promoção deu lucro?'. Quando a Ads API não está liberada "
        "(caso da Shopee sem whitelist), o custo de mídia entra pelo campo de "
        "lançamento manual."
    ),
)
async def campanhas(ctx: CtxDep, db: DbDep, dias: int = Query(90, le=365)) -> list[dict[str, Any]]:
    desde = datetime.now(UTC) - timedelta(days=dias)
    resultado = await db.execute(
        select(Campaign)
        .where(Campaign.tenant_id == ctx.tenant_id)
        .order_by(Campaign.start_at.desc().nullslast())
    )
    campanhas_lista = list(resultado.scalars())

    saida = []
    for campanha in campanhas_lista:
        anuncios = list(
            (
                await db.execute(
                    select(CampaignItem.listing_id).where(CampaignItem.campaign_id == campanha.id)
                )
            ).scalars()
        )

        receita = Decimal("0")
        pedidos = 0
        if anuncios:
            linha = (
                await db.execute(
                    select(
                        func.coalesce(func.sum(OrderItem.gross_amount), 0),
                        func.count(func.distinct(OrderItem.order_id)),
                    )
                    .join(Order, Order.id == OrderItem.order_id)
                    .where(
                        OrderItem.tenant_id == ctx.tenant_id,
                        OrderItem.listing_id.in_(anuncios),
                        Order.date_created >= (campanha.start_at or desde),
                        Order.date_created <= (campanha.end_at or datetime.now(UTC)),
                        Order.status != StatusPedido.CANCELADO,
                    )
                )
            ).one()
            receita = Decimal(str(linha[0] or 0))
            pedidos = int(linha[1] or 0)

        custo_midia = Decimal(str(campanha.manual_media_cost or 0))
        saida.append(
            {
                "id": campanha.id,
                "external_id": campanha.external_id,
                "channel": campanha.channel,
                "name": campanha.name,
                "type": campanha.type,
                "status": campanha.status,
                "start_at": campanha.start_at,
                "end_at": campanha.end_at,
                "itens": len(anuncios),
                "receita_gerada": str(arredondar(receita)),
                "pedidos": pedidos,
                "custo_midia": str(arredondar(custo_midia)),
                "roas": str(arredondar(receita / custo_midia)) if custo_midia else None,
                "resultado": str(arredondar(receita - custo_midia)),
            }
        )
    return saida


class CustoMidiaIn(BaseModel):
    manual_media_cost: Decimal = Field(ge=0)


@router.patch(
    "/marketing/campaigns/{campanha_id}",
    summary="Lança o custo de mídia da campanha",
    description=(
        "A Ads API da Shopee exige whitelist separada e o Mercado Livre não "
        "expõe custo por campanha. Sem este lançamento manual, a rentabilidade "
        "da campanha ficaria estruturalmente incompleta."
    ),
)
async def lancar_custo_midia(
    campanha_id: int, dados: CustoMidiaIn, ctx: AnalistaDep, db: DbDep
) -> dict[str, Any]:
    campanha = await db.scalar(
        select(Campaign).where(
            Campaign.id == campanha_id, Campaign.tenant_id == ctx.tenant_id
        )
    )
    if campanha is None:
        raise NaoEncontrado("Campanha não encontrada.")

    antes = str(campanha.manual_media_cost)
    campanha.manual_media_cost = dados.manual_media_cost

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="campaign.media_cost_updated",
        entity_type="campaign",
        entity_id=campanha_id,
        before={"manual_media_cost": antes},
        after={"manual_media_cost": str(dados.manual_media_cost)},
    )
    await db.commit()
    return {
        "id": campanha.id,
        "name": campanha.name,
        "manual_media_cost": str(campanha.manual_media_cost),
    }


def _aware(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
