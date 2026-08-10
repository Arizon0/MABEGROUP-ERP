"""Testes dos conectores: normalização e assinatura.

Os normalizadores são funções puras sobre dicionário — por isso testáveis com
payloads reais gravados, sem rede. Os payloads abaixo reproduzem a estrutura das
respostas oficiais, incluindo as armadilhas documentadas em
``docs/10-riscos-limitacoes.md``.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.connectors.mercadolivre import normalizer as ml
from app.connectors.shopee import normalizer as shopee
from app.models.enums import CanalLogistico, StatusPagamento, StatusPedido


# --- Mercado Livre -----------------------------------------------------------

PEDIDO_ML = {
    "id": 2000012345,
    "status": "paid",
    "status_detail": None,
    "date_created": "2026-07-15T14:22:31.000-03:00",
    "last_updated": "2026-07-16T09:10:00.000-03:00",
    "currency_id": "BRL",
    "total_amount": 259.80,
    "pack_id": 987654,
    "order_items": [
        {
            "item": {
                "id": "MLB1234567890",
                "title": "Jogo de Anéis de Pistão 0.50mm",
                "seller_sku": "8126STD",
                "variation_id": None,
                "variation_attributes": [],
            },
            "quantity": 2,
            "unit_price": 129.90,
            "sale_fee": 20.14,
        }
    ],
    "payments": [{"id": 55555555555}],
    "shipping": {
        "id": 44444444444,
        "logistic_type": "fulfillment",
        "receiver_address": {"state": {"name": "São Paulo"}, "city": {"name": "Campinas"}},
    },
    "buyer": {"id": 111222333, "nickname": "COMPRADOR123"},
}


class TestNormalizadorMercadoLivre:
    def test_extrai_os_campos_essenciais(self):
        p = ml.normalizar_pedido(PEDIDO_ML)
        assert p.external_id == "2000012345"
        assert p.status == StatusPedido.PAGO
        assert p.currency == "BRL"
        assert p.buyer_nickname == "COMPRADOR123"
        assert p.ship_state == "São Paulo"
        assert p.ship_city == "Campinas"

    def test_calcula_o_bruto_multiplicando_preco_por_quantidade(self):
        p = ml.normalizar_pedido(PEDIDO_ML)
        assert p.gross_amount == Decimal("259.80")

    def test_sale_fee_e_por_unidade_e_acompanha_a_quantidade(self):
        """O ``sale_fee`` do ML é unitário.

        Tratá-lo como total da linha subestimaria a comissão pela metade num
        pedido de 2 unidades — e o líquido apareceria maior do que é.
        """
        p = ml.normalizar_pedido(PEDIDO_ML)
        assert p.platform_fee == Decimal("40.28")  # 20,14 × 2

    def test_traduz_o_canal_logistico(self):
        p = ml.normalizar_pedido(PEDIDO_ML)
        assert p.logistic_type == CanalLogistico.FULFILLMENT

    def test_preserva_o_pacote_para_evitar_dupla_contagem(self):
        p = ml.normalizar_pedido(PEDIDO_ML)
        assert p.external_pack_id == "987654"

    def test_pedido_cancelado_e_reconhecido(self):
        p = ml.normalizar_pedido({**PEDIDO_ML, "status": "cancelled"})
        assert p.status == StatusPedido.CANCELADO

    def test_status_desconhecido_nao_quebra_a_importacao(self):
        """Marketplaces criam status novos sem avisar; o pedido precisa entrar
        mesmo assim, com o valor original preservado em ``status_raw``."""
        p = ml.normalizar_pedido({**PEDIDO_ML, "status": "status_inedito_2027"})
        assert p.status == StatusPedido.PENDENTE
        assert p.status_raw == "status_inedito_2027"

    def test_payload_vazio_nao_levanta_excecao(self):
        p = ml.normalizar_pedido({})
        assert p.gross_amount == Decimal("0")
        assert p.items == []


PAGAMENTO_MP = {
    "id": 55555555555,
    "status": "approved",
    "status_detail": "accredited",
    "transaction_amount": 259.80,
    "shipping_amount": 0,
    "currency_id": "BRL",
    "installments": 3,
    "payment_method_id": "master",
    "date_approved": "2026-07-15T14:23:10.000-03:00",
    "money_release_date": "2026-07-29T14:23:10.000-03:00",
    "money_release_status": "pending",
    "fee_details": [
        {"type": "mercadopago_fee", "amount": 12.99, "fee_payer": "collector"},
        {"type": "financing_fee", "amount": 6.20, "fee_payer": "collector"},
    ],
    "transaction_details": {"net_received_amount": 240.61, "total_paid_amount": 259.80},
}


class TestNormalizadorMercadoPago:
    def test_usa_o_liquido_oficial_do_provedor(self):
        p = ml.normalizar_pagamento(PAGAMENTO_MP)
        assert p.net_received_amount == Decimal("240.61")
        assert p.status == StatusPagamento.APROVADO

    def test_traduz_cada_taxa_para_o_tipo_canonico(self):
        p = ml.normalizar_pagamento(PAGAMENTO_MP)
        tipos = {t.fee_type for t in p.fees}
        assert "payment_fee" in tipos
        assert "financing_fee" in tipos
        assert p.fee_total == Decimal("19.19")

    def test_preserva_a_data_de_liberacao_do_dinheiro(self):
        """É o que permite projetar caixa: saber *quando* o valor entra vale
        mais operacionalmente do que saber que a venda ocorreu."""
        p = ml.normalizar_pagamento(PAGAMENTO_MP)
        assert p.money_release_date is not None
        assert p.money_release_date.day == 29


# --- Shopee ------------------------------------------------------------------

PEDIDO_SHOPEE = {
    "order_sn": "260715ABCD1234",
    "order_status": "COMPLETED",
    "create_time": 1784131351,
    "update_time": 1784217751,
    "currency": "BRL",
    "estimated_shipping_fee": 0,
    "buyer_user_id": 998877,
    "buyer_username": "comprador_shopee",
    "recipient_address": {"state": "Minas Gerais", "city": "Uberlândia"},
    "item_list": [
        {
            "item_id": 22334455,
            "item_name": "Retentor de Válvula Motor AP",
            "model_sku": "5338STA",
            "model_id": 66778899,
            "model_name": "Padrão",
            "model_quantity_purchased": 3,
            "model_original_price": 42.90,
            "model_discounted_price": 38.90,
        }
    ],
}

ESCROW_SHOPEE = {
    "order_sn": "260715ABCD1234",
    "escrow_release_time": 1785000000,
    "order_income": {
        "escrow_amount": 88.42,
        "original_price": 128.70,
        "seller_discount": 12.00,
        "commission_fee": 16.34,
        "service_fee": 7.00,
        "transaction_fee": 3.94,
        "buyer_paid_shipping_fee": 0,
        "actual_shipping_fee": 0,
        "reverse_shipping_fee": 0,
        "currency": "BRL",
    },
}


class TestNormalizadorShopee:
    def test_usa_o_preco_com_desconto_ja_aplicado(self):
        p = shopee.normalizar_pedido(PEDIDO_SHOPEE)
        assert p.gross_amount == Decimal("116.70")  # 38,90 × 3

    def test_converte_epoch_para_data_com_fuso(self):
        p = shopee.normalizar_pedido(PEDIDO_SHOPEE)
        assert p.date_created is not None
        assert p.date_created.tzinfo is not None

    def test_traduz_o_status_de_conclusao(self):
        p = shopee.normalizar_pedido(PEDIDO_SHOPEE)
        assert p.status == StatusPedido.ENTREGUE

    def test_pedido_sem_item_list_nao_quebra(self):
        """A Shopee devolve o payload sem itens quando ``optional_fields`` não é
        pedido explicitamente. O pedido precisa entrar, ainda que zerado, em vez
        de derrubar a importação inteira."""
        p = shopee.normalizar_pedido({**PEDIDO_SHOPEE, "item_list": None})
        assert p.external_id == "260715ABCD1234"
        assert p.gross_amount == Decimal("0")

    def test_escrow_e_a_fonte_do_liquido(self):
        pagamento = shopee.normalizar_escrow(ESCROW_SHOPEE)
        assert pagamento.net_received_amount == Decimal("88.42")
        assert pagamento.status == StatusPagamento.APROVADO

    def test_escrow_detalha_cada_taxa_da_shopee(self):
        pagamento = shopee.normalizar_escrow(ESCROW_SHOPEE)
        valores = {t.fee_type_raw: t.amount for t in pagamento.fees}
        assert valores["commission_fee"] == Decimal("16.34")
        assert valores["service_fee"] == Decimal("7.00")
        assert valores["transaction_fee"] == Decimal("3.94")

    def test_sem_escrow_o_liquido_e_estimado_e_marcado_como_tal(self):
        """Antes da conclusão do pedido a Shopee não informa líquido nenhum.

        A estimativa entra marcada como ``computed`` para que o painel não a
        apresente como dinheiro confirmado.
        """
        p = shopee.estimar_liquido(shopee.normalizar_pedido(PEDIDO_SHOPEE))
        assert p.net_source == "computed"
        assert p.net_amount is not None
        assert p.net_amount < p.gross_amount

    def test_escrow_vazio_resulta_em_pagamento_pendente(self):
        pagamento = shopee.normalizar_escrow({"order_sn": "X", "order_income": {}}, "X")
        assert pagamento.status == StatusPagamento.PENDENTE
        assert pagamento.net_received_amount == Decimal("0")


class TestAssinaturaShopee:
    def test_a_ordem_da_string_base_e_fixa(self, monkeypatch):
        """A concatenação não é alfabética e não pode ser reordenada — trocar a
        ordem produz assinatura inválida em toda chamada."""
        import hashlib
        import hmac

        from app.connectors.shopee.connector import ConectorShopee
        from app.core.config import settings

        monkeypatch.setattr(settings, "SHOPEE_PARTNER_ID", "2000123")
        monkeypatch.setattr(settings, "SHOPEE_PARTNER_KEY", "chave-secreta")

        caminho, ts, token, loja = "/api/v2/order/get_order_list", 1784131351, "tok", "555"
        esperado = hmac.new(
            b"chave-secreta", f"2000123{caminho}{ts}{token}{loja}".encode(), hashlib.sha256
        ).hexdigest()

        assert ConectorShopee.assinar(caminho, ts, token, loja) == esperado

    def test_endpoint_publico_assina_sem_token_nem_loja(self, monkeypatch):
        import hashlib
        import hmac

        from app.connectors.shopee.connector import ConectorShopee
        from app.core.config import settings

        monkeypatch.setattr(settings, "SHOPEE_PARTNER_ID", "2000123")
        monkeypatch.setattr(settings, "SHOPEE_PARTNER_KEY", "chave-secreta")

        caminho, ts = "/api/v2/shop/auth_partner", 1784131351
        esperado = hmac.new(
            b"chave-secreta", f"2000123{caminho}{ts}".encode(), hashlib.sha256
        ).hexdigest()

        assert ConectorShopee.assinar(caminho, ts) == esperado


class TestAssinaturaMercadoPago:
    def test_valida_o_header_x_signature(self, monkeypatch):
        import hashlib
        import hmac
        import json

        from app.connectors.mercadopago.connector import ConectorMercadoPago
        from app.core.config import settings

        monkeypatch.setattr(settings, "MP_WEBHOOK_SECRET", "segredo-do-webhook")

        corpo = json.dumps({"data": {"id": "55555555555"}, "type": "payment"}).encode()
        ts, request_id = "1784131351", "req-abc-123"
        manifesto = f"id:55555555555;request-id:{request_id};ts:{ts};"
        v1 = hmac.new(b"segredo-do-webhook", manifesto.encode(), hashlib.sha256).hexdigest()

        conector = ConectorMercadoPago()
        assert conector.verify_signature(
            corpo, {"x-signature": f"ts={ts},v1={v1}", "x-request-id": request_id}
        )

    def test_rejeita_assinatura_forjada(self, monkeypatch):
        import json

        from app.connectors.mercadopago.connector import ConectorMercadoPago
        from app.core.config import settings

        monkeypatch.setattr(settings, "MP_WEBHOOK_SECRET", "segredo-do-webhook")
        corpo = json.dumps({"data": {"id": "1"}, "type": "payment"}).encode()

        assert not ConectorMercadoPago().verify_signature(
            corpo, {"x-signature": "ts=1,v1=deadbeef", "x-request-id": "r"}
        )


@pytest.mark.parametrize(
    "valor,esperado",
    [(None, "0"), ("", "0"), ("12.34", "12.34"), (12.34, "12.34"), ("abc", "0")],
)
def test_conversao_decimal_tolera_entrada_invalida(valor, esperado):
    """Campo ausente ou corrompido não pode derrubar a importação de um lote."""
    assert ml.dec(valor) == Decimal(esperado)
