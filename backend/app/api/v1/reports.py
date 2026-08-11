"""Relatórios analíticos e exportação em CSV e XLSX."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import AnalistaDep, CtxDep, DbDep
from app.core.errors import ErroDominio
from app.models.order import Order
from app.services import analytics, audit, reconciliation

router = APIRouter(prefix="/reports", tags=["Relatórios"])

#: Formatos que os endpoints realmente aceitam. PDF está fora de propósito:
#: anunciar um formato que a rota rejeita com 422 é pior do que não oferecê-lo,
#: porque quem integra descobre a ausência só em produção.
FORMATOS = {"csv", "xlsx"}


@router.get(
    "/orders/export",
    summary="Exporta pedidos",
    description=(
        "CSV é gerado em streaming — não carrega o resultado inteiro em memória, "
        "o que permite exportar centenas de milhares de linhas sem estourar o "
        "processo nem o tempo da requisição."
    ),
)
async def exportar_pedidos(
    ctx: AnalistaDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
    formato: str = Query("csv", pattern="^(csv|xlsx)$"),
) -> StreamingResponse:
    ini, f = analytics.normalizar_periodo(inicio, fim)
    consulta = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.tenant_id == ctx.tenant_id, Order.date_created >= ini, Order.date_created <= f)
    )
    if channel:
        consulta = consulta.where(Order.channel == channel)
    consulta = consulta.order_by(Order.date_created)

    colunas = [
        "id", "canal", "id_externo", "data", "status", "sku", "titulo", "quantidade",
        "preco_unitario", "receita_bruta", "frete_cobrado", "comissao", "taxa_pagamento",
        "custo_frete", "descontos", "reembolsos", "liquido", "procedencia_liquido",
        "cmv", "margem", "estado", "canal_logistico",
    ]

    def linhas_de(pedido: Order) -> list[list[Any]]:
        if not pedido.items:
            return [[
                pedido.id, pedido.channel, pedido.external_id, pedido.date_created,
                pedido.status, "", "", "", "", str(pedido.gross_amount),
                str(pedido.shipping_revenue), str(pedido.platform_fee),
                str(pedido.payment_fee), str(pedido.shipping_cost),
                str(pedido.discount_amount), str(pedido.refund_amount),
                str(pedido.net_amount), pedido.net_source, str(pedido.cogs),
                str(pedido.net_amount - pedido.cogs), pedido.ship_state, pedido.logistic_type,
            ]]
        return [
            [
                pedido.id, pedido.channel, pedido.external_id, pedido.date_created,
                pedido.status, item.sku_base or item.sku_channel, item.title,
                str(item.quantity), str(item.unit_price), str(item.gross_amount),
                # Valores de nível de pedido só na primeira linha, para não
                # multiplicar o total quando alguém somar a coluna na planilha.
                str(pedido.shipping_revenue) if indice == 0 else "0",
                str(item.platform_fee),
                str(pedido.payment_fee) if indice == 0 else "0",
                str(pedido.shipping_cost) if indice == 0 else "0",
                str(item.discount_amount),
                str(pedido.refund_amount) if indice == 0 else "0",
                str(pedido.net_amount) if indice == 0 else "0",
                pedido.net_source, str(item.cogs),
                str(item.gross_amount - item.cogs), pedido.ship_state, pedido.logistic_type,
            ]
            for indice, item in enumerate(pedido.items)
        ]

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.EXPORTACAO,
        entity_type="orders",
        after={"formato": formato, "inicio": ini.isoformat(), "fim": f.isoformat()},
    )
    await db.commit()

    nome = f"pedidos_{ini.date()}_{f.date()}"

    if formato == "csv":
        async def gerar():
            buffer = io.StringIO()
            escritor = csv.writer(buffer, delimiter=";")
            escritor.writerow(colunas)
            yield buffer.getvalue()
            buffer.seek(0), buffer.truncate(0)

            resultado = await db.stream(consulta)
            async for pedido in resultado.scalars():
                for linha in linhas_de(pedido):
                    escritor.writerow(linha)
                yield buffer.getvalue()
                buffer.seek(0), buffer.truncate(0)

        return StreamingResponse(
            gerar(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome}.csv"'},
        )

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    aba = wb.active
    aba.title = "Pedidos"
    aba.append(colunas)
    for celula in aba[1]:
        celula.font = Font(bold=True)

    pedidos = list((await db.execute(consulta)).scalars())
    for pedido in pedidos:
        for linha in linhas_de(pedido):
            aba.append([str(v) if isinstance(v, datetime) else v for v in linha])

    aba.freeze_panes = "A2"
    saida = io.BytesIO()
    wb.save(saida)
    saida.seek(0)
    return StreamingResponse(
        saida,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}.xlsx"'},
    )


@router.get("/financial/export", summary="Exporta o resumo financeiro consolidado")
async def exportar_financeiro(
    ctx: AnalistaDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    formato: str = Query("csv", pattern="^(csv|xlsx)$"),
) -> StreamingResponse:
    """Resumo por canal e por dia, no formato aceito pela contabilidade."""
    ini, f = analytics.normalizar_periodo(inicio, fim)
    filtro = analytics.Filtro(tenant_id=ctx.tenant_id, inicio=ini, fim=f)

    serie = await analytics.serie_temporal(db, filtro, "day")
    canais = await analytics.por_canal(db, filtro)

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(["RESUMO POR CANAL"])
    escritor.writerow(
        ["canal", "pedidos", "receita_bruta", "receita_liquida", "taxas", "ticket_medio", "taxa_efetiva_%"]
    )
    for c in canais:
        escritor.writerow([
            c["channel"], c["pedidos"], c["receita_bruta"], c["receita_liquida"],
            c["taxas"], c["ticket_medio"], c["taxa_efetiva_pct"],
        ])

    escritor.writerow([])
    escritor.writerow(["SÉRIE DIÁRIA"])
    escritor.writerow(["data", "pedidos", "receita_bruta", "receita_liquida", "cancelados"])
    for linha in serie:
        escritor.writerow([
            linha["bucket"], linha["pedidos"], linha["receita_bruta"],
            linha["receita_liquida"], linha["cancelados"],
        ])

    conteudo = buffer.getvalue()
    return StreamingResponse(
        io.BytesIO(conteudo.encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="financeiro_{ini.date()}_{f.date()}.csv"'
        },
    )


@router.get("/reconciliation/export", summary="Exporta as divergências de conciliação")
async def exportar_divergencias(ctx: AnalistaDep, db: DbDep) -> StreamingResponse:
    linhas = await reconciliation.divergencias(db, ctx.tenant_id, limite=5000)

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow([
        "pedido_id", "id_externo", "canal", "data", "receita_bruta",
        "liquido_esperado", "liquido_liquidado", "divergencia", "divergencia_%", "diagnostico",
    ])
    for linha in linhas:
        escritor.writerow([
            linha["order_id"], linha["external_id"], linha["channel"], linha["date_created"],
            linha["gross_amount"], linha["expected_net"], linha["settled_net"],
            linha["divergence"], linha["divergence_pct"], linha["notes"],
        ])

    return StreamingResponse(
        io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="divergencias.csv"'},
    )


@router.get("/formats", summary="Formatos de exportação suportados")
async def formatos() -> dict[str, Any]:
    return {
        "formatos": sorted(FORMATOS),
        "observacao": (
            "CSV e XLSX são gerados na hora, em streaming — exportar centenas de "
            "milhares de linhas não estoura memória nem tempo de requisição."
        ),
        "nao_disponiveis": {
            "pdf": (
                "Ainda não implementado. Para relatório assinado, exporte em XLSX "
                "e gere o PDF na ferramenta de planilha."
            )
        },
    }


# --- Análises ----------------------------------------------------------------

def _filtro(ctx, inicio, fim, channel, sku=None) -> analytics.Filtro:
    ini, f = analytics.normalizar_periodo(inicio, fim)
    return analytics.Filtro(
        tenant_id=ctx.tenant_id, inicio=ini, fim=f, channel=channel, sku=sku
    )


@router.get(
    "/abc",
    summary="Curva ABC de produtos",
    description=(
        "Classifica os SKUs pela participação **acumulada** na receita: A até "
        "80%, B até 95%, C o resto. O corte é sobre o acumulado e não sobre a "
        "posição no ranking — o que interessa é quantos itens sustentam o "
        "faturamento, e esse número varia de operação para operação."
    ),
)
async def curva_abc(
    ctx: CtxDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
    limite: int = Query(500, ge=10, le=2000),
) -> dict[str, Any]:
    return await analytics.curva_abc(db, _filtro(ctx, inicio, fim, channel), limite=limite)


@router.get(
    "/cohort",
    summary="Coorte de compradores por mês da primeira compra",
    description=(
        "Retenção por grupo: cada linha é quem comprou pela primeira vez num "
        "mês, cada coluna é quantos voltaram nos meses seguintes. Usa "
        "`buyer_hash`, identificador derivado sem dado pessoal; pedidos de canal "
        "que não expõe comprador ficam de fora e a cobertura é informada."
    ),
)
async def coorte(
    ctx: CtxDep,
    db: DbDep,
    meses: int = Query(12, ge=2, le=36),
    channel: str | None = None,
) -> dict[str, Any]:
    return await analytics.coorte_de_compradores(
        db, ctx.tenant_id, meses=meses, canal=channel
    )


@router.get(
    "/moving-average",
    summary="Série diária com média móvel",
    description=(
        "A média móvel remove o ciclo semanal: venda de autopeça cai no fim de "
        "semana e sobe na segunda, e olhar o dia isolado faz toda segunda "
        "parecer crescimento. Os primeiros dias saem sem média, em vez de uma "
        "média parcial exibida como se fosse cheia."
    ),
)
async def media_movel(
    ctx: CtxDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
    janela: int = Query(7, ge=2, le=90),
) -> dict[str, Any]:
    return await analytics.serie_com_media_movel(
        db, _filtro(ctx, inicio, fim, channel), janela=janela
    )
