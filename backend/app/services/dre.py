"""DRE gerencial: da receita bruta ao lucro operacional.

Fecha a lacuna entre "quanto o canal me pagou" e "quanto eu realmente lucrei".
São três deduções que nenhum marketplace conhece e que, somadas, costumam
consumir a maior parte da margem aparente: o custo do produto, o imposto do
regime do vendedor e as despesas fixas da operação.

Ordem das linhas segue a lógica contábil: as deduções que pertencem à venda
saem antes da margem de contribuição; as que pertencem ao mês (aluguel, folha)
saem depois.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.costs import OperatingExpense
from app.models.enums import StatusPedido
from app.models.order import Order
from app.services.finance import arredondar

log = structlog.get_logger(__name__)
ZERO = Decimal("0")


@dataclass(slots=True)
class LinhaDRE:
    rotulo: str
    valor: Decimal
    tipo: str  # receita | deducao | subtotal | resultado
    #: Percentual sobre a receita bruta — o denominador de comparação usual.
    percentual: Decimal = ZERO
    detalhe: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "rotulo": self.rotulo,
            "valor": str(arredondar(self.valor)),
            "tipo": self.tipo,
            "percentual": str(self.percentual),
            "detalhe": self.detalhe,
        }


@dataclass(slots=True)
class DRE:
    inicio: datetime
    fim: datetime
    receita_bruta: Decimal = ZERO
    cancelamentos: Decimal = ZERO
    devolucoes: Decimal = ZERO
    receita_frete: Decimal = ZERO
    comissao: Decimal = ZERO
    taxa_pagamento: Decimal = ZERO
    custo_frete: Decimal = ZERO
    imposto_retido: Decimal = ZERO
    descontos: Decimal = ZERO
    liquido_recebido: Decimal = ZERO
    imposto_sobre_vendas: Decimal = ZERO
    cmv: Decimal = ZERO
    despesas: Decimal = ZERO
    despesas_por_categoria: dict[str, Decimal] = field(default_factory=dict)
    pedidos: int = 0
    unidades: Decimal = ZERO
    itens_sem_custo: int = 0
    pedidos_sem_imposto: int = 0

    # --- Resultados -------------------------------------------------------

    @property
    def receita_liquida_vendas(self) -> Decimal:
        """Bruto menos o que nunca virou receita (cancelado e devolvido)."""
        return self.receita_bruta - self.cancelamentos - self.devolucoes

    @property
    def total_taxas_canal(self) -> Decimal:
        return self.comissao + self.taxa_pagamento

    @property
    def componentes_do_liquido(self) -> Decimal:
        """Soma das deduções que o canal discrimina, item a item."""
        return (
            self.receita_liquida_vendas
            + self.receita_frete
            + self.descontos
            - self.comissao
            - self.taxa_pagamento
            - self.custo_frete
            - self.imposto_retido
        )

    @property
    def ajustes_nao_discriminados(self) -> Decimal:
        """Diferença entre o líquido informado pelo canal e a soma das taxas conhecidas.

        O líquido é importado, não recalculado — é o valor que o canal
        efetivamente repassa, e recalculá-lo faria o painel divergir do extrato.
        Só que a soma das taxas discriminadas quase nunca fecha exatamente com
        ele: parte do custo de frete é cobrada em fatura separada em vez de
        descontada do repasse, e cada canal ainda aplica ajustes que não
        detalha por pedido.

        Sem esta linha, o demonstrativo exibiria uma coluna de valores cuja soma
        não bate com o subtotal impresso logo abaixo — quem conferisse na mão
        encontraria outro número. A diferença aparece explícita, e o próprio
        tamanho dela é diagnóstico: se for grande, o detalhamento de taxas está
        incompleto e a conciliação é o lugar de investigar.
        """
        return self.liquido_recebido - self.componentes_do_liquido

    @property
    def margem_contribuicao(self) -> Decimal:
        """Sobra da venda depois de tudo que varia com ela.

        É o número que decide preço: se for negativo, vender mais aumenta o
        prejuízo.
        """
        return self.liquido_recebido - self.imposto_sobre_vendas - self.cmv

    @property
    def lucro_operacional(self) -> Decimal:
        """O lucro real do período, já com as despesas fixas."""
        return self.margem_contribuicao - self.despesas

    def _pct(self, valor: Decimal) -> Decimal:
        if self.receita_bruta <= ZERO:
            return ZERO
        return arredondar(valor / self.receita_bruta * Decimal("100"))

    @property
    def margem_contribuicao_pct(self) -> Decimal:
        return self._pct(self.margem_contribuicao)

    @property
    def lucro_operacional_pct(self) -> Decimal:
        return self._pct(self.lucro_operacional)

    @property
    def ticket_medio(self) -> Decimal:
        return arredondar(self.receita_bruta / self.pedidos) if self.pedidos else ZERO

    @property
    def lucro_por_pedido(self) -> Decimal:
        return arredondar(self.lucro_operacional / self.pedidos) if self.pedidos else ZERO

    @property
    def ponto_de_equilibrio(self) -> Decimal:
        """Receita bruta necessária no mês para o lucro operacional ser zero.

        Só faz sentido com margem de contribuição positiva: com margem
        negativa, nenhum volume cobre as despesas — vender mais só piora.
        """
        if self.receita_bruta <= ZERO or self.margem_contribuicao <= ZERO:
            return ZERO
        indice = self.margem_contribuicao / self.receita_bruta
        return arredondar(self.despesas / indice)

    def linhas(self) -> list[LinhaDRE]:
        """DRE na ordem de leitura, com o percentual sobre a receita bruta."""
        itens = [
            LinhaDRE("Receita bruta de vendas", self.receita_bruta, "receita"),
            LinhaDRE("(−) Cancelamentos", -self.cancelamentos, "deducao"),
            LinhaDRE("(−) Devoluções e reembolsos", -self.devolucoes, "deducao"),
            LinhaDRE("(=) Receita líquida de vendas", self.receita_liquida_vendas, "subtotal"),
            LinhaDRE("(+) Frete cobrado do comprador", self.receita_frete, "receita"),
            LinhaDRE("(−) Comissão do marketplace", -self.comissao, "deducao"),
            LinhaDRE("(−) Taxa de meio de pagamento", -self.taxa_pagamento, "deducao"),
            LinhaDRE("(−) Custo de frete", -self.custo_frete, "deducao"),
            LinhaDRE("(−) Imposto retido pelo canal", -self.imposto_retido, "deducao"),
            LinhaDRE("(+) Descontos e bônus do canal", self.descontos, "receita"),
            LinhaDRE(
                "(±) Ajustes não discriminados pelo canal",
                self.ajustes_nao_discriminados,
                "deducao",
                detalhe=(
                    "Diferença entre o líquido repassado e a soma das taxas que o "
                    "canal detalha. Parte do frete é faturada à parte, e nem todo "
                    "ajuste vem discriminado por pedido. Valor alto aqui indica "
                    "detalhamento incompleto — investigue em Conciliação."
                ),
            ),
            LinhaDRE(
                "(=) Líquido recebido dos canais",
                self.liquido_recebido,
                "subtotal",
                detalhe="O que efetivamente foi depositado ou está previsto.",
            ),
            LinhaDRE(
                "(−) Imposto sobre vendas",
                -self.imposto_sobre_vendas,
                "deducao",
                detalhe=(
                    "Apurado pelo regime do vendedor. Não é descontado pelo canal: "
                    "o valor chega cheio e o tributo é recolhido depois."
                ),
            ),
            LinhaDRE(
                "(−) CMV",
                -self.cmv,
                "deducao",
                detalhe="Custo do produto e da embalagem, congelado na data da venda.",
            ),
            LinhaDRE(
                "(=) Margem de contribuição",
                self.margem_contribuicao,
                "subtotal",
                detalhe="Sobra de cada venda antes das despesas fixas.",
            ),
            LinhaDRE("(−) Despesas operacionais", -self.despesas, "deducao"),
            LinhaDRE(
                "(=) Lucro operacional",
                self.lucro_operacional,
                "resultado",
                detalhe="O lucro real do período.",
            ),
        ]
        for linha in itens:
            linha.percentual = self._pct(abs(linha.valor))
        return itens

    def como_dict(self) -> dict[str, Any]:
        return {
            "periodo": {"inicio": self.inicio, "fim": self.fim},
            "linhas": [linha.como_dict() for linha in self.linhas()],
            "indicadores": {
                "pedidos": self.pedidos,
                "unidades": str(self.unidades),
                "ticket_medio": str(self.ticket_medio),
                "margem_contribuicao": str(arredondar(self.margem_contribuicao)),
                "margem_contribuicao_pct": str(self.margem_contribuicao_pct),
                "lucro_operacional": str(arredondar(self.lucro_operacional)),
                "lucro_operacional_pct": str(self.lucro_operacional_pct),
                "lucro_por_pedido": str(self.lucro_por_pedido),
                "ponto_de_equilibrio": str(self.ponto_de_equilibrio),
                "taxa_efetiva_canal_pct": str(self._pct(self.total_taxas_canal)),
                "carga_tributaria_pct": str(self._pct(self.imposto_sobre_vendas)),
                "ajustes_nao_discriminados": str(arredondar(self.ajustes_nao_discriminados)),
            },
            "despesas_por_categoria": {
                k: str(arredondar(v)) for k, v in self.despesas_por_categoria.items()
            },
            "qualidade": {
                "itens_sem_custo": self.itens_sem_custo,
                "pedidos_sem_imposto": self.pedidos_sem_imposto,
                "confiavel": self.itens_sem_custo == 0 and self.pedidos_sem_imposto == 0,
                "aviso": _aviso(self.itens_sem_custo, self.pedidos_sem_imposto),
            },
        }


def _aviso(sem_custo: int, sem_imposto: int) -> str:
    """Explicita o que falta em vez de exibir um resultado incompleto como final.

    Um DRE com custo faltando mostra lucro maior do que o real — e é exatamente
    o tipo de número em que alguém baseia uma decisão de preço.
    """
    partes = []
    if sem_custo:
        partes.append(
            f"{sem_custo} itens sem custo cadastrado: o CMV está subestimado e o "
            f"lucro aparece maior do que é. Cadastre o custo em Produtos."
        )
    if sem_imposto:
        partes.append(
            f"{sem_imposto} pedidos sem regra tributária vigente: o imposto está "
            f"zerado. Cadastre a regra em Custos e Impostos."
        )
    return " ".join(partes)


async def apurar(
    db: AsyncSession,
    tenant_id: int,
    *,
    inicio: datetime,
    fim: datetime,
    canal: str | None = None,
) -> DRE:
    """Monta o DRE do período."""
    from app.models.order import OrderItem

    dre = DRE(inicio=inicio, fim=fim)

    filtros = [
        Order.tenant_id == tenant_id,
        Order.date_created >= inicio,
        Order.date_created <= fim,
    ]
    if canal:
        filtros.append(Order.channel == canal)

    validos = [*filtros, Order.status != StatusPedido.CANCELADO]

    linha = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.gross_amount), 0),
                func.coalesce(func.sum(Order.shipping_revenue), 0),
                func.coalesce(func.sum(Order.platform_fee), 0),
                func.coalesce(func.sum(Order.payment_fee), 0),
                func.coalesce(func.sum(Order.shipping_cost), 0),
                func.coalesce(func.sum(Order.tax_amount), 0),
                func.coalesce(func.sum(Order.discount_amount), 0),
                func.coalesce(func.sum(Order.refund_amount), 0),
                func.coalesce(func.sum(Order.net_amount), 0),
                func.coalesce(func.sum(Order.sales_tax_amount), 0),
                func.coalesce(func.sum(Order.cogs), 0),
            ).where(*validos)
        )
    ).one()

    (
        dre.pedidos,
        dre.receita_bruta,
        dre.receita_frete,
        dre.comissao,
        dre.taxa_pagamento,
        dre.custo_frete,
        dre.imposto_retido,
        dre.descontos,
        dre.devolucoes,
        dre.liquido_recebido,
        dre.imposto_sobre_vendas,
        dre.cmv,
    ) = (int(linha[0] or 0), *(Decimal(str(v or 0)) for v in linha[1:]))

    cancelado = await db.scalar(
        select(func.coalesce(func.sum(Order.gross_amount), 0)).where(
            *filtros, Order.status == StatusPedido.CANCELADO
        )
    )
    dre.cancelamentos = Decimal(str(cancelado or 0))

    dre.unidades = Decimal(
        str(
            await db.scalar(
                select(func.coalesce(func.sum(OrderItem.quantity), 0))
                .join(Order, Order.id == OrderItem.order_id)
                .where(*validos)
            )
            or 0
        )
    )

    # --- Despesas do período, por categoria --------------------------------
    consulta_despesas = select(
        OperatingExpense.category, func.coalesce(func.sum(OperatingExpense.amount), 0)
    ).where(
        OperatingExpense.tenant_id == tenant_id,
        OperatingExpense.competence_month >= inicio.date().replace(day=1),
        OperatingExpense.competence_month <= fim.date(),
    )
    if canal:
        consulta_despesas = consulta_despesas.where(
            (OperatingExpense.channel == canal) | (OperatingExpense.channel == "")
        )

    for categoria, total in (await db.execute(consulta_despesas.group_by(OperatingExpense.category))).all():
        valor = Decimal(str(total or 0))
        dre.despesas_por_categoria[str(categoria)] = valor
        dre.despesas += valor

    # --- Sinalização de qualidade do dado ----------------------------------
    dre.itens_sem_custo = int(
        await db.scalar(
            select(func.count(OrderItem.id))
            .join(Order, Order.id == OrderItem.order_id)
            .where(*validos, OrderItem.unit_cost == 0)
        )
        or 0
    )
    dre.pedidos_sem_imposto = int(
        await db.scalar(select(func.count(Order.id)).where(*validos, Order.tax_rule_id.is_(None)))
        or 0
    )

    return dre


def somar_meses(quando: datetime, delta: int) -> datetime:
    """Aritmética de mês sem dependência externa.

    Sempre no primeiro dia, então não há o problema de "31 de janeiro + 1 mês".
    """
    total = (quando.year * 12 + quando.month - 1) + delta
    return quando.replace(year=total // 12, month=total % 12 + 1, day=1)


async def apurar_por_mes(
    db: AsyncSession, tenant_id: int, *, meses: int = 12
) -> list[dict[str, Any]]:
    """Série mensal do lucro — mostra a tendência, não só a foto do período."""
    from datetime import UTC

    resultado: list[dict[str, Any]] = []
    base = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    for indice in range(meses - 1, -1, -1):
        inicio = somar_meses(base, -indice)
        fim = somar_meses(inicio, 1)
        dre = await apurar(db, tenant_id, inicio=inicio, fim=fim)
        resultado.append(
            {
                "mes": inicio.date().isoformat()[:7],
                "receita_bruta": str(arredondar(dre.receita_bruta)),
                "liquido": str(arredondar(dre.liquido_recebido)),
                "margem_contribuicao": str(arredondar(dre.margem_contribuicao)),
                "lucro_operacional": str(arredondar(dre.lucro_operacional)),
                "lucro_pct": str(dre.lucro_operacional_pct),
                "pedidos": dre.pedidos,
            }
        )
    return resultado


def primeiro_dia_do_mes(quando: datetime | date) -> date:
    d = quando.date() if isinstance(quando, datetime) else quando
    return d.replace(day=1)
