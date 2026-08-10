"""Isolamento entre clientes do SaaS.

O vazamento de dados entre tenants é o incidente mais grave possível num
produto multiempresa: um vendedor veria o faturamento do concorrente. Este
arquivo existe para que essa regressão quebre o CI antes de chegar a produção.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.security import criar_access_token, hash_senha
from app.models.enums import Canal, PapelUsuario, StatusConta, StatusPedido
from app.models.channel import ChannelAccount
from app.models.order import Order
from app.models.tenant import Tenant, User

pytestmark = pytest.mark.asyncio


async def _criar_tenant_com_pedido(db, nome: str, valor: str) -> tuple[Tenant, Order, User]:
    tenant = Tenant(name=nome, slug=nome.lower(), plan="pro", status="active")
    db.add(tenant)
    await db.flush()

    usuario = User(
        tenant_id=tenant.id,
        email=f"{nome.lower()}@exemplo.com.br",
        password_hash=hash_senha("senha-de-teste-123"),
        role=PapelUsuario.PROPRIETARIO,
    )
    conta = ChannelAccount(
        tenant_id=tenant.id,
        channel=Canal.MERCADO_LIVRE,
        external_account_id=f"ext-{nome}",
        status=StatusConta.CONECTADA,
    )
    db.add_all([usuario, conta])
    await db.flush()

    pedido = Order(
        tenant_id=tenant.id,
        channel_account_id=conta.id,
        channel=Canal.MERCADO_LIVRE,
        external_id=f"pedido-{nome}",
        status=StatusPedido.PAGO,
        date_created=datetime.now(UTC),
        gross_amount=Decimal(valor),
        net_amount=Decimal(valor),
    )
    db.add(pedido)
    await db.commit()
    return tenant, pedido, usuario


async def test_um_tenant_nao_ve_pedido_de_outro(engine, db):
    """Cenário central: dois clientes, cada um enxerga apenas o próprio dado."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.session import get_db
    from app.main import app

    tenant_a, pedido_a, usuario_a = await _criar_tenant_com_pedido(db, "Alfa", "1000.00")
    tenant_b, pedido_b, _ = await _criar_tenant_com_pedido(db, "Beta", "9999.00")

    fabrica = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def sobrepor():
        async with fabrica() as s:
            yield s

    app.dependency_overrides[get_db] = sobrepor
    token = criar_access_token(
        user_id=usuario_a.id, tenant_id=tenant_a.id, role=PapelUsuario.PROPRIETARIO
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://teste"
        ) as http:
            http.headers["Authorization"] = f"Bearer {token}"

            lista = await http.get("/api/v1/orders")
            assert lista.status_code == 200
            externos = {p["external_id"] for p in lista.json()["itens"]}
            assert externos == {"pedido-Alfa"}

            # O faturamento do tenant B não pode influenciar nenhum agregado.
            visao = await http.get("/api/v1/dashboard/overview")
            assert visao.json()["kpis"]["receita_bruta"]["valor"] == "1000.00"

            # Acesso direto ao recurso alheio: 404, nunca 403 — confirmar a
            # existência do registro já seria vazamento de informação.
            alheio = await http.get(f"/api/v1/orders/{pedido_b.id}")
            assert alheio.status_code == 404

            proprio = await http.get(f"/api/v1/orders/{pedido_a.id}")
            assert proprio.status_code == 200
    finally:
        app.dependency_overrides.clear()


async def test_token_sem_assinatura_valida_e_recusado(cliente):
    cliente.headers["Authorization"] = "Bearer token.forjado.aqui"
    resposta = await cliente.get("/api/v1/orders")
    assert resposta.status_code == 401


async def test_endpoint_protegido_exige_autenticacao(engine):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://teste") as http:
        assert (await http.get("/api/v1/orders")).status_code == 401
        assert (await http.get("/api/v1/dashboard/overview")).status_code == 401
        assert (await http.get("/api/v1/finance/waterfall")).status_code == 401


async def test_papel_insuficiente_e_bloqueado_no_servidor(engine, db, tenant):
    """Esconder o botão na interface é conveniência; o controle é no servidor."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.session import get_db
    from app.main import app

    leitor = User(
        tenant_id=tenant.id,
        email="leitor@exemplo.com.br",
        password_hash=hash_senha("senha-de-teste-123"),
        role=PapelUsuario.LEITOR,
    )
    db.add(leitor)
    await db.commit()

    fabrica = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def sobrepor():
        async with fabrica() as s:
            yield s

    app.dependency_overrides[get_db] = sobrepor
    token = criar_access_token(user_id=leitor.id, tenant_id=tenant.id, role=PapelUsuario.LEITOR)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://teste"
        ) as http:
            http.headers["Authorization"] = f"Bearer {token}"
            # Leitura permitida...
            assert (await http.get("/api/v1/dashboard/overview")).status_code == 200
            # ...mas conectar conta exige perfil administrativo.
            assert (await http.get("/api/v1/oauth/mercadolivre/authorize")).status_code == 403
            # E cadastrar produto exige ao menos analista.
            criar = await http.post(
                "/api/v1/catalog/products", json={"sku": "X1", "name": "Teste"}
            )
            assert criar.status_code == 403
    finally:
        app.dependency_overrides.clear()
