"""Apuração de imposto sobre vendas.

Nenhum marketplace conhece o regime tributário do vendedor — nem poderia. Sem
este módulo o sistema para na margem de contribuição e o "lucro" exibido ignora
o tributo, que costuma ser a segunda maior dedução depois da comissão.

**Distinção que evita erro grave:** existem dois tributos diferentes e somá-los
num campo só conta imposto duas vezes.

* ``Order.tax_amount`` — retido na fonte **pelo canal**, informado pela API.
  Já reduz o valor depositado, então faz parte do líquido.
* ``Order.sales_tax_amount`` — apurado pelo **regime do vendedor** (Simples,
  DAS, presumido). O canal deposita o valor cheio; o tributo é recolhido depois.
  Por isso é deduzido apenas no DRE, nunca do líquido recebido.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.costs import BaseImposto, RegimeTributario, TaxRule
from app.models.enums import StatusPedido
from app.models.order import Order
from app.services.finance import arredondar

log = structlog.get_logger(__name__)
ZERO = Decimal("0")
CEM = Decimal("100")


@dataclass(slots=True)
class ResultadoApuracao:
    pedidos: int = 0
    imposto_total: Decimal = ZERO
    sem_regra: int = 0

    def como_dict(self) -> dict[str, object]:
        return {
            "pedidos": self.pedidos,
            "imposto_total": str(arredondar(self.imposto_total)),
            "sem_regra": self.sem_regra,
        }


async def regras_vigentes(
    db: AsyncSession, tenant_id: int, quando: date, canal: str = ""
) -> list[TaxRule]:
    """Regras aplicáveis a uma venda na data em que ela ocorreu.

    A data do pedido é o que define a regra, e não a data de hoje: no Simples
    Nacional a alíquota muda conforme o faturamento acumulado, e apurar um
    pedido antigo com a alíquota atual reescreveria um mês já fechado pelo
    contador.
    """
    todas = list(
        (
            await db.execute(
                # ``selectinload`` obrigatório: as faixas são lidas fora do
                # contexto da consulta, e lazy loading em sessão async levanta
                # MissingGreenlet em vez de simplesmente buscar.
                select(TaxRule)
                .options(selectinload(TaxRule.brackets))
                .where(TaxRule.tenant_id == tenant_id, TaxRule.is_active.is_(True))
            )
        ).scalars()
    )
    return [
        r
        for r in todas
        if r.vigente_em(quando) and (not r.channel or r.channel == canal)
    ]


# --- Simples Nacional: alíquota efetiva por faixa ----------------------------

def _primeiro_dia(quando: date) -> date:
    return quando.replace(day=1)


def _somar_meses(quando: date, delta: int) -> date:
    total = (quando.year * 12 + quando.month - 1) + delta
    return date(total // 12, total % 12 + 1, 1)


async def calcular_rbt12(
    db: AsyncSession, tenant_id: int, *, referencia: date
) -> tuple[Decimal, int]:
    """Receita bruta acumulada nos 12 meses **anteriores** ao mês de apuração.

    A janela exclui o mês corrente de propósito: é assim que a LC 123/2006
    define a RBT12, e incluir o mês em curso faria a alíquota mudar a cada venda
    do mês, tornando impossível conferir a apuração.

    Devolve também **quantos meses de histórico existem**. Empresa com menos de
    13 meses de operação não tem RBT12 completa, e a lei manda proporcionalizar
    — sem isso, um negócio novo cairia sempre na primeira faixa e recolheria a
    menos.
    """
    fim = _primeiro_dia(referencia)
    inicio = _somar_meses(fim, -12)

    total = await db.scalar(
        select(func.coalesce(func.sum(Order.gross_amount), 0)).where(
            Order.tenant_id == tenant_id,
            Order.status != StatusPedido.CANCELADO,
            Order.date_created >= datetime(inicio.year, inicio.month, 1),
            Order.date_created < datetime(fim.year, fim.month, 1),
        )
    )

    primeiro_pedido = await db.scalar(
        select(func.min(Order.date_created)).where(Order.tenant_id == tenant_id)
    )
    if primeiro_pedido is None:
        return ZERO, 0

    inicio_operacao = max(_primeiro_dia(primeiro_pedido.date()), inicio)
    meses = max(
        0, (fim.year * 12 + fim.month) - (inicio_operacao.year * 12 + inicio_operacao.month)
    )
    return Decimal(str(total or 0)), meses


def rbt12_proporcionalizada(bruto: Decimal, meses: int) -> Decimal:
    """Projeta a receita de quem ainda não completou 12 meses.

    Regra da LC 123/2006 para início de atividade: usa-se a média dos meses em
    operação multiplicada por 12. Sem isso, uma empresa nova ficaria presa na
    faixa mais baixa até completar um ano e recolheria abaixo do devido.
    """
    if meses <= 0:
        return ZERO
    if meses >= 12:
        return bruto
    return (bruto / Decimal(meses) * Decimal("12")).quantize(Decimal("0.01"))


def aliquota_efetiva_simples(regra: TaxRule, rbt12: Decimal) -> Decimal:
    """Alíquota efetiva do Simples Nacional, em pontos percentuais.

        efetiva = (RBT12 × alíquota nominal − parcela a deduzir) ÷ RBT12

    É a fórmula do art. 18 da LC 123/2006, e é o que torna a tabela
    progressiva: sem a parcela a deduzir, cruzar o teto de uma faixa faria o
    imposto saltar de degrau, e faturar R$ 1 a mais poderia custar milhares em
    tributo.

    Empresa sem histórico (RBT12 zero) fica na primeira faixa — é o tratamento
    de início de atividade, não uma isenção.
    """
    faixas = sorted(regra.brackets, key=lambda f: Decimal(str(f.rbt12_ate)))
    if not faixas:
        return Decimal(str(regra.rate_pct or 0))

    escolhida = next(
        (f for f in faixas if rbt12 <= Decimal(str(f.rbt12_ate))),
        faixas[-1],
    )
    nominal = Decimal(str(escolhida.aliquota_nominal_pct or 0))
    deduzir = Decimal(str(escolhida.parcela_deduzir or 0))

    if rbt12 <= ZERO:
        return nominal

    efetiva = (rbt12 * nominal / CEM - deduzir) / rbt12 * CEM
    return max(ZERO, efetiva.quantize(Decimal("0.0001")))


def excedeu_o_teto(regra: TaxRule, rbt12: Decimal) -> bool:
    """Faturamento acima da última faixa da tabela.

    No Simples isso não é "mais uma faixa": passar do teto **desenquadra** a
    empresa, que precisa migrar de regime. O sistema continua calculando pela
    última faixa, porque zerar o imposto seria pior, mas quem olha o painel
    tem de saber que o número deixou de valer — um desenquadramento que passa
    despercebido vira autuação.
    """
    if not regra.brackets:
        return False
    teto = max(Decimal(str(f.rbt12_ate)) for f in regra.brackets)
    return rbt12 > teto


async def aliquota_do_periodo(
    db: AsyncSession, tenant_id: int, regra: TaxRule, *, referencia: date
) -> tuple[Decimal, Decimal]:
    """Alíquota a aplicar no mês e a RBT12 que a determinou.

    Devolver as duas coisas juntas é intencional: sem a RBT12 ao lado, ninguém
    consegue conferir de onde saiu a alíquota, e a apuração vira um número que
    o contador tem de aceitar por fé.
    """
    if regra.regime != RegimeTributario.SIMPLES_PROGRESSIVO:
        return Decimal(str(regra.rate_pct or 0)), ZERO

    bruto, meses = await calcular_rbt12(db, tenant_id, referencia=referencia)
    rbt12 = rbt12_proporcionalizada(bruto, meses)
    return aliquota_efetiva_simples(regra, rbt12), rbt12


def calcular_imposto(
    pedido: Order,
    regras: list[TaxRule],
    aliquotas: dict[int, Decimal] | None = None,
) -> tuple[Decimal, int | None]:
    """Aplica as regras vigentes e devolve ``(imposto, id_da_regra_principal)``.

    ``aliquotas`` traz a alíquota já resolvida por regra — no Simples ela
    depende da RBT12 do **mês**, não do pedido, e recalculá-la a cada linha
    além de custar uma consulta por pedido permitiria que dois pedidos do mesmo
    mês saíssem com alíquotas diferentes. Quando omitido, usa ``rate_pct``.

    Pedido cancelado não gera tributo — não houve receita.
    """
    if pedido.status == StatusPedido.CANCELADO or not regras:
        return ZERO, None

    bruto = Decimal(str(pedido.gross_amount or 0))
    frete = Decimal(str(pedido.shipping_revenue or 0))
    liquido = Decimal(str(pedido.net_amount or 0))
    # Devolução reduz a receita tributável do período.
    devolucao = Decimal(str(pedido.refund_amount or 0))

    total = ZERO
    principal: int | None = None
    maior = ZERO

    for regra in regras:
        if regra.base == BaseImposto.BRUTA_MAIS_FRETE:
            base = bruto + frete
        elif regra.base == BaseImposto.RECEITA_LIQUIDA:
            base = liquido
        else:
            base = bruto
        base = max(ZERO, base - devolucao)

        taxa = (aliquotas or {}).get(regra.id, Decimal(str(regra.rate_pct or 0)))
        valor = (base * taxa / CEM).quantize(Decimal("0.0001"))
        total += valor
        if valor > maior:
            maior, principal = valor, regra.id

    return total, principal


async def apurar_periodo(
    db: AsyncSession,
    tenant_id: int,
    *,
    inicio: datetime,
    fim: datetime,
    canal: str | None = None,
) -> ResultadoApuracao:
    """Recalcula o imposto dos pedidos do período.

    Roda depois de cadastrar ou corrigir uma regra: sem isso, os pedidos já
    importados continuariam com imposto zerado e o DRE mostraria lucro
    inflado.
    """
    consulta = select(Order).where(
        Order.tenant_id == tenant_id,
        Order.date_created >= inicio,
        Order.date_created <= fim,
    )
    if canal:
        consulta = consulta.where(Order.channel == canal)

    resultado = ResultadoApuracao()
    cache: dict[tuple[date, str], list[TaxRule]] = {}
    # A alíquota do Simples é do mês, não do pedido: resolver por (mês, regra)
    # garante que todo pedido de agosto seja tributado igual e evita uma
    # consulta de RBT12 por linha.
    taxas: dict[tuple[date, int], Decimal] = {}

    for pedido in (await db.execute(consulta)).scalars():
        dia = pedido.date_created.date()
        chave = (dia, pedido.channel)
        if chave not in cache:
            cache[chave] = await regras_vigentes(db, tenant_id, dia, pedido.channel)

        regras = cache[chave]
        if not regras:
            resultado.sem_regra += 1

        mes = _primeiro_dia(dia)
        aliquotas: dict[int, Decimal] = {}
        for regra in regras:
            if (mes, regra.id) not in taxas:
                taxa, _ = await aliquota_do_periodo(db, tenant_id, regra, referencia=mes)
                taxas[(mes, regra.id)] = taxa
            aliquotas[regra.id] = taxas[(mes, regra.id)]

        imposto, regra_id = calcular_imposto(pedido, regras, aliquotas)
        pedido.sales_tax_amount = imposto
        pedido.tax_rule_id = regra_id

        resultado.pedidos += 1
        resultado.imposto_total += imposto

    await db.commit()
    log.info("apuracao_concluida", tenant=tenant_id, **resultado.como_dict())
    return resultado


async def aliquota_efetiva(
    db: AsyncSession, tenant_id: int, *, inicio: datetime, fim: datetime
) -> Decimal:
    """Percentual que o imposto representou sobre a receita bruta do período.

    Útil para conferir com o contador: se a efetiva apurada aqui destoar da
    guia recolhida, há regra faltando ou vigência errada.
    """
    from sqlalchemy import func

    linha = (
        await db.execute(
            select(
                func.coalesce(func.sum(Order.gross_amount), 0),
                func.coalesce(func.sum(Order.sales_tax_amount), 0),
            ).where(
                Order.tenant_id == tenant_id,
                Order.date_created >= inicio,
                Order.date_created <= fim,
                Order.status != StatusPedido.CANCELADO,
            )
        )
    ).one()

    bruto = Decimal(str(linha[0] or 0))
    imposto = Decimal(str(linha[1] or 0))
    return arredondar(imposto / bruto * CEM) if bruto else ZERO
