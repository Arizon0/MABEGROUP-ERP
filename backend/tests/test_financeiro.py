"""Testes do motor financeiro.

Toda regra aqui existe porque um erro nela produz um número errado no painel de
alguém — e número financeiro errado é o pior tipo de bug deste produto: ninguém
percebe até o fechamento do mês não bater.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.connectors.base import CanonicalFee, CanonicalOrder, CanonicalOrderItem, CanonicalPayment
from app.models.enums import FonteLiquido, StatusPagamento, StatusPedido, TipoTaxa
from app.services import finance


def pedido(**kwargs) -> CanonicalOrder:
    base = {
        "external_id": "2000012345",
        "channel": "mercadolivre",
        "date_created": datetime.now(UTC),
        "status": StatusPedido.PAGO,
    }
    base.update(kwargs)
    return CanonicalOrder(**base)


def item(preco: str, qtd: str = "1", **kwargs) -> CanonicalOrderItem:
    return CanonicalOrderItem(
        unit_price=Decimal(preco), quantity=Decimal(qtd), gross_amount=Decimal(preco) * Decimal(qtd), **kwargs
    )


class TestReceitaBruta:
    def test_soma_os_itens(self):
        p = pedido(items=[item("129.90", "2"), item("38.90")])
        assert finance.calcular_bruto(p) == Decimal("298.70")

    def test_frete_cobrado_nao_entra_na_receita(self):
        """Frete é repasse de custo logístico, não venda.

        Somá-lo inflaria a receita e distorceria o ticket médio — o vendedor
        acharia que vende mais caro do que vende.
        """
        p = pedido(items=[item("100.00")], shipping_revenue=Decimal("21.90"))
        assert finance.calcular_bruto(p) == Decimal("100.00")

    def test_precisao_de_centavo_em_valores_quebrados(self):
        """Com float, 0.1 + 0.2 != 0.3. Com Decimal, o centavo fecha."""
        p = pedido(items=[item("0.10"), item("0.20")])
        assert finance.calcular_bruto(p) == Decimal("0.30")


class TestReceitaLiquida:
    def test_desconta_taxas_frete_e_soma_bonus(self):
        p = pedido(
            items=[item("100.00")],
            shipping_revenue=Decimal("20.00"),
            platform_fee=Decimal("15.50"),
            payment_fee=Decimal("4.99"),
            shipping_cost=Decimal("18.00"),
            discount_amount=Decimal("5.00"),
        )
        liquido, fonte = finance.calcular_liquido(p)
        # 100 + 20 − 15,50 − 4,99 − 18 + 5
        assert liquido == Decimal("86.51")
        assert fonte == FonteLiquido.CALCULADO

    def test_respeita_o_liquido_informado_pelo_canal(self):
        """Quando o canal informa o líquido, ele é fonte primária.

        Recalcular por conta própria criaria divergência com o extrato que o
        vendedor vê no painel do próprio marketplace.
        """
        p = pedido(
            items=[item("100.00")],
            net_amount=Decimal("81.23"),
            net_source=FonteLiquido.REPORTADO_API,
        )
        liquido, fonte = finance.calcular_liquido(p)
        assert liquido == Decimal("81.23")
        assert fonte == FonteLiquido.REPORTADO_API

    def test_pedido_cancelado_nao_gera_liquido(self):
        p = pedido(status=StatusPedido.CANCELADO, items=[item("100.00")], net_amount=Decimal("80"))
        liquido, _ = finance.calcular_liquido(p)
        assert liquido == Decimal("0")


class TestAplicarPagamentos:
    def test_separa_comissao_do_marketplace_das_demais_taxas(self):
        """São custos distintos: um se negocia com o canal, outro com o meio
        de pagamento. Agregá-los esconde qual dos dois subiu."""
        p = pedido(items=[item("100.00")])
        pagamento = CanonicalPayment(
            external_id="1",
            channel="mercadolivre",
            provider="mercadopago",
            status=StatusPagamento.APROVADO,
            net_received_amount=Decimal("80.00"),
            fees=[
                CanonicalFee(fee_type=TipoTaxa.COMISSAO_MARKETPLACE, amount=Decimal("15.00")),
                CanonicalFee(fee_type=TipoTaxa.TAXA_PAGAMENTO, amount=Decimal("4.00")),
                CanonicalFee(fee_type=TipoTaxa.TAXA_PARCELAMENTO, amount=Decimal("1.00")),
            ],
        )
        finance.aplicar_pagamentos(p, [pagamento])

        assert p.platform_fee == Decimal("15.00")
        assert p.payment_fee == Decimal("5.00")  # pagamento + parcelamento
        assert p.net_amount == Decimal("80.00")
        assert p.net_source == FonteLiquido.REPORTADO_API

    def test_deduplica_o_mesmo_pagamento_entregue_duas_vezes(self):
        """Webhook e polling podem trazer o mesmo pagamento na mesma rodada."""
        p = pedido(items=[item("100.00")])
        pagamento = CanonicalPayment(
            external_id="dup-1",
            channel="mercadolivre",
            provider="mercadopago",
            status=StatusPagamento.APROVADO,
            net_received_amount=Decimal("80.00"),
        )
        finance.aplicar_pagamentos(p, [pagamento, pagamento])
        assert p.net_amount == Decimal("80.00")

    def test_rejeita_liquido_maior_que_o_total_pago(self):
        """Só acontece com pagamento associado ao pedido errado.

        Aceitar produziria margem acima de 100% no painel — o tipo de número
        impossível que destrói a confiança em toda a tela.
        """
        p = pedido(items=[item("100.00")])
        absurdo = CanonicalPayment(
            external_id="x",
            channel="mercadolivre",
            provider="mercadopago",
            status=StatusPagamento.APROVADO,
            net_received_amount=Decimal("5000.00"),
        )
        finance.aplicar_pagamentos(p, [absurdo])
        assert p.net_amount is None  # estimativa preservada, valor absurdo descartado


class TestConsolidacao:
    def test_exclui_cancelados_da_receita_mas_os_contabiliza(self):
        pedidos = [
            pedido(items=[item("100.00")], net_amount=Decimal("80")),
            pedido(items=[item("50.00")], net_amount=Decimal("40")),
            pedido(status=StatusPedido.CANCELADO, items=[item("70.00")]),
        ]
        resumo = finance.consolidar(pedidos)

        assert resumo.pedidos == 2
        assert resumo.receita_bruta == Decimal("150.00")
        assert resumo.cancelados == 1
        assert resumo.valor_cancelado == Decimal("70.00")

    def test_ticket_medio_ignora_cancelados(self):
        pedidos = [
            pedido(items=[item("100.00")]),
            pedido(items=[item("200.00")]),
            pedido(status=StatusPedido.CANCELADO, items=[item("900.00")]),
        ]
        assert finance.consolidar(pedidos).ticket_medio == Decimal("150.00")

    def test_taxa_efetiva_expressa_o_peso_das_taxas_sobre_o_bruto(self):
        pedidos = [
            pedido(
                items=[item("1000.00")],
                platform_fee=Decimal("155.00"),
                payment_fee=Decimal("45.00"),
                net_amount=Decimal("800.00"),
            )
        ]
        assert finance.consolidar(pedidos).taxa_efetiva == Decimal("20.00")

    def test_margem_desconta_o_custo_do_produto(self):
        pedidos = [pedido(items=[item("100.00")], net_amount=Decimal("80"), cogs=Decimal("30"))]
        resumo = finance.consolidar(pedidos)
        assert resumo.margem_contribuicao == Decimal("50.00")
        assert resumo.margem_pct == Decimal("50.00")


class TestArredondamento:
    def test_arredonda_meio_centavo_para_cima(self):
        assert finance.arredondar(Decimal("10.005")) == Decimal("10.01")

    @pytest.mark.parametrize(
        "entrada,esperado",
        [("0.014", "0.01"), ("0.015", "0.02"), ("-0.015", "-0.02")],
    )
    def test_regra_consistente(self, entrada: str, esperado: str):
        assert finance.arredondar(Decimal(entrada)) == Decimal(esperado)
