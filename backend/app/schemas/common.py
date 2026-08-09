"""Schemas Pydantic compartilhados entre os endpoints."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer

T = TypeVar("T")


class Base(BaseModel):
    """Base dos schemas de resposta.

    ``Decimal`` é serializado como **string**, não como número: JSON não tem
    decimal exato, e converter para float na saída reintroduziria justamente o
    erro de arredondamento que o resto do sistema evita.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("*", when_used="json")
    def _serializar(self, valor: Any) -> Any:
        return str(valor) if isinstance(valor, Decimal) else valor


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
