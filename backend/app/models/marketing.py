"""Campanhas, promoções e a ponte com os itens participantes."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Campaign(Base, TimestampMixin):
    """Campanha, cupom, promoção ou combo de um canal.

    ``manual_media_cost`` existe porque a Ads API da Shopee exige whitelist
    separada e o Mercado Livre não expõe custo de mídia por campanha de forma
    consolidada. Sem um campo para lançamento manual, a rentabilidade da campanha
    ficaria estruturalmente incompleta.
    """

    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_camp_conta_externo"),
        Index("ix_camp_tenant_periodo", "tenant_id", "start_at", "end_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    type: Mapped[str] = mapped_column(String(30), default="discount")
    status: Mapped[str] = mapped_column(String(30), default="active")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    budget: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    manual_media_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    items: Mapped[list[CampaignItem]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", lazy="selectin"
    )


class CampaignItem(Base):
    """Anúncio participante de uma campanha, com o preço promocional aplicado."""

    __tablename__ = "campaign_items"
    __table_args__ = (
        Index("ix_camp_item", "campaign_id"),
        Index("ix_camp_item_anuncio", "listing_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    external_listing_id: Mapped[str] = mapped_column(String(64), default="")
    original_price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    promo_price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    stock_limit: Mapped[int] = mapped_column(Integer, default=0)

    campaign: Mapped[Campaign] = relationship(back_populates="items")
