"""Tarefas assíncronas executadas pelos workers.

Rodam num processo separado da API — não é preferência de estilo: um backfill de
24 meses satura CPU por horas, e no mesmo processo da API deixaria o painel de
todos os tenants lento durante o onboarding de um único cliente.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.channel import ChannelAccount, WebhookEvent
from app.models.enums import Canal, StatusConta, StatusWebhook
from app.models.tenant import Tenant
from app.services import accounts, analytics, reconciliation, sync, tokens, webhooks

log = structlog.get_logger(__name__)


async def processar_webhook(_ctx: dict[str, Any], evento_id: int) -> dict[str, Any]:
    """Processa uma notificação recém-recebida."""
    async with SessionLocal() as db:
        ok = await webhooks.processar(db, evento_id)
        return {"evento_id": evento_id, "sucesso": ok}


async def drenar_webhooks(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Varredura de segurança da fila de webhooks.

    Existe porque o enfileiramento pode falhar (Redis indisponível no instante
    da requisição) ou o worker pode morrer no meio do processamento. O evento
    continua ``pending`` no banco, e esta varredura o recupera — nada some por
    causa de uma falha transitória de infraestrutura.
    """
    async with SessionLocal() as db:
        ids = await webhooks.pendentes(db, limite=200)
        sucessos = 0
        for evento_id in ids:
            if await webhooks.processar(db, evento_id):
                sucessos += 1
        if ids:
            log.info("webhooks_drenados", total=len(ids), sucessos=sucessos)
        return {"processados": len(ids), "sucessos": sucessos}


async def sincronizar_pedidos_recentes(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Polling incremental de pedidos (a cada 5 minutos)."""
    async with SessionLocal() as db:
        contas = await sync.contas_ativas(db)
        total = {"contas": 0, "criados": 0, "atualizados": 0}
        for conta in contas:
            if conta.channel == Canal.MERCADO_PAGO:
                continue
            resultado = await sync.sincronizar_pedidos(db, conta)
            total["contas"] += 1
            total["criados"] += resultado.criados
            total["atualizados"] += resultado.atualizados
        return total


async def sincronizar_catalogo(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Anúncios, estoque e perguntas (a cada hora)."""
    async with SessionLocal() as db:
        contas = await sync.contas_ativas(db)
        total = {"anuncios": 0, "perguntas": 0}
        for conta in contas:
            if conta.channel == Canal.MERCADO_PAGO:
                continue
            total["anuncios"] += (await sync.sincronizar_anuncios(db, conta)).atualizados
            total["perguntas"] += (await sync.sincronizar_perguntas(db, conta)).atualizados
        return total


async def renovar_tokens(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Renovação proativa (de hora em hora).

    Renovar antes de precisar evita duas coisas: uma requisição de usuário pagar
    o custo do refresh, e várias chamadas simultâneas encontrarem o token já
    vencido — que é justamente a corrida que derruba a conta no Mercado Livre.
    """
    async with SessionLocal() as db:
        contas = await tokens.contas_para_renovar(db)
        renovados, falhas = 0, 0
        for conta in contas:
            try:
                await tokens.obter_access_token(db, conta)
                renovados += 1
            except Exception as exc:
                falhas += 1
                log.error(
                    "renovacao_token_falhou",
                    conta=conta.id,
                    canal=conta.channel,
                    erro=str(exc),
                )
        if contas:
            log.info("tokens_renovados", renovados=renovados, falhas=falhas)
        return {"avaliados": len(contas), "renovados": renovados, "falhas": falhas}


async def atualizar_metricas(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Recalcula os rollups dos buckets recentes (a cada 5 minutos)."""
    async with SessionLocal() as db:
        tenants = list((await db.execute(select(Tenant.id).where(Tenant.status == "active"))).scalars())
        total = {"tenants": 0, "horas": 0, "dias": 0}
        for tenant_id in tenants:
            resultado = await analytics.recalcular_rollups(db, tenant_id, horas=3)
            total["tenants"] += 1
            total["horas"] += resultado["horas"]
            total["dias"] += resultado["dias"]
        return total


async def conciliar(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Conciliação financeira diária."""
    async with SessionLocal() as db:
        tenants = list((await db.execute(select(Tenant.id).where(Tenant.status == "active"))).scalars())
        total = {"tenants": 0, "divergentes": 0}
        for tenant_id in tenants:
            resultado = await reconciliation.conciliar_periodo(db, tenant_id, dias=45)
            total["tenants"] += 1
            total["divergentes"] += resultado.divergentes
        return total


async def capturar_snapshots(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Fotografa diariamente indicadores que o marketplace não versiona."""
    async with SessionLocal() as db:
        contas = await sync.contas_ativas(db)
        for conta in contas:
            if conta.channel != Canal.MERCADO_PAGO:
                await sync.capturar_reputacao(db, conta)
        return {"contas": len(contas)}


async def backfill_conta(_ctx: dict[str, Any], conta_id: int, dias: int = 90) -> dict[str, Any]:
    """Carga histórica de uma conta recém-conectada.

    Roda em fases para que o painel mostre algo útil em segundos, enquanto o
    histórico completo continua carregando em segundo plano.
    """
    async with SessionLocal() as db:
        conta = await db.get(ChannelAccount, conta_id)
        if conta is None:
            return {"erro": "conta não encontrada"}

        cursor = await sync.obter_cursor(db, conta.id, sync.Recurso.PEDIDOS)
        cursor.last_synced_at = datetime.now(UTC) - timedelta(days=dias)
        cursor.progress_pct = 0
        await db.commit()

        resultados = await sync.sincronizar_conta(db, conta)
        await analytics.recalcular_rollups(db, conta.tenant_id, horas=24 * dias)
        return {
            "conta_id": conta_id,
            "resultados": [r.como_dict() for r in resultados],
        }


async def limpar_dados_antigos(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Retenção: remove eventos e logs vencidos (ver docs/02 §2.4).

    Dado financeiro nunca é apagado — só ruído operacional de curta validade.
    """
    from sqlalchemy import delete

    from app.models.channel import IntegrationLog

    async with SessionLocal() as db:
        corte_webhooks = datetime.now(UTC) - timedelta(days=90)
        removidos = (
            await db.execute(
                delete(WebhookEvent).where(
                    WebhookEvent.received_at < corte_webhooks,
                    WebhookEvent.status == StatusWebhook.CONCLUIDO,
                )
            )
        ).rowcount or 0

        corte_logs = datetime.now(UTC) - timedelta(days=30)
        logs = (
            await db.execute(delete(IntegrationLog).where(IntegrationLog.created_at < corte_logs))
        ).rowcount or 0

        states = await accounts.limpar_states_expirados(db)
        await db.commit()
        return {"webhooks": removidos, "logs": logs, "oauth_states": states}


async def verificar_alertas(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Avalia as regras de alerta configuradas pelos tenants."""
    from decimal import Decimal

    from app.events import bus
    from app.models.catalog import Listing
    from app.models.metrics import Alert, AlertRule

    async with SessionLocal() as db:
        regras = list(
            (await db.execute(select(AlertRule).where(AlertRule.is_active.is_(True)))).scalars()
        )
        disparados = 0

        for regra in regras:
            titulo = mensagem = ""
            if regra.kind == "stock_out":
                total = await db.scalar(
                    select(Listing)
                    .where(
                        Listing.tenant_id == regra.tenant_id,
                        Listing.available_quantity <= int(regra.threshold or 0),
                        Listing.status == "active",
                    )
                    .limit(1)
                )
                if total is not None:
                    titulo = "Anúncios em ruptura"
                    mensagem = "Há anúncios ativos com estoque no ou abaixo do limite configurado."
            elif regra.kind == "divergence":
                resumo = await reconciliation.resumo(db, regra.tenant_id, dias=7)
                divergentes = resumo["por_status"].get("divergent", {}).get("quantidade", 0)
                if divergentes >= int(regra.threshold or 1):
                    titulo = "Divergências de conciliação"
                    mensagem = f"{divergentes} pedidos com divergência financeira nos últimos 7 dias."

            if not titulo:
                continue

            # Evita repetir o mesmo alerta a cada execução do job.
            recente = await db.scalar(
                select(Alert)
                .where(
                    Alert.tenant_id == regra.tenant_id,
                    Alert.rule_id == regra.id,
                    Alert.created_at >= datetime.now(UTC) - timedelta(hours=6),
                )
                .limit(1)
            )
            if recente is not None:
                continue

            db.add(
                Alert(
                    tenant_id=regra.tenant_id,
                    rule_id=regra.id,
                    kind=regra.kind,
                    severity="warning",
                    title=titulo,
                    message=mensagem,
                    created_at=datetime.now(UTC),
                )
            )
            await bus.publicar(
                bus.TipoEvento.ALERTA,
                regra.tenant_id,
                {"severity": "warning", "title": titulo, "message": mensagem},
            )
            disparados += 1

        await db.commit()
        return {"regras": len(regras), "disparados": disparados}


async def semear_demonstracao(_ctx: dict[str, Any], tenant_id: int = 1) -> dict[str, Any]:
    """Popula o tenant com dados simulados (uso local e demonstração)."""
    async with SessionLocal() as db:
        contas = list(
            (
                await db.execute(
                    select(ChannelAccount).where(
                        ChannelAccount.tenant_id == tenant_id,
                        ChannelAccount.status == StatusConta.CONECTADA,
                    )
                )
            ).scalars()
        )
        for conta in contas:
            await sync.sincronizar_conta(db, conta)
        await analytics.recalcular_rollups(db, tenant_id, horas=24 * 90)
        await reconciliation.conciliar_periodo(db, tenant_id, dias=90)
        return {"contas": len(contas)}
