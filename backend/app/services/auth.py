"""Autenticação e autorização dos usuários do painel."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflito, NaoAutorizado, Proibido
from app.core.security import (
    criar_access_token,
    gerar_refresh_token,
    hash_refresh_token,
    hash_senha,
    verificar_senha,
)
from app.models.enums import HIERARQUIA_PAPEIS, PapelUsuario
from app.models.tenant import Tenant, User, UserSession

log = structlog.get_logger(__name__)


async def autenticar(
    db: AsyncSession, email: str, senha: str, *, ip: str = "", user_agent: str = ""
) -> dict[str, object]:
    """Valida credenciais e emite o par de tokens."""
    usuario = await db.scalar(select(User).where(User.email == email.lower().strip()))

    # Mensagem idêntica para e-mail inexistente e senha errada: diferenciá-las
    # transforma o login num verificador de cadastro.
    if usuario is None or not verificar_senha(senha, usuario.password_hash):
        log.info("login_recusado", email=email[:64])
        raise NaoAutorizado("E-mail ou senha inválidos.")
    if not usuario.is_active:
        raise Proibido("Usuário desativado. Procure o administrador da conta.")

    tenant = await db.get(Tenant, usuario.tenant_id)
    if tenant is None or tenant.status != "active":
        raise Proibido("Esta organização está suspensa.")

    access = criar_access_token(
        user_id=usuario.id, tenant_id=usuario.tenant_id, role=usuario.role
    )
    refresh, refresh_hash = gerar_refresh_token()

    db.add(
        UserSession(
            user_id=usuario.id,
            token_hash=refresh_hash,
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            ip=ip[:45],
            user_agent=user_agent[:400],
        )
    )
    usuario.last_login_at = datetime.now(UTC)
    await db.commit()

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _usuario_publico(usuario, tenant),
    }


async def renovar(db: AsyncSession, refresh_token: str) -> dict[str, object]:
    """Rotaciona o refresh token e emite novo access token.

    A rotação é obrigatória: um refresh token reutilizável que vaze dá acesso
    permanente à conta. Aqui o antigo é revogado no mesmo instante.
    """
    token_hash = hash_refresh_token(refresh_token)
    sessao = await db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    if sessao is None or sessao.revoked_at is not None:
        raise NaoAutorizado("Sessão inválida. Faça login novamente.")

    expira = sessao.expires_at
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=UTC)
    if expira < datetime.now(UTC):
        raise NaoAutorizado("Sessão expirada. Faça login novamente.")

    usuario = await db.get(User, sessao.user_id)
    if usuario is None or not usuario.is_active:
        raise NaoAutorizado("Usuário indisponível.")

    sessao.revoked_at = datetime.now(UTC)
    novo_refresh, novo_hash = gerar_refresh_token()
    db.add(
        UserSession(
            user_id=usuario.id,
            token_hash=novo_hash,
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            ip=sessao.ip,
            user_agent=sessao.user_agent,
        )
    )
    await db.commit()

    return {
        "access_token": criar_access_token(
            user_id=usuario.id, tenant_id=usuario.tenant_id, role=usuario.role
        ),
        "refresh_token": novo_refresh,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


async def encerrar_sessao(db: AsyncSession, refresh_token: str) -> None:
    sessao = await db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_refresh_token(refresh_token))
    )
    if sessao is not None:
        sessao.revoked_at = datetime.now(UTC)
        await db.commit()


async def criar_usuario(
    db: AsyncSession,
    *,
    tenant_id: int,
    email: str,
    senha: str,
    nome: str = "",
    papel: str = PapelUsuario.LEITOR,
) -> User:
    email = email.lower().strip()
    if await db.scalar(
        select(User).where(User.tenant_id == tenant_id, User.email == email)
    ):
        raise Conflito("Já existe um usuário com este e-mail nesta organização.")
    if len(senha) < 10:
        raise Conflito("A senha deve ter ao menos 10 caracteres.")

    usuario = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_senha(senha),
        full_name=nome,
        role=papel,
    )
    db.add(usuario)
    await db.commit()
    return usuario


def pode(papel_atual: str, papel_exigido: str) -> bool:
    """Verificação hierárquica de permissão (RBAC)."""
    return HIERARQUIA_PAPEIS.get(papel_atual, -1) >= HIERARQUIA_PAPEIS.get(papel_exigido, 99)


def _usuario_publico(usuario: User, tenant: Tenant) -> dict[str, object]:
    return {
        "id": usuario.id,
        "email": usuario.email,
        "full_name": usuario.full_name,
        "role": usuario.role,
        "tenant": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "plan": tenant.plan},
    }
