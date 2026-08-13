"""Paginação de pedidos do Mercado Livre acima do teto de offset.

A busca do ML não pagina além do offset 1.000 e **não sinaliza** quando há
mais: devolve as primeiras mil e o total real no `paging`. Um vendedor de
volume alto perde metade do faturamento sem nenhum erro aparecer — o modo mais
perigoso de falhar, porque o número menor parece legítimo.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.mercadolivre.connector import TETO_DE_OFFSET, ConectorMercadoLivre


class ApiFalsa:
    """Simula a busca do ML, com volume distribuído por dia."""

    def __init__(self, pedidos_por_dia: int) -> None:
        self.pedidos_por_dia = pedidos_por_dia
        self.chamadas = 0

    async def get(self, _caminho: str, *, token: str = "", params: dict) -> dict:
        self.chamadas += 1
        inicio = datetime.fromisoformat(params["order.date_created.from"].replace("Z", "+00:00"))
        fim = datetime.fromisoformat(params["order.date_created.to"].replace("Z", "+00:00"))

        segundos = (fim - inicio).total_seconds()
        total = max(0, int(self.pedidos_por_dia * segundos / 86400))

        offset = int(params["offset"])
        limite = int(params["limit"])
        # O ML nunca serve além do teto, mas informa o total verdadeiro.
        disponivel = min(total, TETO_DE_OFFSET)
        fatia = range(offset, min(offset + limite, disponivel))

        return {
            "paging": {"total": total},
            "results": [
                {
                    "id": f"{inicio.date()}-{i}",
                    "date_created": inicio.isoformat(),
                    "status": "paid",
                    "order_items": [],
                    "payments": [],
                }
                for i in fatia
            ],
        }


@pytest.fixture
def conector(monkeypatch):
    c = ConectorMercadoLivre()
    return c


def _preparar(conector, monkeypatch, pedidos_por_dia: int) -> ApiFalsa:
    api = ApiFalsa(pedidos_por_dia)
    monkeypatch.setattr(conector, "_api", lambda **_: api)
    monkeypatch.setattr(
        "app.connectors.mercadolivre.connector.norm.normalizar_pedido",
        lambda bruto: bruto["id"],
    )
    return api


async def test_volume_baixo_nao_subdivide(conector, monkeypatch):
    """Sem estourar o teto, o comportamento antigo se mantém."""
    api = _preparar(conector, monkeypatch, pedidos_por_dia=10)
    fim = datetime.now(UTC)
    pedidos = await conector.fetch_orders(
        "tok", since=fim - timedelta(days=30), until=fim, seller_id="1"
    )
    assert len(pedidos) == 300
    assert len(set(pedidos)) == len(pedidos), "não pode duplicar"


async def test_janela_acima_do_teto_e_subdividida(conector, monkeypatch):
    """O caso que fazia sumir metade das vendas.

    Com 60 pedidos por dia, uma janela de 30 dias tem 1.800 — acima do teto de
    1.000. Sem subdivisão, os 800 excedentes desapareciam sem erro.
    """
    api = _preparar(conector, monkeypatch, pedidos_por_dia=60)
    fim = datetime.now(UTC)
    pedidos = await conector.fetch_orders(
        "tok", since=fim - timedelta(days=30), until=fim, seller_id="1"
    )

    assert len(pedidos) == 1800, f"esperado 1800, veio {len(pedidos)}"
    assert len(set(pedidos)) == len(pedidos), "a subdivisão não pode duplicar pedidos"


async def test_volume_muito_alto_continua_completo(conector, monkeypatch):
    """Divide quantas vezes for preciso, não só uma."""
    api = _preparar(conector, monkeypatch, pedidos_por_dia=200)
    fim = datetime.now(UTC)
    pedidos = await conector.fetch_orders(
        "tok", since=fim - timedelta(days=60), until=fim, seller_id="1"
    )

    assert len(pedidos) == 12000
    assert len(set(pedidos)) == len(pedidos)


async def test_dia_acima_do_teto_avisa_em_vez_de_sumir(conector, monkeypatch, capsys):
    """Um único dia acima de mil é o limite da API — mas não pode ser silencioso.

    Usa ``capsys`` e não ``caplog``: o structlog escreve direto em stdout, então
    o ``caplog`` do pytest, que observa o módulo ``logging``, não veria nada e o
    teste passaria sem provar coisa alguma.
    """
    _preparar(conector, monkeypatch, pedidos_por_dia=1500)
    fim = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    pedidos = await conector.fetch_orders(
        "tok", since=fim - timedelta(days=2), until=fim, seller_id="1"
    )

    assert len(pedidos) == 2 * TETO_DE_OFFSET
    saida = capsys.readouterr().out
    assert "pedidos_acima_do_teto_de_paginacao" in saida
    assert "faltando=500" in saida
