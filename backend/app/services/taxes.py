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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.costs import BaseImposto, TaxRule
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
                select(TaxRule).where(
                    TaxRule.tenant_id == tenant_id, TaxRule.is_active.is_(True)
                )
            )
        ).scalars()
    )
    return [
        r
        for r in todas
        if r.vigente_em(quando) and (not r.channel or r.channel == canal)
    ]


def calcular_imposto(pedido: Order, regras: list[TaxRule]) -> tuple[Decimal, int | None]:
    """Aplica as regras vigentes e devolve ``(imposto, id_da_regra_principal)``.

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

        valor = (base * Decimal(str(regra.rate_pct or 0)) / CEM).quantize(Decimal("0.0001"))
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

    for pedido in (await db.execute(consulta)).scalars():
        dia = pedido.date_created.date()
        chave = (dia, pedido.channel)
        if chave not in cache:
            cache[chave] = await regras_vigentes(db, tenant_id, dia, pedido.channel)

        regras = cache[chave]
        if not regras:
            resultado.sem_regra += 1

        imposto, regra_id = calcular_imposto(pedido, regras)
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
