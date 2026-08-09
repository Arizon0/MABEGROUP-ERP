"""Catálogo: produtos internos, anúncios, variações, de-para de SKU e estoque."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """Produto interno do vendedor — o SKU base que unifica os canais.

    É a entidade que permite responder "quanto o produto 5338 faturou no total?"
    quando ele está em quatro anúncios do ML e dois da Shopee, com códigos
    diferentes em cada um. Nenhum painel nativo consegue isso, porque nenhum
    enxerga os outros canais.
    """

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_produto_tenant_sku"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(300), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(160), default="")
    #: Custo atual. O custo usado numa venda é congelado em ``order_items``.
    unit_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    ncm: Mapped[str] = mapped_column(String(20), default="")
    ean: Mapped[str] = mapped_column(String(20), default="")
    weight_grams: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(String(1000), default="")


class Listing(Base, TimestampMixin):
    """Anúncio publicado num canal."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_anuncio_conta_externo"),
        Index("ix_anuncio_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channel_accounts.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(30), default="active")
    listing_type: Mapped[str] = mapped_column(String(40), default="")
    category_id: Mapped[str] = mapped_column(String(40), default="")
    sku_channel: Mapped[str] = mapped_column(String(80), default="")
    price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0)
    sold_quantity: Mapped[int] = mapped_column(Integer, default=0)
    permalink: Mapped[str] = mapped_column(String(500), default="")
    thumbnail: Mapped[str] = mapped_column(String(500), default="")
    logistic_type: Mapped[str] = mapped_column(String(40), default="")
    health: Mapped[Decimal | None] = mapped_column(nullable=True)
    visits_30d: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    variations: Mapped[list[ListingVariation]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", lazy="selectin"
    )


class ListingVariation(Base, TimestampMixin):
    """Variação de um anúncio (``variation`` no ML, ``model`` na Shopee)."""

    __tablename__ = "listing_variations"
    __table_args__ = (
        UniqueConstraint("listing_id", "external_variation_id", name="uq_variacao_anuncio"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    external_variation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sku_channel: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0)

    listing: Mapped[Listing] = relationship(back_populates="variations")


class SkuLink(Base, TimestampMixin):
    """De-para ``sku_channel`` → produto interno.

    Necessário porque o mesmo produto costuma ter códigos diferentes em cada
    canal (``8126``, ``8126STD``, ``8126a`` no ML e ``8126STA`` na Shopee são o
    mesmo item). Sem esse mapeamento manual, a consolidação por SKU é impossível.
    """

    __tablename__ = "sku_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "sku_channel", name="uq_depara_tenant_canal_sku"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    sku_channel: Mapped[str] = mapped_column(String(80), nullable=False)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[str] = mapped_column(String(20), default="manual")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SkuPendency(Base, TimestampMixin):
    """SKU visto na ingestão sem mapeamento.

    Regra explícita: pendência **nunca** bloqueia a importação do pedido. O
    dinheiro entra no sistema mesmo sem o de-para; o que fica indisponível é só
    a margem daquele item, e isso é sinalizado em vez de silenciado.
    """

    __tablename__ = "sku_pendencies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "sku_channel", name="uq_pendencia_tenant_canal_sku"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    sku_channel: Mapped[str] = mapped_column(String(80), nullable=False)
    sample_title: Mapped[str] = mapped_column(String(300), default="")
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class InventorySnapshot(Base):
    """Série temporal de estoque.

    Sem esta série é impossível responder "quantos dias esse SKU ficou em ruptura
    no mês passado?" — a API só informa o estoque de agora.
    """

    __tablename__ = "inventory_snapshots"
    __table_args__ = (Index("ix_estoque_anuncio_data", "listing_id", "captured_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    variation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
