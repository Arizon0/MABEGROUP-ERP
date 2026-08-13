"""Engine assíncrona, fábrica de sessões e dependência do FastAPI."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _kwargs_engine() -> dict:
    if settings.is_sqlite:
        # SQLite (dev/teste) não tem pool configurável nos mesmos termos.
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        # Postgres gerenciado derruba conexões ociosas; sem pre_ping a primeira
        # query após um período parado falha com "server closed the connection".
        "pool_pre_ping": True,
        # Cinco minutos, não trinta. Uma sincronização longa alterna trabalho no
        # banco com esperas de HTTP que chegam a minutos — a espera imposta pelo
        # `Retry-After` do canal, sobretudo. Nesse intervalo a conexão ociosa
        # morre, e a próxima operação a encontra morta. O `pre_ping` deveria
        # cobrir isso, mas no driver assíncrono a falha no teste de vida estoura
        # como MissingGreenlet e derruba a requisição inteira, em vez de trocar a
        # conexão em silêncio. Reciclar antes evita chegar nesse caminho.
        "pool_recycle": settings.DB_POOL_RECYCLE,
    }


engine = create_async_engine(settings.DATABASE_URL, echo=settings.DB_ECHO, **_kwargs_engine())

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objetos permanecem utilizáveis após o commit
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependência do FastAPI: uma sessão por requisição, com rollback em erro."""
    async with SessionLocal() as sessao:
        try:
            yield sessao
        except Exception:
            await sessao.rollback()
            raise
        finally:
            await sessao.close()


async def criar_schema() -> None:
    """Cria as tabelas a partir dos models (desenvolvimento e testes).

    Em produção o schema é gerido por Alembic — ver ``docs/09-deploy.md``: as
    migrations rodam como job separado, antes do deploy, nunca no startup de N
    réplicas simultâneas.
    """
    from app.db.base import Base
    import app.models  # noqa: F401  — registra todos os models no metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
