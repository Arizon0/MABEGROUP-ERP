"""Rollups de métricas e fotografias diárias de indicadores.

Existem por um motivo concreto de desempenho: um dashboard que agrega milhões de
linhas de ``orders`` a cada F5 não escala. O worker mantém estas tabelas prontas
e o painel lê linhas já somadas.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BigPK, Base, TimestampMixin


class MetricHourly(Base, TimestampMixin):
    """Agregado por hora. Usado nas janelas de 7 a 90 dias."""

    __tablename__ = "metrics_hourly"
    __table_args__ = (
        # O canal faz parte da chave natural: o rollup mantém uma linha por
        # marketplace em cada hora, e sem ele dois canais no mesmo bucket
        # colidiriam.
        UniqueConstraint(
            "tenant_id",
            "channel_account_id",
            "channel",
            "bucket",
            name="uq_metrica_hora_conta_canal_bucket",
        ),
        Index("ix_metrica_hora_tenant", "tenant_id", "bucket"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    #: 0 = consolidado de todas as contas do tenant; > 0 = conta específica.
    channel_account_id: Mapped[int] = mapped_column(BigPK, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="")
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    fees_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    shipping_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    cancelled_count: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)


class MetricDaily(Base, TimestampMixin):
    """Agregado por dia. Usado nas janelas acima de 90 dias e nos comparativos."""

    __tablename__ = "metrics_daily"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel_account_id", "day", name="uq_metrica_dia_conta_dia"
        ),
        Index("ix_metrica_dia_tenant", "tenant_id", "day"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigPK, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="")
    day: Mapped[date] = mapped_column(Date, nullable=False)

    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    fees_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    shipping_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    cogs_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    cancelled_count: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    avg_ticket: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)


class MetricSnapshot(Base):
    """Fotografia diária de indicadores que o marketplace não versiona.

    Reputação, nível de Mercado Líder e taxa de cancelamento mudam no canal sem
    deixar rastro: a API só devolve o estado de agora. Se não fotografarmos
    diariamente, o histórico simplesmente não existe — e o gráfico de evolução
    de reputação seria impossível de construir depois.
    """

    __tablename__ = "metrics_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id", "day", "metric", name="uq_snapshot_conta_dia_metrica"
        ),
        Index("ix_snapshot_tenant_dia", "tenant_id", "day"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    channel_account_id: Mapped[int] = mapped_column(BigPK, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(60), nullable=False)
    value_num: Mapped[Decimal | None] = mapped_column(nullable=True)
    value_text: Mapped[str] = mapped_column(String(120), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertRule(Base, TimestampMixin):
    """Regra de alerta configurável pelo usuário na aba de Configurações."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: stock_out | divergence | sales_drop | unanswered_question | sync_lag
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    notify_email: Mapped[str] = mapped_column(String(255), default="")
    notify_webhook: Mapped[str] = mapped_column(String(500), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Alert(Base):
    """Alerta disparado, com estado de reconhecimento."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerta_tenant_data", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    rule_id: Mapped[int | None] = mapped_column(BigPK, nullable=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
