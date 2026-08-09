"""Trilha de auditoria: o que um usuário humano fez no sistema."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import AuditLog


class Acao:
    LOGIN = "auth.login"
    LOGOUT = "auth.logout"
    CONTA_CONECTADA = "account.connected"
    CONTA_REVOGADA = "account.revoked"
    TOKEN_RENOVADO = "account.token_refreshed"
    SYNC_MANUAL = "sync.manual"
    WEBHOOK_REPROCESSADO = "webhook.reprocessed"
    PRODUTO_CRIADO = "product.created"
    PRODUTO_ATUALIZADO = "product.updated"
    SKU_MAPEADO = "sku.mapped"
    USUARIO_CRIADO = "user.created"
    USUARIO_ALTERADO = "user.updated"
    EXPORTACAO = "report.exported"
    CONFIG_ALTERADA = "settings.updated"


async def registrar(
    db: AsyncSession,
    *,
    tenant_id: int,
    action: str,
    user_id: int | None = None,
    entity_type: str = "",
    entity_id: str | int = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str = "",
    user_agent: str = "",
) -> None:
    """Grava um evento de auditoria.

    Não faz commit: a auditoria participa da transação da operação auditada, de
    forma que uma operação revertida não deixa registro de algo que não ocorreu.
    """
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id)[:64],
            before_json=_limpar(before),
            after_json=_limpar(after),
            ip=ip[:45],
            user_agent=user_agent[:400],
            created_at=datetime.now(UTC),
        )
    )


async def listar(
    db: AsyncSession,
    tenant_id: int,
    *,
    limite: int = 100,
    offset: int = 0,
    action: str | None = None,
) -> list[AuditLog]:
    consulta = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if action:
        consulta = consulta.where(AuditLog.action == action)
    resultado = await db.execute(
        consulta.order_by(AuditLog.created_at.desc()).limit(limite).offset(offset)
    )
    return list(resultado.scalars())


_CHAVES_PROIBIDAS = {
    "password", "senha", "password_hash", "access_token", "refresh_token",
    "client_secret", "partner_key", "token", "secret",
}


def _limpar(dados: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remove segredos antes de gravar.

    A auditoria é a tabela mais lida em investigação de incidente — é o último
    lugar onde um token deveria acabar armazenado em texto claro.
    """
    if dados is None:
        return None
    return {
        k: ("***" if k.lower() in _CHAVES_PROIBIDAS else v) for k, v in dados.items()
    }
