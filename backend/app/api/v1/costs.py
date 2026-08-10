"""Custos, impostos e DRE — a aba que fecha a conta até o lucro real."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import AdminDep, AnalistaDep, CtxDep, DbDep
from app.core.errors import Conflito, NaoEncontrado
from app.models.costs import BaseImposto, MonthlyClose, OperatingExpense, TaxRule
from app.schemas.common import Base, RespostaOperacao
from app.services import analytics, audit, dre as servico_dre, taxes

router = APIRouter(prefix="/costs", tags=["Custos, impostos e DRE"])


# --- Schemas ----------------------------------------------------------------

class RegraImpostoIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = "simples_nacional"
    rate_pct: Decimal = Field(ge=0, le=100)
    base: str = BaseImposto.RECEITA_BRUTA
    channel: str = ""
    valid_from: date
    valid_to: date | None = None
    is_active: bool = True
    notes: str = ""


class RegraImpostoOut(Base):
    id: int
    name: str
    kind: str
    rate_pct: Decimal
    base: str
    channel: str
    valid_from: date
    valid_to: date | None
    is_active: bool
    notes: str


class DespesaIn(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    category: str = "other"
    amount: Decimal = Field(ge=0)
    competence_month: date
    is_recurring: bool = False
    channel: str = ""
    notes: str = ""


class DespesaOut(Base):
    id: int
    description: str
    category: str
    amount: Decimal
    competence_month: date
    is_recurring: bool
    channel: str
    notes: str


# --- Regras tributárias -----------------------------------------------------

@router.get("/tax-rules", response_model=list[RegraImpostoOut], summary="Regras tributárias")
async def listar_regras(ctx: CtxDep, db: DbDep) -> list[RegraImpostoOut]:
    resultado = await db.execute(
        select(TaxRule)
        .where(TaxRule.tenant_id == ctx.tenant_id)
        .order_by(TaxRule.valid_from.desc())
    )
    return [RegraImpostoOut.model_validate(r) for r in resultado.scalars()]


@router.post(
    "/tax-rules",
    response_model=RegraImpostoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cria regra tributária",
    description=(
        "A vigência é obrigatória porque a alíquota do Simples muda com o "
        "faturamento acumulado. Cada pedido é apurado com a regra vigente na "
        "data da venda, nunca com a alíquota de hoje."
    ),
)
async def criar_regra(dados: RegraImpostoIn, ctx: AnalistaDep, db: DbDep) -> RegraImpostoOut:
    if dados.valid_to and dados.valid_to < dados.valid_from:
        raise Conflito("A data final da vigência não pode ser anterior à inicial.")

    regra = TaxRule(tenant_id=ctx.tenant_id, **dados.model_dump())
    db.add(regra)
    await db.flush()
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="tax_rule.created",
        entity_type="tax_rule",
        entity_id=regra.id,
        after=dados.model_dump(mode="json"),
    )
    await db.commit()
    return RegraImpostoOut.model_validate(regra)


@router.patch("/tax-rules/{regra_id}", response_model=RegraImpostoOut, summary="Edita regra")
async def editar_regra(
    regra_id: int, dados: RegraImpostoIn, ctx: AnalistaDep, db: DbDep
) -> RegraImpostoOut:
    regra = await _obter_regra(db, ctx.tenant_id, regra_id)
    antes = {"rate_pct": str(regra.rate_pct), "valid_from": regra.valid_from.isoformat()}
    for campo, valor in dados.model_dump().items():
        setattr(regra, campo, valor)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="tax_rule.updated",
        entity_type="tax_rule",
        entity_id=regra_id,
        before=antes,
        after=dados.model_dump(mode="json"),
    )
    await db.commit()
    return RegraImpostoOut.model_validate(regra)


@router.delete("/tax-rules/{regra_id}", response_model=RespostaOperacao, summary="Remove regra")
async def remover_regra(regra_id: int, ctx: AnalistaDep, db: DbDep) -> RespostaOperacao:
    """Remove a regra e zera o imposto dos pedidos que a usavam.

    Deixar o imposto apurado por uma regra apagada tornaria a apuração
    impossível de auditar — não haveria como reconstruir de onde veio o valor.
    """
    regra = await _obter_regra(db, ctx.tenant_id, regra_id)

    from app.models.order import Order

    afetados = list(
        (await db.execute(select(Order).where(Order.tax_rule_id == regra_id))).scalars()
    )
    for pedido in afetados:
        pedido.sales_tax_amount = Decimal("0")
        pedido.tax_rule_id = None

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="tax_rule.deleted",
        entity_type="tax_rule",
        entity_id=regra_id,
        before={"name": regra.name, "rate_pct": str(regra.rate_pct)},
    )
    await db.delete(regra)
    await db.commit()
    return RespostaOperacao(
        mensagem=f"Regra removida. {len(afetados)} pedidos precisam ser reapurados."
    )


@router.post(
    "/tax-rules/apply",
    response_model=RespostaOperacao,
    summary="Reapura o imposto do período",
    description=(
        "Recalcula `sales_tax_amount` dos pedidos existentes. Necessário após "
        "cadastrar ou corrigir uma regra — sem isso os pedidos já importados "
        "continuam com imposto zerado e o DRE mostra lucro inflado."
    ),
)
async def reapurar(
    ctx: AnalistaDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
) -> RespostaOperacao:
    ini, f = analytics.normalizar_periodo(inicio, fim, padrao_dias=365)
    resultado = await taxes.apurar_periodo(db, ctx.tenant_id, inicio=ini, fim=f, canal=channel)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="tax.reapplied",
        after=resultado.como_dict(),
    )
    await db.commit()
    return RespostaOperacao(
        mensagem=f"{resultado.pedidos} pedidos reapurados.", dados=resultado.como_dict()
    )


# --- Despesas operacionais --------------------------------------------------

@router.get("/expenses", response_model=list[DespesaOut], summary="Despesas operacionais")
async def listar_despesas(
    ctx: CtxDep, db: DbDep, mes: date | None = None
) -> list[DespesaOut]:
    consulta = select(OperatingExpense).where(OperatingExpense.tenant_id == ctx.tenant_id)
    if mes:
        consulta = consulta.where(OperatingExpense.competence_month == mes.replace(day=1))
    resultado = await db.execute(consulta.order_by(OperatingExpense.competence_month.desc()))
    return [DespesaOut.model_validate(d) for d in resultado.scalars()]


@router.post(
    "/expenses",
    response_model=DespesaOut,
    status_code=status.HTTP_201_CREATED,
    summary="Lança despesa",
    description=(
        "Lançada por competência: o mês a que a despesa pertence, "
        "independentemente de quando foi paga."
    ),
)
async def criar_despesa(dados: DespesaIn, ctx: AnalistaDep, db: DbDep) -> DespesaOut:
    payload = dados.model_dump()
    payload["competence_month"] = payload["competence_month"].replace(day=1)
    despesa = OperatingExpense(tenant_id=ctx.tenant_id, created_by=ctx.user_id, **payload)
    db.add(despesa)
    await db.flush()
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="expense.created",
        entity_type="operating_expense",
        entity_id=despesa.id,
        after=dados.model_dump(mode="json"),
    )
    await db.commit()
    return DespesaOut.model_validate(despesa)


@router.patch("/expenses/{despesa_id}", response_model=DespesaOut, summary="Edita despesa")
async def editar_despesa(
    despesa_id: int, dados: DespesaIn, ctx: AnalistaDep, db: DbDep
) -> DespesaOut:
    despesa = await _obter_despesa(db, ctx.tenant_id, despesa_id)
    antes = {"amount": str(despesa.amount), "description": despesa.description}
    payload = dados.model_dump()
    payload["competence_month"] = payload["competence_month"].replace(day=1)
    for campo, valor in payload.items():
        setattr(despesa, campo, valor)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="expense.updated",
        entity_type="operating_expense",
        entity_id=despesa_id,
        before=antes,
        after=dados.model_dump(mode="json"),
    )
    await db.commit()
    return DespesaOut.model_validate(despesa)


@router.delete("/expenses/{despesa_id}", response_model=RespostaOperacao, summary="Remove despesa")
async def remover_despesa(despesa_id: int, ctx: AnalistaDep, db: DbDep) -> RespostaOperacao:
    despesa = await _obter_despesa(db, ctx.tenant_id, despesa_id)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="expense.deleted",
        entity_type="operating_expense",
        entity_id=despesa_id,
        before={"description": despesa.description, "amount": str(despesa.amount)},
    )
    await db.delete(despesa)
    await db.commit()
    return RespostaOperacao(mensagem="Despesa removida.")


@router.post(
    "/expenses/replicate",
    response_model=RespostaOperacao,
    summary="Replica despesas recorrentes para um mês",
)
async def replicar_recorrentes(
    ctx: AnalistaDep, db: DbDep, origem: date, destino: date
) -> RespostaOperacao:
    """Copia as despesas fixas de um mês para outro.

    Evita redigitar aluguel, folha e software todo mês — e evita o esquecimento,
    que faria o lucro daquele mês aparecer maior do que foi.
    """
    mes_origem, mes_destino = origem.replace(day=1), destino.replace(day=1)

    recorrentes = list(
        (
            await db.execute(
                select(OperatingExpense).where(
                    OperatingExpense.tenant_id == ctx.tenant_id,
                    OperatingExpense.competence_month == mes_origem,
                    OperatingExpense.is_recurring.is_(True),
                )
            )
        ).scalars()
    )

    ja_existentes = {
        d.description
        for d in (
            await db.execute(
                select(OperatingExpense).where(
                    OperatingExpense.tenant_id == ctx.tenant_id,
                    OperatingExpense.competence_month == mes_destino,
                )
            )
        ).scalars()
    }

    criadas = 0
    for origem_despesa in recorrentes:
        # Não duplica o que já foi lançado no destino.
        if origem_despesa.description in ja_existentes:
            continue
        db.add(
            OperatingExpense(
                tenant_id=ctx.tenant_id,
                description=origem_despesa.description,
                category=origem_despesa.category,
                amount=origem_despesa.amount,
                competence_month=mes_destino,
                is_recurring=True,
                channel=origem_despesa.channel,
                notes=origem_despesa.notes,
                created_by=ctx.user_id,
            )
        )
        criadas += 1

    await db.commit()
    return RespostaOperacao(
        mensagem=f"{criadas} despesas recorrentes replicadas.",
        dados={"criadas": criadas, "ignoradas": len(recorrentes) - criadas},
    )


# --- DRE --------------------------------------------------------------------

@router.get(
    "/dre",
    summary="DRE gerencial até o lucro operacional",
    description=(
        "Da receita bruta ao lucro real, deduzindo o que nenhum marketplace "
        "conhece: custo do produto, imposto do regime do vendedor e despesas "
        "fixas. Sinaliza explicitamente quando falta custo ou regra tributária, "
        "em vez de exibir um lucro incompleto como se fosse final."
    ),
)
async def demonstrativo(
    ctx: CtxDep,
    db: DbDep,
    inicio: datetime | None = None,
    fim: datetime | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    ini, f = analytics.normalizar_periodo(inicio, fim)
    resultado = await servico_dre.apurar(db, ctx.tenant_id, inicio=ini, fim=f, canal=channel)
    return resultado.como_dict()


@router.get("/dre/monthly", summary="Série mensal do lucro")
async def dre_mensal(
    ctx: CtxDep, db: DbDep, meses: int = Query(12, ge=1, le=36)
) -> list[dict[str, Any]]:
    return await servico_dre.apurar_por_mes(db, ctx.tenant_id, meses=meses)


@router.get("/tax-summary", summary="Carga tributária efetiva do período")
async def resumo_tributario(
    ctx: CtxDep, db: DbDep, inicio: datetime | None = None, fim: datetime | None = None
) -> dict[str, Any]:
    ini, f = analytics.normalizar_periodo(inicio, fim)
    efetiva = await taxes.aliquota_efetiva(db, ctx.tenant_id, inicio=ini, fim=f)
    regras = await taxes.regras_vigentes(db, ctx.tenant_id, f.date())
    return {
        "periodo": {"inicio": ini, "fim": f},
        "aliquota_efetiva_pct": str(efetiva),
        "regras_vigentes": [
            {"id": r.id, "name": r.name, "rate_pct": str(r.rate_pct), "base": r.base}
            for r in regras
        ],
        "soma_aliquotas_pct": str(sum((Decimal(str(r.rate_pct)) for r in regras), Decimal("0"))),
    }


# --- Fechamento de mês ------------------------------------------------------

@router.post(
    "/close-month",
    response_model=RespostaOperacao,
    summary="Fecha o mês",
    description=(
        "Congela o resultado apurado. Protege um número já entregue ao contador "
        "de mudar sozinho quando chegar um chargeback ou ajuste retroativo — "
        "esses lançam na competência em que ocorreram."
    ),
)
async def fechar_mes(ctx: AdminDep, db: DbDep, mes: date) -> RespostaOperacao:
    primeiro = mes.replace(day=1)
    if await db.scalar(
        select(MonthlyClose).where(
            MonthlyClose.tenant_id == ctx.tenant_id, MonthlyClose.month == primeiro
        )
    ):
        raise Conflito(f"O mês {primeiro:%m/%Y} já está fechado.")

    inicio = datetime.combine(primeiro, datetime.min.time()).replace(tzinfo=UTC)
    fim = servico_dre.somar_meses(inicio, 1)
    resultado = await servico_dre.apurar(db, ctx.tenant_id, inicio=inicio, fim=fim)

    fechamento = MonthlyClose(
        tenant_id=ctx.tenant_id,
        month=primeiro,
        closed_at=datetime.now(UTC),
        closed_by=ctx.user_id,
        gross_amount=resultado.receita_bruta,
        net_amount=resultado.liquido_recebido,
        tax_amount=resultado.imposto_sobre_vendas,
        cogs_amount=resultado.cmv,
        expenses_amount=resultado.despesas,
        operating_profit=resultado.lucro_operacional,
        orders_count=resultado.pedidos,
    )
    db.add(fechamento)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="month.closed",
        entity_type="monthly_close",
        entity_id=primeiro.isoformat(),
        after={"lucro_operacional": str(resultado.lucro_operacional)},
    )
    await db.commit()

    return RespostaOperacao(
        mensagem=f"Mês {primeiro:%m/%Y} fechado.",
        dados={
            "lucro_operacional": str(resultado.lucro_operacional),
            "pedidos": resultado.pedidos,
            "aviso": resultado.como_dict()["qualidade"]["aviso"],
        },
    )


@router.get("/closes", summary="Meses fechados")
async def listar_fechamentos(ctx: CtxDep, db: DbDep) -> list[dict[str, Any]]:
    resultado = await db.execute(
        select(MonthlyClose)
        .where(MonthlyClose.tenant_id == ctx.tenant_id)
        .order_by(MonthlyClose.month.desc())
    )
    return [
        {
            "month": f.month,
            "closed_at": f.closed_at,
            "gross_amount": str(f.gross_amount),
            "net_amount": str(f.net_amount),
            "tax_amount": str(f.tax_amount),
            "cogs_amount": str(f.cogs_amount),
            "expenses_amount": str(f.expenses_amount),
            "operating_profit": str(f.operating_profit),
            "orders_count": f.orders_count,
        }
        for f in resultado.scalars()
    ]


# --- Auxiliares -------------------------------------------------------------

async def _obter_regra(db, tenant_id: int, regra_id: int) -> TaxRule:
    regra = await db.scalar(
        select(TaxRule).where(TaxRule.id == regra_id, TaxRule.tenant_id == tenant_id)
    )
    if regra is None:
        raise NaoEncontrado("Regra tributária não encontrada.")
    return regra


async def _obter_despesa(db, tenant_id: int, despesa_id: int) -> OperatingExpense:
    despesa = await db.scalar(
        select(OperatingExpense).where(
            OperatingExpense.id == despesa_id, OperatingExpense.tenant_id == tenant_id
        )
    )
    if despesa is None:
        raise NaoEncontrado("Despesa não encontrada.")
    return despesa
