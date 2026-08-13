"""Enriquecimento posterior do frete e recálculo do líquido.

O backfill de volume alto importa sem buscar frete, para não triplicar as
chamadas à API. O worker completa depois — e precisa refazer a conta do
líquido, porque o frete é dedução. Gravar o custo sem recalcular deixaria o
pedido com o frete registrado e o líquido de antes, inflado exatamente pelo
valor do frete.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import FonteLiquido, StatusPedido
from app.models.order import Order
from app.workers.tasks import _recalcular_liquido


def _pedido(**kw) -> Order:
    base = dict(
        tenant_id=1,
        channel_account_id=1,
        channel="mercadolivre",
        external_id="X",
        status=StatusPedido.ENTREGUE,
        date_created=datetime.now(UTC),
        gross_amount=Decimal("1000"),
        shipping_revenue=Decimal("0"),
        platform_fee=Decimal("150"),
        payment_fee=Decimal("0"),
        shipping_cost=Decimal("0"),
        tax_amount=Decimal("0"),
        discount_amount=Decimal("0"),
        refund_amount=Decimal("0"),
        net_amount=Decimal("850"),
        net_source=FonteLiquido.CALCULADO,
    )
    base.update(kw)
    return Order(**base)


def test_frete_reduz_o_liquido():
    """Sem isto, o painel mostra lucro que não existe."""
    pedido = _pedido(shipping_cost=Decimal("200"))
    _recalcular_liquido(pedido)
    assert pedido.net_amount == Decimal("650.00")


def test_nao_sobrescreve_liquido_informado_pelo_canal():
    """O número do canal vale mais que a nossa conta.

    Recalcular por cima faria o painel divergir do extrato que o vendedor
    confere no marketplace — o erro que mais destrói confiança no sistema.
    """
    pedido = _pedido(shipping_cost=Decimal("200"), net_source=FonteLiquido.REPORTADO_API)
    _recalcular_liquido(pedido)
    assert pedido.net_amount == Decimal("850")


def test_nao_sobrescreve_valor_liquidado():
    pedido = _pedido(shipping_cost=Decimal("200"), net_source=FonteLiquido.LIQUIDADO)
    _recalcular_liquido(pedido)
    assert pedido.net_amount == Decimal("850")


def test_cancelado_permanece_intocado():
    pedido = _pedido(status=StatusPedido.CANCELADO, shipping_cost=Decimal("200"))
    _recalcular_liquido(pedido)
    assert pedido.net_amount == Decimal("850")


def test_soma_todas_as_parcelas():
    pedido = _pedido(
        gross_amount=Decimal("1000"),
        shipping_revenue=Decimal("50"),
        platform_fee=Decimal("150"),
        payment_fee=Decimal("30"),
        shipping_cost=Decimal("120"),
        tax_amount=Decimal("10"),
        discount_amount=Decimal("25"),
        refund_amount=Decimal("40"),
    )
    _recalcular_liquido(pedido)
    # 1000 + 50 − 150 − 30 − 120 − 10 + 25 − 40
    assert pedido.net_amount == Decimal("725.00")


def test_liquido_nunca_supera_o_bruto_mais_frete_cobrado():
    """A invariante que pega dedução com sinal trocado."""
    pedido = _pedido(shipping_cost=Decimal("200"), shipping_revenue=Decimal("30"))
    _recalcular_liquido(pedido)
    assert pedido.net_amount <= pedido.gross_amount + pedido.shipping_revenue
