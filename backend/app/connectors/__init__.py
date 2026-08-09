"""Registro de conectores de marketplace.

Ponto único onde o sistema decide qual implementação atende cada canal. Todo o
resto do código pede o conector por nome de canal e recebe algo que cumpre o
protocolo — sem saber se é a integração real ou a simulada.

É também onde ``USE_MOCK_CONNECTORS`` é honrado: alternar entre dados simulados e
APIs reais é uma variável de ambiente, não uma mudança de código.
"""
from __future__ import annotations

from typing import Any

from app.connectors.base import Connector
from app.core.config import settings
from app.core.errors import ErroDominio
from app.models.enums import Canal

#: Canais que expõem catálogo e pedidos (o Mercado Pago é só financeiro).
CANAIS_COMERCIAIS = (Canal.MERCADO_LIVRE, Canal.SHOPEE)


def obter_conector(canal: str, *, forcar_real: bool = False) -> Any:
    """Devolve o conector do canal.

    ``forcar_real`` existe para o caso de um tenant específico já ter credenciais
    válidas enquanto o ambiente ainda roda em modo simulado.
    """
    if settings.USE_MOCK_CONNECTORS and not forcar_real:
        from app.connectors.mock import ConectorMock

        return ConectorMock(canal)

    if canal == Canal.MERCADO_LIVRE:
        from app.connectors.mercadolivre.connector import ConectorMercadoLivre

        return ConectorMercadoLivre()
    if canal == Canal.MERCADO_PAGO:
        from app.connectors.mercadopago.connector import ConectorMercadoPago

        return ConectorMercadoPago()
    if canal == Canal.SHOPEE:
        from app.connectors.shopee.connector import ConectorShopee

        return ConectorShopee()

    raise ErroDominio(f"Canal não suportado: {canal!r}")


def canais_disponiveis() -> list[str]:
    return [Canal.MERCADO_LIVRE, Canal.MERCADO_PAGO, Canal.SHOPEE]


def suporta(conector: Any, metodo: str) -> bool:
    """Informa se o conector implementa uma capacidade.

    A interface consulta isto antes de oferecer uma funcionalidade — é o que
    permite esconder o módulo de Livestream quando a API não está liberada para a
    região, em vez de exibir um erro recorrente ao usuário.
    """
    return callable(getattr(conector, metodo, None))


__all__ = ["Connector", "obter_conector", "canais_disponiveis", "suporta", "CANAIS_COMERCIAIS"]
