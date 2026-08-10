"""Ambiente do Alembic.

Lê a URL de ``DATABASE_URL`` (nunca do ``alembic.ini``, que é versionado) e
roda com engine assíncrona, a mesma da aplicação.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base
import app.models  # noqa: F401 — registra todo o metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _incluir_objeto(objeto, nome, tipo, reflexo, comparar_com) -> bool:
    """Ignora tabelas fora do metadata (extensões, tabelas de sistema)."""
    if tipo == "table" and reflexo and comparar_com is None:
        return False
    return True


def rodar_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _migrar(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # compare_type detecta mudança de tipo de coluna; sem ele, alterar um
        # NUMERIC(12,2) para NUMERIC(18,4) passaria despercebido.
        compare_type=True,
        compare_server_default=True,
        include_object=_incluir_objeto,
    )
    with context.begin_transaction():
        context.run_migrations()


async def rodar_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as conexao:
        await conexao.run_sync(_migrar)
    await engine.dispose()


if context.is_offline_mode():
    rodar_offline()
else:
    asyncio.run(rodar_online())
