"""Campanhas, promoções e a ponte com os itens participantes."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigPK, Base, TimestampMixin


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

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigPK, nullable=False)
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

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    campaign_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[int | None] = mapped_column(
        BigPK, ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    external_listing_id: Mapped[str] = mapped_column(String(64), default="")
    original_price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    promo_price: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    stock_limit: Mapped[int] = mapped_column(Integer, default=0)

    campaign: Mapped[Campaign] = relationship(back_populates="items")


class EscopoAds:
    """Escopos de lançamento de investimento em Ads, do mais específico ao mais
    genérico. A ordem é a precedência do rateio: um pedido coberto por um
    lançamento de anúncio não recebe verba do SKU nem do canal de novo."""

    ANUNCIO = "listing"
    SKU = "sku"
    CANAL = "channel"

    TODOS = (ANUNCIO, SKU, CANAL)


class AdSpend(Base, TimestampMixin):
    """Investimento em publicidade de uma competência, em um escopo.

    Existe porque nenhuma API entrega o custo de Ads **por pedido** — o
    Mercado Livre consolida por campanha/anúncio e a Ads API da Shopee exige
    whitelist separada. O valor lançado aqui é rateado entre os pedidos da
    competência proporcionalmente à receita (ver ``services/margens.py``), que
    é o mesmo método da planilha de qualquer analista — só que auditável.

    ``attributed_revenue`` é a receita que o canal atribuiu à publicidade, do
    relatório de Ads. Sem ela não existe ACOS (a tela mostra "—"); TACOS não
    precisa dela porque usa a receita total do pedido.
    """

    __tablename__ = "ad_spends"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel", "year", "month", "scope", "reference",
            name="uq_ads_tenant_competencia_escopo",
        ),
        Index("ix_ads_tenant_competencia", "tenant_id", "year", "month"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(10), default=EscopoAds.CANAL, nullable=False)
    #: id externo do anúncio quando ``scope="listing"``; ``sku_base`` quando
    #: ``scope="sku"``; ``""`` quando o lançamento é do canal inteiro. Vazio e
    #: não NULL: em Postgres dois NULL não colidem no UNIQUE, o que permitiria
    #: duplicar o lançamento do canal e dobrar o rateio.
    reference: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    attributed_revenue: Mapped[Decimal | None] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(String(300), default="")
