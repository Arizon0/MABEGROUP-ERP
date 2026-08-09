"""Pedidos, itens, timeline e envios — o núcleo comercial."""
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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import FonteLiquido, StatusPedido


class Order(Base, TimestampMixin):
    """Pedido canônico, idêntico em estrutura para todos os marketplaces."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_pedido_conta_externo"),
        Index("ix_orders_tenant_data", "tenant_id", "date_created"),
        Index("ix_orders_tenant_status", "tenant_id", "status", "date_created"),
        Index("ix_orders_conta_data", "channel_account_id", "date_created"),
        Index("ix_orders_geo", "tenant_id", "ship_state", "date_created"),
        Index("ix_orders_pack", "external_pack_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channel_accounts.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Carrinho/pacote. Agrupar por aqui evita contar a receita do pacote uma vez
    #: por linha-componente — o erro clássico de dupla contagem no Mercado Livre.
    external_pack_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(30), default=StatusPedido.PENDENTE, nullable=False)
    status_raw: Mapped[str] = mapped_column(String(60), default="")
    status_detail: Mapped[str] = mapped_column(String(120), default="")

    date_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_closed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    currency: Mapped[str] = mapped_column(String(3), default="BRL", nullable=False)

    # --- Composição financeira (ver docs/06) --------------------------------
    gross_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    shipping_revenue: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    payment_fee: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    #: Procedência do líquido. Um painel que mistura estimativa com valor
    #: liquidado sem distinguir diverge do extrato do vendedor.
    net_source: Mapped[str] = mapped_column(
        String(20), default=FonteLiquido.CALCULADO, nullable=False
    )
    cogs: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    # --- Comprador (pseudonimizado — ver docs/08) ---------------------------
    buyer_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    buyer_nickname: Mapped[str] = mapped_column(String(120), default="")

    ship_state: Mapped[str] = mapped_column(String(40), default="")
    ship_city: Mapped[str] = mapped_column(String(120), default="")
    logistic_type: Mapped[str] = mapped_column(String(40), default="")

    has_multiple_items: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list[OrderEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    shipments: Mapped[list[Shipment]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    @property
    def is_faturavel(self) -> bool:
        """Pedidos cancelados são preservados, mas não somam receita."""
        return self.status != StatusPedido.CANCELADO


class OrderItem(Base, TimestampMixin):
    """Linha do pedido, com custo congelado no momento da ingestão."""

    __tablename__ = "order_items"
    __table_args__ = (
        Index("ix_item_pedido", "order_id"),
        Index("ix_item_sku", "tenant_id", "sku_base"),
        Index("ix_item_produto", "product_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    external_item_id: Mapped[str] = mapped_column(String(64), default="")
    listing_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    variation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listing_variations.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    sku_channel: Mapped[str] = mapped_column(String(80), default="")
    sku_base: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    variation_name: Mapped[str] = mapped_column(String(200), default="")

    quantity: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    #: Custo congelado na ingestão. Alterar o custo do produto hoje não pode
    #: reescrever a margem de um pedido de seis meses atrás — senão nenhum
    #: fechamento histórico é reproduzível.
    unit_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    cogs: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


class OrderEvent(Base):
    """Timeline do pedido: o que o marketplace fez, na ordem em que aconteceu."""

    __tablename__ = "order_events"
    __table_args__ = (Index("ix_evento_pedido_data", "order_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    from_status: Mapped[str] = mapped_column(String(30), default="")
    to_status: Mapped[str] = mapped_column(String(30), default="")
    source: Mapped[str] = mapped_column(String(20), default="sync")  # webhook|sync|manual
    description: Mapped[str] = mapped_column(String(400), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped[Order] = relationship(back_populates="events")


class Shipment(Base, TimestampMixin):
    """Envio associado a um pedido."""

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_envio_conta_externo"),
        Index("ix_envio_tenant_status", "tenant_id", "status"),
        Index("ix_envio_atraso", "tenant_id", "estimated_delivery"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=True
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    status_raw: Mapped[str] = mapped_column(String(60), default="")
    substatus: Mapped[str] = mapped_column(String(60), default="")
    tracking_number: Mapped[str] = mapped_column(String(80), default="")
    carrier: Mapped[str] = mapped_column(String(80), default="")
    logistic_type: Mapped[str] = mapped_column(String(40), default="")

    date_shipped: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_delivered: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_delivery: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Positivo = atraso em dias. Calculado na ingestão para permitir ordenar e
    #: filtrar por atraso sem expressão em toda query do painel de logística.
    delay_days: Mapped[int] = mapped_column(Integer, default=0)

    cost_seller: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    cost_buyer: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    receiver_state: Mapped[str] = mapped_column(String(40), default="")
    receiver_city: Mapped[str] = mapped_column(String(120), default="")
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    order: Mapped[Order | None] = relationship(back_populates="shipments")
    events: Mapped[list[ShipmentEvent]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class ShipmentEvent(Base):
    """Evento de rastreio."""

    __tablename__ = "shipment_events"
    __table_args__ = (Index("ix_evento_envio_data", "shipment_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="")
    substatus: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    shipment: Mapped[Shipment] = relationship(back_populates="events")
