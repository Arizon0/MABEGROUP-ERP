"""Atendimento e reputação: perguntas, mensagens, reclamações e avaliações."""
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


class Question(Base, TimestampMixin):
    """Pergunta pública num anúncio (pré-venda).

    O tempo de resposta é fator de ranqueamento no Mercado Livre, por isso
    ``response_time_seconds`` é materializado em vez de calculado em consulta.
    """

    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_pergunta_conta_externo"),
        Index("ix_pergunta_tenant_status", "tenant_id", "status", "date_created"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    listing_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    external_listing_id: Mapped[str] = mapped_column(String(64), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    answer_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="unanswered")
    asker_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    date_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_answered: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Message(Base, TimestampMixin):
    """Mensagem pós-venda.

    No Mercado Livre exige ``pack_id`` — não existe acesso a chat pré-venda pela
    API oficial (ver ``docs/10-riscos-limitacoes.md``).
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_msg_conta_externo"),
        Index("ix_msg_tenant_data", "tenant_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    pack_id: Mapped[str] = mapped_column(String(64), default="")
    from_role: Mapped[str] = mapped_column(String(20), default="buyer")  # buyer|seller
    text: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Claim(Base, TimestampMixin):
    """Reclamação, mediação, disputa ou devolução."""

    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_recl_conta_externo"),
        Index("ix_recl_tenant_status", "tenant_id", "status", "opened_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(30), default="claim")
    stage: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30), default="opened")
    reason_code: Mapped[str] = mapped_column(String(60), default="")
    reason_text: Mapped[str] = mapped_column(String(400), default="")
    resolution: Mapped[str] = mapped_column(String(200), default="")
    amount_involved: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    events: Mapped[list[ClaimEvent]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class ClaimEvent(Base):
    """Andamento de uma reclamação."""

    __tablename__ = "claim_events"
    __table_args__ = (Index("ix_evento_recl_data", "claim_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(30), default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    claim: Mapped[Claim] = relationship(back_populates="events")


class Review(Base, TimestampMixin):
    """Avaliação de produto ou da venda."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_aval_conta_externo"),
        Index("ix_aval_tenant_data", "tenant_id", "date_created"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    listing_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    date_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
