"""Contrato de chamada entre a sincronização e os conectores.

O serviço de sincronização é genérico: passa ``seller_id`` e ``shop_id`` para
qualquer canal, sem saber qual dos dois aquele marketplace usa. Se uma
assinatura não aceitar os dois, a chamada quebra com ``TypeError`` — e só com
credenciais reais, porque os conectores simulados aceitam qualquer argumento.

Este arquivo verifica a compatibilidade por introspecção, sem rede.
"""
from __future__ import annotations

import inspect

import pytest

from app.connectors.mercadolivre.connector import ConectorMercadoLivre
from app.connectors.mercadopago.connector import ConectorMercadoPago
from app.connectors.shopee.connector import ConectorShopee

CONECTORES = [ConectorMercadoLivre(), ConectorMercadoPago(), ConectorShopee()]

#: Como ``services/sync.py`` e ``services/webhooks.py`` chamam cada método.
CHAMADAS = {
    "fetch_orders": {"since": None, "until": None, "seller_id": "1", "shop_id": "1"},
    "fetch_order": {"shop_id": "1"},
    "fetch_shipment": {"shop_id": "1"},
    "fetch_payment": {},
    "fetch_escrow": {"shop_id": "1"},
    "fetch_listings": {"seller_id": "1", "shop_id": "1"},
    "fetch_questions": {"seller_id": "1", "shop_id": "1"},
    "fetch_claims": {"shop_id": "1"},
    "fetch_campaigns": {"shop_id": "1"},
    "fetch_seller_reputation": {"seller_id": "1"},
}


@pytest.mark.parametrize("conector", CONECTORES, ids=lambda c: c.channel)
@pytest.mark.parametrize("metodo,kwargs", list(CHAMADAS.items()))
def test_assinatura_aceita_os_argumentos_do_sincronizador(conector, metodo, kwargs):
    funcao = getattr(conector, metodo, None)
    if funcao is None:
        pytest.skip(f"{conector.channel} não implementa {metodo}")

    assinatura = inspect.signature(funcao)
    aceita_extras = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in assinatura.parameters.values()
    )
    if aceita_extras:
        return  # **kwargs cobre qualquer argumento

    faltando = [nome for nome in kwargs if nome not in assinatura.parameters]
    assert not faltando, (
        f"{conector.channel}.{metodo} não aceita {faltando}. "
        f"Adicione `**_: Any` à assinatura — o sincronizador é genérico e a "
        f"chamada quebraria em produção, com credenciais reais."
    )


@pytest.mark.parametrize("conector", CONECTORES, ids=lambda c: c.channel)
def test_conector_declara_identidade_e_versao(conector):
    """Versão do conector é gravada junto do payload cru: quando o marketplace
    muda um contrato, é ela que permite reprocessar o histórico corretamente."""
    assert conector.channel
    assert conector.API_VERSION


@pytest.mark.parametrize("conector", CONECTORES, ids=lambda c: c.channel)
def test_conector_sabe_receber_webhook(conector):
    notificacao = conector.parse_webhook({}, {})
    assert notificacao.channel == conector.channel
