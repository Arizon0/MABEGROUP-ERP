"""Motor financeiro: faturamento bruto, líquido e margem.

Regras invioláveis deste módulo (ver ``docs/06-financeiro-conciliacao.md``):

1. Todo cálculo em ``Decimal``. Nunca ``float``.
2. Arredondamento **só na apresentação**. Arredondar em etapa intermediária é o
   que produz a divergência de centavos que ninguém consegue explicar depois.
3. Todo líquido carrega a procedência (``net_source``). Estimativa e valor
   liquidado somados no mesmo indicador, sem distinção, fazem o painel divergir
   do extrato do vendedor.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from app.connectors.base import CanonicalOrder, CanonicalPayment
from app.models.enums import STATUS_NAO_FATURAVEIS, FonteLiquido, StatusPedido, TipoTaxa

ZERO = Decimal("0")
CENTAVO = Decimal("0.01")


def arredondar(valor: Decimal) -> Decimal:
    """Arredonda para 2 casas. Usar **apenas** ao apresentar ou persistir total."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class ResumoFinanceiro:
    """Composição completa do faturamento de um conjunto de pedidos."""

    receita_bruta: Decimal = ZERO
    receita_frete: Decimal = ZERO
    comissao_marketplace: Decimal = ZERO
    taxa_pagamento: Decimal = ZERO
    custo_frete: Decimal = ZERO
    impostos: Decimal = ZERO
    descontos: Decimal = ZERO
    reembolsos: Decimal = ZERO
    liquido: Decimal = ZERO
    cmv: Decimal = ZERO
    pedidos: int = 0
    unidades: Decimal = ZERO
    cancelados: int = 0
    valor_cancelado: Decimal = ZERO

    @property
    def taxas_totais(self) -> Decimal:
        return self.comissao_marketplace + self.taxa_pagamento

    @property
    def ticket_medio(self) -> Decimal:
        return arredondar(self.receita_bruta / self.pedidos) if self.pedidos else ZERO

    @property
    def taxa_efetiva(self) -> Decimal:
        """Percentual que as taxas representam sobre o bruto."""
        if self.receita_bruta <= ZERO:
            return ZERO
        return arredondar(self.taxas_totais / self.receita_bruta * 100)

    @property
    def margem_contribuicao(self) -> Decimal:
        return self.liquido - self.cmv

    @property
    def margem_pct(self) -> Decimal:
        if self.receita_bruta <= ZERO:
            return ZERO
        return arredondar(self.margem_contribuicao / self.receita_bruta * 100)

    def como_dict(self) -> dict[str, str | int]:
        """Serializa com valores em string, preservando a exatidão do Decimal."""
        return {
            "receita_bruta": str(arredondar(self.receita_bruta)),
            "receita_frete": str(arredondar(self.receita_frete)),
            "comissao_marketplace": str(arredondar(self.comissao_marketplace)),
            "taxa_pagamento": str(arredondar(self.taxa_pagamento)),
            "taxas_totais": str(arredondar(self.taxas_totais)),
            "custo_frete": str(arredondar(self.custo_frete)),
            "impostos": str(arredondar(self.impostos)),
            "descontos": str(arredondar(self.descontos)),
            "reembolsos": str(arredondar(self.reembolsos)),
            "liquido": str(arredondar(self.liquido)),
            "cmv": str(arredondar(self.cmv)),
            "margem_contribuicao": str(arredondar(self.margem_contribuicao)),
            "margem_pct": str(self.margem_pct),
            "ticket_medio": str(self.ticket_medio),
            "taxa_efetiva_pct": str(self.taxa_efetiva),
            "pedidos": self.pedidos,
            "unidades": str(self.unidades),
            "cancelados": self.cancelados,
            "valor_cancelado": str(arredondar(self.valor_cancelado)),
        }


def calcular_bruto(pedido: CanonicalOrder) -> Decimal:
    """Receita bruta do pedido: soma dos itens.

    O frete cobrado do comprador **não** entra: é repasse de custo logístico, não
    venda. Fica em linha separada (``shipping_revenue``) para não inflar a
    receita nem distorcer o ticket médio.
    """
    if pedido.items:
        return sum((i.unit_price * i.quantity for i in pedido.items), ZERO)
    return pedido.gross_amount


def calcular_liquido(pedido: CanonicalOrder) -> tuple[Decimal, FonteLiquido]:
    """Calcula o líquido do pedido e informa a procedência do número.

    Se o canal já informou um líquido confiável, ele é respeitado — recalcular
    aqui só criaria divergência com o extrato que o vendedor vê no painel do
    próprio marketplace.
    """
    if pedido.status in STATUS_NAO_FATURAVEIS:
        return ZERO, FonteLiquido.LIQUIDADO

    if pedido.net_amount is not None and pedido.net_amount != ZERO:
        return pedido.net_amount, pedido.net_source

    bruto = calcular_bruto(pedido)
    liquido = (
        bruto
        + pedido.shipping_revenue
        - pedido.platform_fee
        - pedido.payment_fee
        - pedido.shipping_cost
        - pedido.tax_amount
        + pedido.discount_amount
        - pedido.refund_amount
    )
    return liquido, FonteLiquido.CALCULADO


def aplicar_pagamentos(
    pedido: CanonicalOrder, pagamentos: Iterable[CanonicalPayment]
) -> CanonicalOrder:
    """Enriquece o pedido com os dados financeiros do provedor de pagamento.

    Separa a comissão do marketplace das demais taxas: o Mercado Pago devolve
    ambas misturadas em ``fee_details``, mas gerencialmente são custos distintos
    — uma se negocia com o marketplace, a outra com o meio de pagamento.
    """
    # Deduplica por identificador externo: webhook e polling podem trazer o
    # mesmo pagamento na mesma rodada, e somá-lo duas vezes dobraria o líquido.
    unicos: dict[str, CanonicalPayment] = {}
    for pagamento in pagamentos:
        unicos.setdefault(pagamento.external_id, pagamento)
    pagamentos = list(unicos.values())
    if not pagamentos:
        return pedido

    aprovados = [p for p in pagamentos if p.status == "approved"] or pagamentos

    taxa_pagamento = ZERO
    comissao = ZERO
    liquido = ZERO
    reembolsos = ZERO

    for pagamento in aprovados:
        for taxa in pagamento.fees:
            if taxa.fee_type == TipoTaxa.COMISSAO_MARKETPLACE:
                comissao += taxa.amount
            elif taxa.fee_type != TipoTaxa.TAXA_ENVIO:
                taxa_pagamento += taxa.amount
        liquido += pagamento.net_received_amount
        reembolsos += sum(
            (Decimal(str(r.get("amount", 0))) for r in pagamento.refunds), ZERO
        )

    pedido.payment_fee = taxa_pagamento
    if comissao > ZERO:
        pedido.platform_fee = comissao
    pedido.refund_amount = reembolsos

    if liquido > ZERO:
        # Verificação de sanidade: o líquido nunca pode superar o que o
        # comprador pagou. Ultrapassar esse teto significa pagamento associado
        # ao pedido errado ou registro duplicado — e aceitar o número produziria
        # margem acima de 100% no painel, o tipo de resultado impossível que
        # derruba a confiança em todos os outros números da tela.
        teto = calcular_bruto(pedido) + pedido.shipping_revenue
        if teto > ZERO and liquido > teto:
            return pedido
        pedido.net_amount = liquido - reembolsos
        pedido.net_source = FonteLiquido.REPORTADO_API

    return pedido


def consolidar(pedidos: Iterable[Any]) -> ResumoFinanceiro:
    """Consolida uma coleção de pedidos (canônicos ou persistidos).

    Aceita as duas formas de propósito: a ingestão trabalha com DTOs canônicos e
    os relatórios com registros do banco, mas a regra de agregação é a mesma e
    não deve existir em duas versões que possam divergir.
    """
    resumo = ResumoFinanceiro()

    for pedido in pedidos:
        status = getattr(pedido, "status", StatusPedido.PENDENTE)
        bruto = _bruto_de(pedido)
        unidades = sum(
            (Decimal(str(i.quantity)) for i in getattr(pedido, "items", []) or []), ZERO
        )

        if status in STATUS_NAO_FATURAVEIS:
            resumo.cancelados += 1
            resumo.valor_cancelado += bruto
            continue

        resumo.pedidos += 1
        resumo.unidades += unidades
        resumo.receita_bruta += bruto
        resumo.receita_frete += _dec(getattr(pedido, "shipping_revenue", ZERO))
        resumo.comissao_marketplace += _dec(getattr(pedido, "platform_fee", ZERO))
        resumo.taxa_pagamento += _dec(getattr(pedido, "payment_fee", ZERO))
        resumo.custo_frete += _dec(getattr(pedido, "shipping_cost", ZERO))
        resumo.impostos += _dec(getattr(pedido, "tax_amount", ZERO))
        resumo.descontos += _dec(getattr(pedido, "discount_amount", ZERO))
        resumo.reembolsos += _dec(getattr(pedido, "refund_amount", ZERO))
        resumo.cmv += _dec(getattr(pedido, "cogs", ZERO))

        liquido = getattr(pedido, "net_amount", None)
        if liquido is None or _dec(liquido) == ZERO:
            liquido, _ = calcular_liquido(pedido) if hasattr(pedido, "items") else (ZERO, None)
        resumo.liquido += _dec(liquido)

    return resumo


def calcular_cmv(itens: Iterable[Any]) -> Decimal:
    """CMV a partir do custo congelado em cada item."""
    return sum((_dec(i.unit_cost) * _dec(i.quantity) for i in itens), ZERO)


def _bruto_de(pedido: Any) -> Decimal:
    itens = getattr(pedido, "items", None)
    if itens:
        return sum((_dec(i.unit_price) * _dec(i.quantity) for i in itens), ZERO)
    return _dec(getattr(pedido, "gross_amount", ZERO))


def _dec(valor: Any) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if valor is None:
        return ZERO
    return Decimal(str(valor))
