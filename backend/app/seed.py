"""Carga inicial: organização de demonstração, usuário admin e catálogo.

Roda no startup em ambiente não-produtivo. Idempotente — executar várias vezes
não duplica nada.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_senha
from app.models.catalog import Product
from app.models.channel import ChannelAccount
from app.models.enums import Canal, PapelUsuario, StatusConta
from app.models.metrics import AlertRule
from app.models.tenant import Tenant, User

log = structlog.get_logger(__name__)

# Catálogo espelhando a operação real de autopeças (retentores, anéis,
# bronzinas e vedadores), com custos plausíveis para o cálculo de margem.
CATALOGO = [
    ("5338", "Retentor de Válvula Motor AP 1.6/1.8/2.0", "Sabó", Decimal("14.20")),
    ("8126", "Jogo de Anéis de Pistão 0.50mm Fire 1.0", "Cofap", Decimal("58.40")),
    ("5245", "Bronzina de Biela STD Motor Zetec 1.8", "Metal Leve", Decimal("41.30")),
    ("7712", "Vedador de Cabeçote Motor EA111 1.6", "Sabó", Decimal("28.90")),
    ("3390", "Retentor Dianteiro Virabrequim Corsa 1.0", "Corteco", Decimal("16.75")),
    ("9104", "Kit Junta Motor Completo Palio Fire", "Taranto", Decimal("104.60")),
    ("6621", "Bronzina de Mancal 0.25mm Gol 1.0 8V", "Metal Leve", Decimal("38.20")),
    ("4457", "Retentor Traseiro Câmbio HB20 1.0", "Corteco", Decimal("22.15")),
]


async def executar(db: AsyncSession) -> dict[str, int]:
    """Cria os dados iniciais. Seguro de re-executar."""
    tenant = await _tenant(db)
    usuario = await _admin(db, tenant.id)
    produtos = await _produtos(db, tenant.id)
    contas = await _contas_simuladas(db, tenant.id)
    regras = await _regras_alerta(db, tenant.id)
    await db.commit()

    resumo = {
        "tenant_id": tenant.id,
        "usuario_id": usuario.id,
        "produtos": produtos,
        "contas": contas,
        "regras": regras,
    }
    log.info("seed_concluido", **resumo)
    return resumo


async def _tenant(db: AsyncSession) -> Tenant:
    tenant = await db.scalar(select(Tenant).where(Tenant.slug == "demo"))
    if tenant is None:
        tenant = Tenant(
            name=settings.ADMIN_TENANT, slug="demo", plan="pro", status="active"
        )
        db.add(tenant)
        await db.flush()
    return tenant


async def _admin(db: AsyncSession, tenant_id: int) -> User:
    email = settings.ADMIN_EMAIL.lower()
    usuario = await db.scalar(select(User).where(User.email == email))
    if usuario is None:
        usuario = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_senha(settings.ADMIN_PASSWORD),
            full_name="Administrador",
            role=PapelUsuario.PROPRIETARIO,
        )
        db.add(usuario)
        await db.flush()
        if settings.is_production:
            log.warning(
                "admin_criado_com_senha_padrao",
                aviso="Troque a senha imediatamente em produção.",
            )
    return usuario


async def _produtos(db: AsyncSession, tenant_id: int) -> int:
    criados = 0
    for sku, nome, marca, custo in CATALOGO:
        existente = await db.scalar(
            select(Product).where(Product.tenant_id == tenant_id, Product.sku == sku)
        )
        if existente is None:
            db.add(
                Product(
                    tenant_id=tenant_id,
                    sku=sku,
                    name=nome,
                    brand=marca,
                    category="Motor",
                    unit_cost=custo,
                )
            )
            criados += 1
    await db.flush()
    return criados


async def _contas_simuladas(db: AsyncSession, tenant_id: int) -> int:
    """Cria contas conectadas simuladas.

    Só em modo mock: em produção a conta só existe depois de uma autorização
    real do vendedor, e criar uma conta falsa ali seria mentir sobre o estado da
    integração.
    """
    if not settings.USE_MOCK_CONNECTORS:
        return 0

    from app.connectors.mock import ConectorMock
    from app.services import tokens

    criadas = 0
    for canal in (Canal.MERCADO_LIVRE, Canal.SHOPEE):
        conector = ConectorMock(canal)
        info = await conector.fetch_account_info("mock")

        existente = await db.scalar(
            select(ChannelAccount).where(
                ChannelAccount.tenant_id == tenant_id, ChannelAccount.channel == canal
            )
        )
        if existente is not None:
            continue

        conta = ChannelAccount(
            tenant_id=tenant_id,
            channel=canal,
            external_account_id=info.external_account_id,
            nickname=info.nickname,
            site_id=info.site_id,
            status=StatusConta.CONECTADA,
            scopes=["read", "offline_access"],
            connected_at=datetime.now(UTC),
            metadata_json={"simulada": True},
        )
        db.add(conta)
        await db.flush()
        await tokens.salvar_tokens(
            db, conta.id, await conector.exchange_code("SEED", shop_id=info.external_account_id)
        )
        criadas += 1

    return criadas


async def _regras_alerta(db: AsyncSession, tenant_id: int) -> int:
    padrao = [
        ("Ruptura de estoque", "stock_out", Decimal("0")),
        ("Divergência de conciliação", "divergence", Decimal("5")),
    ]
    criadas = 0
    for nome, tipo, limite in padrao:
        existente = await db.scalar(
            select(AlertRule).where(AlertRule.tenant_id == tenant_id, AlertRule.kind == tipo)
        )
        if existente is None:
            db.add(
                AlertRule(tenant_id=tenant_id, name=nome, kind=tipo, threshold=limite)
            )
            criadas += 1
    await db.flush()
    return criadas
