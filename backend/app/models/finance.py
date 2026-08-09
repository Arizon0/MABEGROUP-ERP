"""Pagamentos, taxas, reembolsos, repasses e conciliação."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
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
from app.models.enums import StatusConciliacao, StatusPagamento


class Payment(Base, TimestampMixin):
    """Pagamento de um pedido.

    No Mercado Livre vem do Mercado Pago, que informa o líquido pronto em
    ``net_received_amount``. Na Shopee vem do escrow, e só depois da conclusão
    do pedido — ver ``docs/06-financeiro-conciliacao.md``.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_pgto_conta_externo"),
        Index("ix_pgto_tenant_data", "tenant_id", "date_approved"),
        Index("ix_pgto_pedido", "order_id"),
        Index("ix_pgto_liberacao", "tenant_id", "money_release_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # mercadopago|shopee_escrow

    status: Mapped[str] = mapped_column(
        String(30), default=StatusPagamento.PENDENTE, nullable=False
    )
    status_raw: Mapped[str] = mapped_column(String(60), default="")
    status_detail: Mapped[str] = mapped_column(String(80), default="")
    payment_method: Mapped[str] = mapped_column(String(40), default="")
    installments: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")

    transaction_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    total_paid_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    shipping_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    taxes_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    #: Líquido informado pelo provedor. Quando existe, é fonte primária — não
    #: recalculamos, para não divergir do extrato que o vendedor enxerga.
    net_received_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    date_approved: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Data em que o dinheiro fica disponível. É o que permite projetar caixa.
    money_release_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    money_release_status: Mapped[str] = mapped_column(String(30), default="")
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    fees: Mapped[list[PaymentFee]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )
    refunds: Mapped[list[Refund]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentFee(Base):
    """Taxa individual de um pagamento.

    Guardar taxa por tipo (em vez de um total) é o que torna a conciliação
    auditável: permite responder "a taxa de parcelamento subiu?" em vez de só
    constatar que o líquido caiu.
    """

    __tablename__ = "payment_fees"
    __table_args__ = (Index("ix_taxa_pgto", "payment_id"), Index("ix_taxa_tipo", "tenant_id", "fee_type"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    fee_type: Mapped[str] = mapped_column(String(40), nullable=False)
    fee_type_raw: Mapped[str] = mapped_column(String(60), default="")
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    payer: Mapped[str] = mapped_column(String(20), default="collector")

    payment: Mapped[Payment] = relationship(back_populates="fees")


class Refund(Base, TimestampMixin):
    """Reembolso ou chargeback."""

    __tablename__ = "refunds"
    __table_args__ = (Index("ix_reemb_tenant_data", "tenant_id", "date_created"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(64), default="")
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="")
    #: Chargeback pode chegar meses depois e reabriria um período já fechado; por
    #: isso lança na data do evento, nunca retroage.
    is_chargeback: Mapped[bool] = mapped_column(Boolean, default=False)
    date_created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="refunds")


class Settlement(Base, TimestampMixin):
    """Repasse: o crédito que efetivamente caiu na conta do vendedor."""

    __tablename__ = "settlements"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "external_id", name="uq_repasse_conta_externo"),
        Index("ix_repasse_tenant_data", "tenant_id", "settlement_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    status: Mapped[str] = mapped_column(String(30), default="")
    bank_reference: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(40), default="")  # mp_release_report|shopee_payout
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    entries: Mapped[list[SettlementEntry]] = relationship(
        back_populates="settlement", cascade="all, delete-orphan"
    )


class SettlementEntry(Base):
    """Linha do repasse ligada ao pagamento/pedido de origem.

    É o que responde "esse crédito de R$ 4.312,88 corresponde a quais pedidos?".
    """

    __tablename__ = "settlement_entries"
    __table_args__ = (
        Index("ix_linha_repasse", "settlement_id"),
        Index("ix_linha_pgto", "payment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    settlement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False
    )
    payment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(40), default="sale")
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="")

    settlement: Mapped[Settlement] = relationship(back_populates="entries")


class Reconciliation(Base, TimestampMixin):
    """Resultado do casamento venda ↔ pagamento ↔ repasse.

    ``notes`` recebe o diagnóstico automático da causa provável. Apontar a
    diferença sem explicá-la transfere o trabalho de volta para o usuário.
    """

    __tablename__ = "reconciliations"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_conciliacao_pedido"),
        Index("ix_concil_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    expected_net: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    settled_net: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    divergence: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    divergence_pct: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=StatusConciliacao.AGUARDANDO_REPASSE, nullable=False
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
