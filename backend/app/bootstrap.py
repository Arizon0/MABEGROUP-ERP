"""Bootstrap de produção: organização e usuário proprietário, nada além.

O ``seed`` de desenvolvimento cria demonstração inteira — contas simuladas,
catálogo, custos de exemplo. Em produção nada disso pode existir, mas um deploy
novo ainda precisa de **uma** organização e **um** proprietário, senão a tela
de login fica sem ninguém que possa entrar.

Roda como passo do start do contêiner (depois do ``alembic upgrade head``):

    python -m app.bootstrap

É idempotente e **nunca altera a senha de um usuário que já existe** — se o
dono já trocou a dele pela aplicação, reexecutar o boot não a ressuscita.
"""
from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_senha
from app.models.enums import PapelUsuario
from app.models.tenant import Tenant, User

log = structlog.get_logger(__name__)


async def executar() -> dict[str, int]:
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        tenant = await db.scalar(select(Tenant).limit(1))
        criados = {"tenants": 0, "usuarios": 0}

        if tenant is None:
            tenant = Tenant(
                name=settings.TENANT_NAME,
                slug=settings.TENANT_SLUG,
                plan="pro",
                status="active",
            )
            db.add(tenant)
            await db.flush()
            criados["tenants"] = 1

        email = settings.ADMIN_EMAIL.strip().lower()
        usuario = await db.scalar(select(User).where(User.email == email))
        if usuario is None:
            db.add(
                User(
                    tenant_id=tenant.id,
                    email=email,
                    password_hash=hash_senha(settings.ADMIN_PASSWORD),
                    full_name="Proprietário",
                    role=PapelUsuario.PROPRIETARIO,
                )
            )
            criados["usuarios"] = 1
            if settings.ADMIN_PASSWORD == "admin123":
                log.warning(
                    "proprietario_com_senha_padrao",
                    aviso="Defina ADMIN_PASSWORD no ambiente e troque a senha "
                    "no primeiro acesso, em Configurações.",
                )

        await db.commit()
        log.info("bootstrap_concluido", **criados)
        return criados


if __name__ == "__main__":
    asyncio.run(executar())
