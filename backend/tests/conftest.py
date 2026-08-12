"""Fixtures compartilhadas.

Cada teste roda contra um banco SQLite próprio em memória e com os conectores
simulados — nenhuma chamada sai para a internet, o que torna a suíte
determinística e executável sem credencial nenhuma.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("USE_MOCK_CONNECTORS", "1")
# Banco em arquivo (não ":memory:"): o endpoint de webhook abre a própria
# sessão para responder rápido, e ":memory:" daria a ele um banco vazio
# separado do usado pelo restante do teste.
_ARQUIVO_DB = Path(tempfile.gettempdir()) / "marketplace_hub_teste.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_ARQUIVO_DB}")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("SEED_ON_STARTUP", "0")
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao-000000")
# Volume simulado enxuto: provar a regra não exige mil pedidos, exige os casos
# certos. Com o padrão de produção cada sincronização gerava ~990 pedidos e a
# suíte levava mais de oito minutos — tempo pago em toda iteração, sem comprar
# nenhuma cobertura.
os.environ.setdefault("MOCK_ORDERS_PER_DAY", "2")
os.environ.setdefault("BACKFILL_DAYS", "30")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.core.security import hash_senha  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.channel import ChannelAccount  # noqa: E402
from app.models.enums import Canal, PapelUsuario, StatusConta  # noqa: E402
from app.models.tenant import Tenant, User  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def engine():
    """Recria o schema a cada teste, no mesmo banco que a aplicação usa.

    Compartilhar a engine da aplicação é o que permite testar o endpoint de
    webhook, que abre sessão própria para cumprir o SLA de resposta.
    """
    from app.db.session import engine as motor

    import app.models  # noqa: F401 — registra o metadata completo

    async with motor.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield motor
    async with motor.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    fabrica = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with fabrica() as sessao:
        yield sessao


@pytest_asyncio.fixture
async def tenant(db: AsyncSession) -> Tenant:
    registro = Tenant(name="Autopeças Teste", slug="teste", plan="pro", status="active")
    db.add(registro)
    await db.commit()
    return registro


@pytest_asyncio.fixture
async def usuario(db: AsyncSession, tenant: Tenant) -> User:
    registro = User(
        tenant_id=tenant.id,
        email="teste@exemplo.com.br",
        password_hash=hash_senha("senha-de-teste-123"),
        full_name="Usuário de Teste",
        role=PapelUsuario.PROPRIETARIO,
    )
    db.add(registro)
    await db.commit()
    return registro


@pytest_asyncio.fixture
async def conta(db: AsyncSession, tenant: Tenant) -> ChannelAccount:
    """Conta do Mercado Livre já conectada, com credencial simulada."""
    from app.connectors.mock import ConectorMock
    from app.services import tokens

    registro = ChannelAccount(
        tenant_id=tenant.id,
        channel=Canal.MERCADO_LIVRE,
        external_account_id="123456789",
        nickname="LOJA-TESTE",
        site_id="MLB",
        status=StatusConta.CONECTADA,
        connected_at=datetime.now(UTC),
    )
    db.add(registro)
    await db.commit()

    pacote = await ConectorMock(Canal.MERCADO_LIVRE).exchange_code("CODE-TESTE")
    await tokens.salvar_tokens(db, registro.id, pacote)
    await db.commit()
    return registro


@pytest_asyncio.fixture
async def conta_shopee(db: AsyncSession, tenant: Tenant) -> ChannelAccount:
    from app.connectors.mock import ConectorMock
    from app.services import tokens

    registro = ChannelAccount(
        tenant_id=tenant.id,
        channel=Canal.SHOPEE,
        external_account_id="987654321",
        nickname="Loja Shopee Teste",
        site_id="BR",
        status=StatusConta.CONECTADA,
        connected_at=datetime.now(UTC),
    )
    db.add(registro)
    await db.commit()

    pacote = await ConectorMock(Canal.SHOPEE).exchange_code("CODE", shop_id="987654321")
    await tokens.salvar_tokens(db, registro.id, pacote)
    await db.commit()
    return registro


@pytest_asyncio.fixture
async def cliente(engine, usuario) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP autenticado, com a sessão do teste injetada na aplicação."""
    from app.db.session import get_db
    from app.main import app

    fabrica = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def sobrepor_db():
        async with fabrica() as sessao:
            yield sessao

    app.dependency_overrides[get_db] = sobrepor_db

    transporte = ASGITransport(app=app)
    async with AsyncClient(transport=transporte, base_url="http://teste") as http:
        resposta = await http.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "senha": "senha-de-teste-123"},
        )
        assert resposta.status_code == 200, resposta.text
        http.headers["Authorization"] = f"Bearer {resposta.json()['access_token']}"
        yield http

    app.dependency_overrides.clear()
