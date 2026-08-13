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

from sqlalchemy import func, select

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


# --- A tarefa em si ----------------------------------------------------------

def test_extrai_id_do_envio_do_payload_do_mercado_livre():
    """O identificador vem do payload guardado, não da tabela de envios.

    Quando o backfill pula o enriquecimento, nenhum registro de envio chega a
    existir — não há de onde partir a não ser do que foi salvo na importação.
    """
    from app.workers.tasks import _id_do_envio

    pedido = _pedido()
    pedido.channel = "mercadolivre"
    pedido.raw = {"shipping": {"id": 47275271472}}
    assert _id_do_envio(pedido) == "47275271472"


def test_extrai_id_do_envio_da_shopee():
    from app.workers.tasks import _id_do_envio

    pedido = _pedido()
    pedido.channel = "shopee"
    pedido.raw = {"package_number": "PKG-99"}
    assert _id_do_envio(pedido) == "PKG-99"


def test_pedido_sem_envio_nao_quebra():
    """Venda retirada em mãos ou payload sem o campo."""
    from app.workers.tasks import _id_do_envio

    pedido = _pedido()
    pedido.channel = "mercadolivre"
    pedido.raw = {}
    assert _id_do_envio(pedido) == ""


async def test_tarefa_roda_e_reduz_o_liquido(db, conta):
    """Executa a tarefa de ponta a ponta contra o conector simulado.

    A primeira versão desta tarefa consultava um campo inexistente no modelo e
    falhava em toda execução. Nenhum teste a executava — só a rodada real
    revelou. Este teste existe para que isso não se repita.
    """
    from app.services import sync
    from app.workers import tasks

    await sync.sincronizar_pedidos(db, conta, enriquecer=False)

    # O recálculo só age sobre líquido **calculado por nós** — valor informado
    # pelo canal é respeitado. O cenário que importa aqui é o do backfill sem
    # enriquecimento, em que o líquido é sempre estimado.
    for pedido in (await db.execute(select(Order))).scalars():
        pedido.net_source = FonteLiquido.CALCULADO
    await db.commit()

    antes = await db.scalar(
        select(func.coalesce(func.sum(Order.net_amount), 0)).where(
            Order.status != StatusPedido.CANCELADO
        )
    )
    resultado = await tasks.enriquecer_pedidos({}, limite=40)

    assert resultado["enriquecidos"] > 0, f"nada foi enriquecido: {resultado}"

    depois = await db.scalar(
        select(func.coalesce(func.sum(Order.net_amount), 0)).where(
            Order.status != StatusPedido.CANCELADO
        )
    )
    # O frete é dedução: o líquido tem de cair, nunca subir.
    assert depois < antes, f"líquido não caiu: {antes} → {depois}"
