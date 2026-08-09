"""Base declarativa, mixins e o tipo monetário do sistema."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Escala monetária: 4 casas no armazenamento, 2 na apresentação. As 2 casas
# extras absorvem rateios (frete e comissão divididos entre itens de um pedido)
# sem acumular erro de arredondamento no meio da cadeia de cálculo.
Money = Numeric(18, 4, asdecimal=True)
ZERO = Decimal("0")

# Chave primária de 64 bits. O SQLite só auto-incrementa a coluna quando o tipo
# declarado é exatamente ``INTEGER`` — um ``BIGINT PRIMARY KEY`` ali vira uma
# coluna comum e todo INSERT falha por NOT NULL. A variante mantém BIGINT no
# PostgreSQL (produção) e INTEGER no SQLite (desenvolvimento e testes).
BigPK = BigInteger().with_variant(Integer, "sqlite")


#: Nomear constraints é o que permite ao Alembic gerar migrations reversíveis:
#: sem nome determinístico, o autogenerate não sabe qual índice remover no
#: downgrade e produz migrations que só funcionam de ida.
CONVENCAO_NOMES = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa com convenção de nomes explícita."""

    metadata = MetaData(naming_convention=CONVENCAO_NOMES)

    type_annotation_map = {Decimal: Money}


class TimestampMixin:
    """``created_at`` e ``updated_at`` mantidos pelo banco."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin:
    """Coluna de isolamento presente em toda tabela de negócio.

    A checagem de que nenhuma query de negócio escapa desse filtro é feita pelo
    teste ``test_isolamento_tenant.py``, que roda no CI.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(
            BigPK,
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class ExternalMixin:
    """Espelho de um recurso que vive num marketplace.

    ``raw`` guarda o payload original: é a prova documental em disputa
    financeira e o que permite reprocessar o histórico quando o normalizador
    evolui, sem precisar rebuscar tudo na API externa.
    """

    external_id: Mapped[str] = mapped_column(nullable=False, index=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def agora() -> datetime:
    """Instante atual em UTC, com fuso explícito."""
    return datetime.now(UTC)


def indice_tenant_data(tabela: str, coluna_data: str = "created_at") -> Index:
    """Índice padrão das consultas de painel: sempre filtra tenant e ordena por data."""
    return Index(f"ix_{tabela}_tenant_data", "tenant_id", f"{coluna_data} DESC")
