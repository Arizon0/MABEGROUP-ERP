"""Schemas Pydantic compartilhados entre os endpoints."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer

T = TypeVar("T")


def texto_decimal(valor: Decimal) -> str:
    """Serializa ``Decimal`` em escala canônica.

    ``str(Decimal)`` preserva a escala do objeto, e a escala varia conforme o
    caminho do código: um registro recém-criado na sessão carrega o valor como
    veio do corpo da requisição (``10.00``), enquanto o mesmo registro relido do
    banco volta na escala da coluna ``Numeric(18, 4)`` (``10.0000``). O mesmo
    valor saindo com dois textos diferentes quebra comparação no cliente e faz
    o teste de contrato depender de quando houve ``refresh``.

    Aqui a escala é derivada do próprio número: zeros à direita são descartados
    e o resultado recebe no mínimo duas casas. Precisão real acima de centavos —
    custo unitário de peça, alíquota fracionária — é preservada, porque só os
    zeros irrelevantes são removidos.
    """
    ajustado = valor.normalize()
    if ajustado.as_tuple().exponent > -2:
        ajustado = ajustado.quantize(Decimal("0.01"))
    return format(ajustado, "f")


class Base(BaseModel):
    """Base dos schemas de resposta.

    ``Decimal`` é serializado como **string**, não como número: JSON não tem
    decimal exato, e converter para float na saída reintroduziria justamente o
    erro de arredondamento que o resto do sistema evita.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("*", when_used="json")
    def _serializar(self, valor: Any) -> Any:
        return texto_decimal(valor) if isinstance(valor, Decimal) else valor


class Pagina(Base, Generic[T]):
    itens: list[T]
    total: int
    limite: int = 50
    offset: int = 0

    @property
    def tem_proxima(self) -> bool:
        return self.offset + self.limite < self.total


class FiltroPeriodo(BaseModel):
    """Filtros globais aceitos por todas as consultas do painel."""

    inicio: datetime | None = Field(None, description="Início do período (ISO-8601, UTC).")
    fim: datetime | None = Field(None, description="Fim do período (ISO-8601, UTC).")
    channel: str | None = Field(None, description="mercadolivre | mercadopago | shopee")
    account_id: int | None = Field(None, description="Conta específica.")
    status: str | None = None
    logistic_type: str | None = None
    state: str | None = Field(None, description="UF de destino.")
    sku: str | None = None


class Mensagem(Base):
    mensagem: str
    detalhes: dict[str, Any] = Field(default_factory=dict)


class RespostaOperacao(Base):
    sucesso: bool = True
    mensagem: str = ""
    dados: dict[str, Any] = Field(default_factory=dict)
