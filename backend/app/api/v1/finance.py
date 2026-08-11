"""Aba Faturamento e Conciliação."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.deps import AnalistaDep, CtxDep, DbDep
from app.models.enums import FonteLiquido, StatusPagamento, StatusPedido
from app.models.finance import Payment, PaymentFee, Settlement
from app.models.order import Order
from app.services import analytics, reconciliation
from app.services.finance import arredondar

router = APIRouter(prefix="/finance", tags=["Faturamento e conciliação"])


@router.get(
    "/waterfall",
    summary="Cascata do bruto ao líquido",
    description=(
        "Decomposição completa do faturamento. O 'gap' entre bruto e líquido é a "
        "leitura mais útil do painel: mostra o peso real das taxas."
    ),
)
async def cascata(
    ctx: CtxDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    ini, f = analytics.normalizar_periodo(inicio, fim)
    filtro = analytics.Filtro(tenant_id=ctx.tenant_id, inicio=ini, fim=f, channel=channel)

    linha = (
        await db.execute(
            filtro.aplicar(
                select(
                    func.coalesce(func.sum(Order.gross_amount), 0),
                    func.coalesce(func.sum(Order.shipping_revenue), 0),
                    func.coalesce(func.sum(Order.platform_fee), 0),
                    func.coalesce(func.sum(Order.payment_fee), 0),
                    func.coalesce(func.sum(Order.shipping_cost), 0),
                    func.coalesce(func.sum(Order.tax_amount), 0),
                    func.coalesce(func.sum(Order.discount_amount), 0),
                    func.coalesce(func.sum(Order.refund_amount), 0),
                    func.coalesce(func.sum(Order.net_amount), 0),
                    func.coalesce(func.sum(Order.cogs), 0),
                )
            ).where(Order.status != StatusPedido.CANCELADO)
        )
    ).one()

    (bruto, frete_receita, comissao, taxa_pgto, frete_custo, impostos,
     descontos, reembolsos, liquido, cmv) = (Decimal(str(v or 0)) for v in linha)

    # Composição por procedência: sem isso o usuário não sabe quanto do "líquido"
    # já é dinheiro em conta e quanto ainda é previsão.
    fontes = (
        await db.execute(
            filtro.aplicar(
                select(Order.net_source, func.count(Order.id), func.sum(Order.net_amount))
            )
            .where(Order.status != StatusPedido.CANCELADO)
            .group_by(Order.net_source)
        )
    ).all()

    return {
        "periodo": {"inicio": ini, "fim": f},
        "etapas": [
            {"nome": "Receita bruta", "valor": str(arredondar(bruto)), "tipo": "inicio"},
            {"nome": "Frete cobrado", "valor": str(arredondar(frete_receita)), "tipo": "positivo"},
            {"nome": "Comissão do marketplace", "valor": str(arredondar(-comissao)), "tipo": "negativo"},
            {"nome": "Taxa de pagamento", "valor": str(arredondar(-taxa_pgto)), "tipo": "negativo"},
            {"nome": "Custo de frete", "valor": str(arredondar(-frete_custo)), "tipo": "negativo"},
            {"nome": "Impostos", "valor": str(arredondar(-impostos)), "tipo": "negativo"},
            {"nome": "Descontos e bônus", "valor": str(arredondar(descontos)), "tipo": "positivo"},
            {"nome": "Reembolsos", "valor": str(arredondar(-reembolsos)), "tipo": "negativo"},
            {"nome": "Receita líquida", "valor": str(arredondar(liquido)), "tipo": "total"},
        ],
        "totais": {
            "receita_bruta": str(arredondar(bruto)),
            "taxas_totais": str(arredondar(comissao + taxa_pgto)),
            "taxa_efetiva_pct": str(arredondar((comissao + taxa_pgto) / bruto * 100))
            if bruto
            else "0.00",
            "receita_liquida": str(arredondar(liquido)),
            "cmv": str(arredondar(cmv)),
            "margem_contribuicao": str(arredondar(liquido - cmv)),
            "margem_pct": str(arredondar((liquido - cmv) / bruto * 100)) if bruto else "0.00",
        },
        "por_procedencia": [
            {
                "fonte": str(fonte),
                "rotulo": _rotulo_fonte(str(fonte)),
                "pedidos": int(qtd or 0),
                "valor": str(arredondar(Decimal(str(soma or 0)))),
            }
            for fonte, qtd, soma in fontes
        ],
    }


def _rotulo_fonte(fonte: str) -> str:
    return {
        FonteLiquido.LIQUIDADO: "Liquidado — dinheiro em conta",
        FonteLiquido.REPORTADO_API: "Informado pelo canal — ainda não liberado",
        FonteLiquido.CALCULADO: "Estimado pelo sistema",
    }.get(fonte, fonte)


@router.get("/fees", summary="Composição de taxas por tipo")
async def taxas(
    ctx: CtxDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
) -> list[dict[str, Any]]:
    """Detalhamento por tipo de taxa.

    É aqui que o vendedor descobre que a taxa de parcelamento subiu três pontos
    sem aviso — informação que o total agregado esconde.
    """
    ini, f = analytics.normalizar_periodo(inicio, fim)
    linhas = (
        await db.execute(
            select(
                PaymentFee.fee_type,
                func.count(PaymentFee.id),
                func.coalesce(func.sum(PaymentFee.amount), 0),
            )
            .join(Payment, Payment.id == PaymentFee.payment_id)
            .where(
                PaymentFee.tenant_id == ctx.tenant_id,
                Payment.date_approved >= ini,
                Payment.date_approved <= f,
            )
            .group_by(PaymentFee.fee_type)
            .order_by(func.sum(PaymentFee.amount).desc())
        )
    ).all()

    total = sum((Decimal(str(v or 0)) for _, _, v in linhas), Decimal("0"))
    return [
        {
            "tipo": str(tipo),
            "ocorrencias": int(qtd or 0),
            "valor": str(arredondar(Decimal(str(soma or 0)))),
            "participacao_pct": str(arredondar(Decimal(str(soma or 0)) / total * 100))
            if total
            else "0.00",
        }
        for tipo, qtd, soma in linhas
    ]


@router.get("/reconciliation", summary="Resumo da conciliação")
async def resumo_conciliacao(ctx: CtxDep, db: DbDep, dias: int = Query(30, le=365)) -> dict:
    return await reconciliation.resumo(db, ctx.tenant_id, dias=dias)


@router.get("/divergences", summary="Fila de divergências com diagnóstico")
async def divergencias(
    ctx: CtxDep, db: DbDep, limite: int = Query(100, le=500)
) -> list[dict[str, Any]]:
    return await reconciliation.divergencias(db, ctx.tenant_id, limite=limite)


@router.post("/reconciliation/run", summary="Executa a conciliação sob demanda")
async def rodar_conciliacao(
    ctx: AnalistaDep, db: DbDep, dias: int = Query(30, le=365)
) -> dict:
    resultado = await reconciliation.conciliar_periodo(db, ctx.tenant_id, dias=dias)
    return resultado.como_dict()


@router.get(
    "/cashflow",
    summary="Fluxo de caixa projetado",
    description=(
        "Combina `money_release_date` do Mercado Pago e `escrow_release_time` da "
        "Shopee. Responde 'quanto entra e quando' — informação que nenhum painel "
        "nativo entrega de forma consolidada entre canais."
    ),
)
async def fluxo_de_caixa(ctx: CtxDep, db: DbDep, dias: int = Query(30, le=180)) -> dict:
    hoje = datetime.now(UTC)
    limite = hoje + timedelta(days=dias)

    linhas = (
        await db.execute(
            select(
                func.date(Payment.money_release_date),
                Payment.money_release_status,
                func.coalesce(func.sum(Payment.net_received_amount), 0),
                func.count(Payment.id),
            )
            .where(
                Payment.tenant_id == ctx.tenant_id,
                Payment.money_release_date.is_not(None),
                Payment.money_release_date >= hoje - timedelta(days=7),
                Payment.money_release_date <= limite,
            )
            .group_by(func.date(Payment.money_release_date), Payment.money_release_status)
            .order_by(func.date(Payment.money_release_date))
        )
    ).all()

    por_dia: dict[str, dict[str, Any]] = {}
    for dia, status, valor, qtd in linhas:
        chave = str(dia)[:10]
        entrada = por_dia.setdefault(
            chave, {"data": chave, "liberado": Decimal("0"), "previsto": Decimal("0"), "pagamentos": 0}
        )
        if status == "released":
            entrada["liberado"] += Decimal(str(valor or 0))
        else:
            entrada["previsto"] += Decimal(str(valor or 0))
        entrada["pagamentos"] += int(qtd or 0)

    dias_ordenados = sorted(por_dia.values(), key=lambda d: d["data"])
    total_liberado = sum((d["liberado"] for d in dias_ordenados), Decimal("0"))
    total_previsto = sum((d["previsto"] for d in dias_ordenados), Decimal("0"))

    return {
        "resumo": {
            "total_liberado": str(arredondar(total_liberado)),
            "total_previsto": str(arredondar(total_previsto)),
            "total": str(arredondar(total_liberado + total_previsto)),
            "dias": dias,
        },
        "calendario": [
            {
                "data": d["data"],
                "liberado": str(arredondar(d["liberado"])),
                "previsto": str(arredondar(d["previsto"])),
                "pagamentos": d["pagamentos"],
            }
            for d in dias_ordenados
        ],
    }


@router.get("/settlements", summary="Extrato de repasses")
async def repasses(
    ctx: CtxDep, db: DbDep, limite: int = Query(50, le=200)
) -> list[dict[str, Any]]:
    resultado = await db.execute(
        select(Settlement)
        .where(Settlement.tenant_id == ctx.tenant_id)
        .order_by(Settlement.settlement_date.desc())
        .limit(limite)
    )
    return [
        {
            "id": r.id,
            "external_id": r.external_id,
            "channel": r.channel,
            "settlement_date": r.settlement_date,
            "gross_amount": str(r.gross_amount),
            "fee_amount": str(r.fee_amount),
            "net_amount": str(r.net_amount),
            "status": r.status,
            "source": r.source,
        }
        for r in resultado.scalars()
    ]


@router.get(
    "/receivables",
    summary="Saldo a receber consolidado (Mercado Pago + Shopee)",
    description=(
        "Quanto ainda vai cair na conta, somando todos os canais. Diferente do "
        "fluxo de caixa, não se limita a uma janela de datas: pagamento sem "
        "data de liberação prevista também aparece, separado, porque é "
        "justamente o que some de um calendário e some do controle."
    ),
)
async def contas_a_receber(ctx: CtxDep, db: DbDep) -> dict[str, Any]:
    """Saldo a receber por canal, com idade do valor pendente.

    O Mercado Pago e a Shopee informam por pagamento quando o dinheiro é
    liberado (``money_release_date`` / ``escrow_release_time``). Somar isso é o
    que responde "quanto tenho a receber" — pergunta que nenhum painel nativo
    responde de forma consolidada entre canais, porque cada um só enxerga a si
    mesmo.

    Valores **liberados** ficam de fora do saldo: já entraram na conta.
    """
    hoje = datetime.now(UTC)

    linhas = (
        await db.execute(
            select(
                Payment.channel_account_id,
                Payment.provider,
                Payment.money_release_status,
                Payment.money_release_date,
                func.coalesce(func.sum(Payment.net_received_amount), 0),
                func.count(Payment.id),
            )
            .where(
                Payment.tenant_id == ctx.tenant_id,
                Payment.status == StatusPagamento.APROVADO,
            )
            .group_by(
                Payment.channel_account_id,
                Payment.provider,
                Payment.money_release_status,
                Payment.money_release_date,
            )
        )
    ).all()

    por_provedor: dict[str, dict[str, Any]] = {}
    faixas = {"vencido": Decimal("0"), "ate_7_dias": Decimal("0"),
              "ate_30_dias": Decimal("0"), "acima_de_30": Decimal("0"),
              "sem_previsao": Decimal("0")}
    total_pendente = Decimal("0")
    total_liberado = Decimal("0")

    for conta_id, provedor, situacao, liberacao, valor, qtd in linhas:
        montante = Decimal(str(valor or 0))
        bucket = por_provedor.setdefault(
            provedor or "desconhecido",
            {"provedor": provedor or "desconhecido", "pendente": Decimal("0"),
             "liberado": Decimal("0"), "pagamentos": 0, "contas": set()},
        )
        bucket["pagamentos"] += int(qtd or 0)
        bucket["contas"].add(conta_id)

        if situacao == "released":
            bucket["liberado"] += montante
            total_liberado += montante
            continue

        bucket["pendente"] += montante
        total_pendente += montante

        if liberacao is None:
            faixas["sem_previsao"] += montante
        else:
            # O SQLite devolve datetime sem fuso e o Postgres com fuso; subtrair
            # os dois levanta TypeError e derruba o endpoint inteiro.
            if liberacao.tzinfo is None:
                liberacao = liberacao.replace(tzinfo=UTC)
            dias = (liberacao - hoje).days
            if dias < 0:
                # Data de liberação no passado e status ainda pendente: ou o
                # repasse atrasou, ou o webhook de liberação não chegou. Nos
                # dois casos é o valor que precisa ser investigado primeiro.
                faixas["vencido"] += montante
            elif dias <= 7:
                faixas["ate_7_dias"] += montante
            elif dias <= 30:
                faixas["ate_30_dias"] += montante
            else:
                faixas["acima_de_30"] += montante

    return {
        "resumo": {
            "total_a_receber": str(arredondar(total_pendente)),
            "total_ja_liberado": str(arredondar(total_liberado)),
            "atualizado_em": hoje,
        },
        "por_faixa": {chave: str(arredondar(v)) for chave, v in faixas.items()},
        "por_provedor": [
            {
                "provedor": b["provedor"],
                "pendente": str(arredondar(b["pendente"])),
                "liberado": str(arredondar(b["liberado"])),
                "pagamentos": b["pagamentos"],
                "contas": len(b["contas"]),
            }
            for b in sorted(
                por_provedor.values(), key=lambda x: x["pendente"], reverse=True
            )
        ],
        "observacao": (
            "Valor pendente é o líquido já descontado das taxas do canal. O "
            "imposto do regime do vendedor ainda será recolhido sobre ele."
        ),
    }
