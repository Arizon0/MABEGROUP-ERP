"""Custos, impostos e DRE — a aba que fecha a conta até o lucro real."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminDep, AnalistaDep, CtxDep, DbDep
from app.core.errors import Conflito, NaoEncontrado
from app.models.costs import (
    BaseImposto,
    MonthlyClose,
    OperatingExpense,
    RegimeTributario,
    TaxBracket,
    TaxRule,
)
from app.schemas.common import Base, RespostaOperacao
from app.services import analytics, audit, dre as servico_dre, finance, taxes

router = APIRouter(prefix="/costs", tags=["Custos, impostos e DRE"])


# --- Schemas ----------------------------------------------------------------

class FaixaIn(BaseModel):
    rbt12_ate: Decimal = Field(gt=0)
    aliquota_nominal_pct: Decimal = Field(ge=0, le=100)
    parcela_deduzir: Decimal = Field(default=Decimal("0"), ge=0)


class FaixaOut(Base):
    id: int
    rbt12_ate: Decimal
    aliquota_nominal_pct: Decimal
    parcela_deduzir: Decimal


class RegraImpostoIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = "simples_nacional"
    #: Ignorado quando ``regime="simples_progressive"``: ali a alíquota sai das
    #: faixas, e aceitar um valor fixo junto criaria dois números concorrentes
    #: para a mesma coisa.
    rate_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    regime: str = Field(default=RegimeTributario.FIXA, pattern="^(fixed|simples_progressive)$")
    annex: str = ""
    faixas: list[FaixaIn] = Field(default_factory=list)
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
    regime: str
    annex: str
    brackets: list[FaixaOut] = []
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
        .options(selectinload(TaxRule.brackets))
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

    faixas = dados.faixas
    if dados.regime == RegimeTributario.SIMPLES_PROGRESSIVO and not faixas:
        raise Conflito(
            "Regime progressivo exige as faixas da tabela. Informe as faixas ou "
            "use o regime de alíquota fixa."
        )

    regra = TaxRule(
        tenant_id=ctx.tenant_id,
        **dados.model_dump(exclude={"faixas"}),
    )
    db.add(regra)
    await db.flush()
    for faixa in faixas:
        db.add(
            TaxBracket(
                tenant_id=ctx.tenant_id, tax_rule_id=regra.id, **faixa.model_dump()
            )
        )
    await db.flush()
    await db.refresh(regra, ["brackets"])
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
    await db.refresh(regra, ["brackets"])
    return RegraImpostoOut.model_validate(regra)


@router.patch("/tax-rules/{regra_id}", response_model=RegraImpostoOut, summary="Edita regra")
async def editar_regra(
    regra_id: int, dados: RegraImpostoIn, ctx: AnalistaDep, db: DbDep
) -> RegraImpostoOut:
    regra = await _obter_regra(db, ctx.tenant_id, regra_id)
    antes = {"rate_pct": str(regra.rate_pct), "valid_from": regra.valid_from.isoformat()}
    for campo, valor in dados.model_dump(exclude={"faixas"}).items():
        setattr(regra, campo, valor)

    # As faixas são substituídas por inteiro, não mescladas: a tabela do Simples
    # muda faixa a faixa por lei, e um merge parcial deixaria conviverem
    # números de tabelas diferentes na mesma regra.
    if dados.faixas:
        await db.execute(delete(TaxBracket).where(TaxBracket.tax_rule_id == regra_id))
        for faixa in dados.faixas:
            db.add(
                TaxBracket(
                    tenant_id=ctx.tenant_id, tax_rule_id=regra_id, **faixa.model_dump()
                )
            )
        await db.flush()
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
    mes = f.date().replace(day=1)

    detalhadas: list[dict[str, Any]] = []
    soma = Decimal("0")
    for r in regras:
        taxa, rbt12 = await taxes.aliquota_do_periodo(db, ctx.tenant_id, r, referencia=mes)
        soma += taxa
        detalhadas.append(
            {
                "id": r.id,
                "name": r.name,
                "regime": r.regime,
                "annex": r.annex,
                "base": r.base,
                "aliquota_aplicada_pct": str(taxa),
                # A RBT12 acompanha a alíquota de propósito: sem ela ninguém
                # confere de onde saiu o número, e a apuração vira um valor que
                # o contador teria de aceitar por fé.
                "rbt12": str(finance.arredondar(rbt12)) if rbt12 else None,
                "excedeu_teto_do_simples": taxes.excedeu_o_teto(r, rbt12),
            }
        )

    bruto_12m, meses = await taxes.calcular_rbt12(db, ctx.tenant_id, referencia=mes)
    return {
        "periodo": {"inicio": ini, "fim": f},
        "aliquota_efetiva_pct": str(efetiva),
        "rbt12": {
            "acumulado": str(finance.arredondar(bruto_12m)),
            "meses_de_historico": meses,
            "proporcionalizada": str(
                finance.arredondar(taxes.rbt12_proporcionalizada(bruto_12m, meses))
            ),
            "observacao": (
                "Menos de 12 meses de operação: a receita é proporcionalizada "
                "conforme a regra de início de atividade."
                if 0 < meses < 12
                else ""
            ),
        },
        "regras_vigentes": detalhadas,
        "soma_aliquotas_pct": str(soma),
        "alerta": next(
            (
                f"O faturamento acumulado ultrapassou o teto da tabela de "
                f"{r['name']}. No Simples isso desenquadra a empresa — procure "
                f"o contador antes de usar esta apuração."
                for r in detalhadas
                if r["excedeu_teto_do_simples"]
            ),
            "",
        ),
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
        select(TaxRule)
        .options(selectinload(TaxRule.brackets))
        .where(TaxRule.id == regra_id, TaxRule.tenant_id == tenant_id)
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


# --- Investimento em publicidade (Ads) ---------------------------------------
# Alimenta a análise de margem por pedido (GET /orders/margins). Nenhuma API
# entrega custo de Ads por pedido; o lançamento aqui é por competência e
# escopo, e o rateio acontece na consulta da análise.

from pydantic import field_validator  # noqa: E402

from app.models.marketing import AdSpend, EscopoAds  # noqa: E402


class AdSpendIn(BaseModel):
    channel: str = Field(min_length=1, max_length=20)
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    scope: str = Field(default=EscopoAds.CANAL)
    reference: str = Field(default="", max_length=80)
    amount: Decimal = Field(ge=0)
    #: Receita que o canal atribuiu à publicidade (relatório de Ads). Sem ela
    #: não há ACOS — só TACOS.
    attributed_revenue: Decimal | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=300)

    @field_validator("scope")
    @classmethod
    def _escopo_valido(cls, v: str) -> str:
        if v not in EscopoAds.TODOS:
            raise ValueError(f"scope deve ser um de {list(EscopoAds.TODOS)}")
        return v

    @field_validator("reference")
    @classmethod
    def _referencia_limpa(cls, v: str) -> str:
        return (v or "").strip()


class AdSpendOut(Base):
    id: int
    channel: str
    year: int
    month: int
    scope: str
    reference: str
    amount: Decimal
    attributed_revenue: Decimal | None = None
    notes: str


@router.get("/ad-spend", response_model=list[AdSpendOut], summary="Investimentos em Ads")
async def listar_ad_spend(
    ctx: CtxDep,
    db: DbDep,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    channel: str | None = None,
) -> list[AdSpendOut]:
    consulta = select(AdSpend).where(AdSpend.tenant_id == ctx.tenant_id)
    if year is not None:
        consulta = consulta.where(AdSpend.year == year)
    if month is not None:
        consulta = consulta.where(AdSpend.month == month)
    if channel:
        consulta = consulta.where(AdSpend.channel == channel)
    resultado = await db.execute(
        consulta.order_by(AdSpend.year.desc(), AdSpend.month.desc(), AdSpend.channel)
    )
    return [AdSpendOut.model_validate(a) for a in resultado.scalars()]


@router.put(
    "/ad-spend",
    response_model=AdSpendOut,
    summary="Lança ou atualiza investimento em Ads",
    description=(
        "Idempotente pela chave (canal, ano, mês, escopo, referência): regravar "
        "a mesma competência atualiza o valor em vez de duplicar o rateio."
    ),
)
async def salvar_ad_spend(dados: AdSpendIn, ctx: AnalistaDep, db: DbDep) -> AdSpendOut:
    # Escopo de canal não carrega referência; guardar uma criaria dois
    # lançamentos "do canal inteiro" diferentes na mesma competência.
    referencia = "" if dados.scope == EscopoAds.CANAL else dados.reference
    existente = (
        await db.execute(
            select(AdSpend).where(
                AdSpend.tenant_id == ctx.tenant_id,
                AdSpend.channel == dados.channel,
                AdSpend.year == dados.year,
                AdSpend.month == dados.month,
                AdSpend.scope == dados.scope,
                AdSpend.reference == referencia,
            )
        )
    ).scalar_one_or_none()

    if existente is None:
        existente = AdSpend(
            tenant_id=ctx.tenant_id,
            channel=dados.channel,
            year=dados.year,
            month=dados.month,
            scope=dados.scope,
            reference=referencia,
        )
        db.add(existente)
        acao = "ad_spend.created"
    else:
        acao = "ad_spend.updated"

    existente.amount = dados.amount
    existente.attributed_revenue = dados.attributed_revenue
    existente.notes = dados.notes
    await db.flush()
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=acao,
        entity_type="ad_spend",
        entity_id=existente.id,
        after=dados.model_dump(mode="json"),
    )
    await db.commit()
    return AdSpendOut.model_validate(existente)


@router.delete(
    "/ad-spend/{ad_spend_id}",
    response_model=RespostaOperacao,
    summary="Remove investimento em Ads",
)
async def remover_ad_spend(
    ad_spend_id: int, ctx: AnalistaDep, db: DbDep
) -> RespostaOperacao:
    registro = (
        await db.execute(
            select(AdSpend).where(
                AdSpend.tenant_id == ctx.tenant_id, AdSpend.id == ad_spend_id
            )
        )
    ).scalar_one_or_none()
    if registro is None:
        raise NaoEncontrado("Lançamento de Ads não encontrado.")
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="ad_spend.deleted",
        entity_type="ad_spend",
        entity_id=ad_spend_id,
        before={"amount": str(registro.amount), "channel": registro.channel},
    )
    await db.delete(registro)
    await db.commit()
    return RespostaOperacao(mensagem="Lançamento de Ads removido.")
