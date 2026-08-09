"""Integração: contas conectadas, cofre de tokens, cursores e webhooks."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigPK, Base, TimestampMixin
from app.models.enums import StatusConta, StatusWebhook


class ChannelAccount(Base, TimestampMixin):
    """Uma conta de marketplace autorizada por um tenant.

    Um tenant pode ter N contas do mesmo canal — o caso real de quem opera
    várias lojas na mesma plataforma.
    """

    __tablename__ = "channel_accounts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel", "external_account_id", name="uq_conta_canal_externo"
        ),
        Index("ix_contas_tenant_canal", "tenant_id", "channel"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nickname: Mapped[str] = mapped_column(String(120), default="")
    site_id: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(20), default=StatusConta.CONECTADA, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    credentials: Mapped[list[ChannelCredential]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class ChannelCredential(Base, TimestampMixin):
    """Tokens cifrados. Ver ``core/crypto.py`` e ``docs/08-seguranca.md``.

    Credenciais antigas são mantidas com ``is_current=False`` para auditoria —
    permite responder "qual token estava em uso quando essa sincronização
    falhou?" sem guardar nada em texto claro.
    """

    __tablename__ = "channel_credentials"
    __table_args__ = (Index("ix_cred_conta_atual", "channel_account_id", "is_current"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    channel_account_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("channel_accounts.id", ondelete="CASCADE"), nullable=False
    )
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_version: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[ChannelAccount] = relationship(back_populates="credentials")


class OAuthState(Base, TimestampMixin):
    """Estado efêmero do fluxo OAuth: anti-CSRF (``state``) e PKCE.

    Consumido uma única vez e com TTL curto. O ``code_verifier`` fica cifrado
    porque, junto do ``code`` interceptado, seria suficiente para trocar por um
    token válido.
    """

    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    state: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    code_verifier_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    redirect_after: Mapped[str] = mapped_column(String(500), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncCursor(Base, TimestampMixin):
    """Marca d'água da sincronização incremental por (conta, recurso).

    ``last_synced_at`` recebe o maior ``date_last_updated`` observado, nunca
    ``now()``: gravar o relógio local abriria uma janela cega do tamanho da
    própria duração da sincronização, e os pedidos criados nesse intervalo
    sumiriam sem nenhum erro visível.
    """

    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("channel_account_id", "resource", name="uq_cursor_conta_recurso"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    channel_account_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("channel_accounts.id", ondelete="CASCADE"), nullable=False
    )
    resource: Mapped[str] = mapped_column(String(40), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_external_id: Mapped[str] = mapped_column(String(64), default="")
    cursor_token: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="idle")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    backfill_done: Mapped[bool] = mapped_column(Boolean, default=False)
    progress_pct: Mapped[int] = mapped_column(SmallInteger, default=0)


class WebhookEvent(Base):
    """Notificação crua recebida de um marketplace.

    O endpoint HTTP só grava aqui e devolve 200 — o Mercado Livre exige resposta
    em até 500 ms e suspende aplicações lentas. Todo o trabalho real acontece no
    worker que consome esta tabela.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_fila", "status", "next_attempt_at"),
        Index("ix_webhook_canal_data", "channel", "received_at"),
        Index("ix_webhook_conta", "channel_account_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_account_id: Mapped[int | None] = mapped_column(BigPK, nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(BigPK, nullable=True)
    topic: Mapped[str] = mapped_column(String(60), default="")
    resource: Mapped[str] = mapped_column(String(255), default="")
    external_event_id: Mapped[str] = mapped_column(String(128), default="")
    #: sha256(canal|tópico|recurso|...). É o UNIQUE que torna a reentrega inócua.
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(
        String(20), default=StatusWebhook.PENDENTE, nullable=False
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


class IntegrationLog(Base):
    """Registro de chamadas às APIs externas — diagnóstico e controle de cota."""

    __tablename__ = "integration_logs"
    __table_args__ = (Index("ix_intlog_canal_data", "channel", "created_at"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(BigPK, nullable=True)
    channel_account_id: Mapped[int | None] = mapped_column(BigPK, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(10), default="GET")
    endpoint: Mapped[str] = mapped_column(String(255), default="")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=1)
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
