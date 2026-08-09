"""Aba Visão Geral e Relatórios: KPIs, séries e rankings."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import CtxDep, DbDep
from app.schemas.common import FiltroPeriodo
from app.services import analytics

router = APIRouter(prefix="/dashboard", tags=["Painel e análises"])


def _filtro(ctx: CtxDep, params: Annotated[FiltroPeriodo, Depends()]) -> analytics.Filtro:
    inicio, fim = analytics.normalizar_periodo(params.inicio, params.fim)
    return analytics.Filtro(
        tenant_id=ctx.tenant_id,
        inicio=inicio,
        fim=fim,
        channel=params.channel,
        account_id=params.account_id,
        status=params.status,
        logistic_type=params.logistic_type,
        state=params.state,
        sku=params.sku,
    )


FiltroDep = Annotated[analytics.Filtro, Depends(_filtro)]


@router.get("/overview", summary="KPIs com comparação contra o período anterior")
async def overview(db: DbDep, filtro: FiltroDep) -> dict:
    return await analytics.visao_geral(db, filtro)


@router.get("/timeseries", summary="Série temporal de receita e pedidos")
async def timeseries(
    db: DbDep,
    filtro: FiltroDep,
    granularidade: str = Query("day", pattern="^(hour|day|month)$"),
) -> list[dict]:
    return await analytics.serie_temporal(db, filtro, granularidade)


@router.get("/channels", summary="Comparativo entre marketplaces")
async def por_canal(db: DbDep, filtro: FiltroDep) -> list[dict]:
    return await analytics.por_canal(db, filtro)


@router.get(
    "/products",
    summary="Ranking de produtos consolidado por SKU base",
    description=(
        "Une o mesmo produto vendido em vários anúncios e canais numa linha só — "
        "visão que nenhum painel nativo consegue oferecer, porque nenhum enxerga "
        "os outros marketplaces."
    ),
)
async def ranking_produtos(
    db: DbDep, filtro: FiltroDep, limite: int = Query(20, le=200)
) -> list[dict]:
    return await analytics.ranking_produtos(db, filtro, limite=limite)


@router.get("/geo", summary="Distribuição por estado de destino")
async def geografia(db: DbDep, filtro: FiltroDep) -> list[dict]:
    return await analytics.por_estado(db, filtro)


@router.get("/heatmap", summary="Mapa de calor hora x dia da semana")
async def heatmap(db: DbDep, filtro: FiltroDep) -> list[dict]:
    return await analytics.mapa_de_calor(db, filtro)


@router.get("/compare", summary="Comparação entre dois períodos")
async def comparar(
    db: DbDep,
    ctx: CtxDep,
    inicio_a: datetime,
    fim_a: datetime,
    inicio_b: datetime,
    fim_b: datetime,
    channel: str | None = None,
) -> dict:
    """Compara dois intervalos arbitrários (mês atual × anterior, por exemplo)."""
    a_inicio, a_fim = analytics.normalizar_periodo(inicio_a, fim_a)
    b_inicio, b_fim = analytics.normalizar_periodo(inicio_b, fim_b)

    periodo_a = analytics.Filtro(
        tenant_id=ctx.tenant_id, inicio=a_inicio, fim=a_fim, channel=channel
    )
    periodo_b = analytics.Filtro(
        tenant_id=ctx.tenant_id, inicio=b_inicio, fim=b_fim, channel=channel
    )
    return {
        "periodo_a": await analytics.visao_geral(db, periodo_a),
        "periodo_b": await analytics.visao_geral(db, periodo_b),
    }
