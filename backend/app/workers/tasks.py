"""Tarefas assíncronas executadas pelos workers.

Rodam num processo separado da API — não é preferência de estilo: um backfill de
24 meses satura CPU por horas, e no mesmo processo da API deixaria o painel de
todos os tenants lento durante o onboarding de um único cliente.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select

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
    """Anúncios, estoque, perguntas, reclamações e campanhas (a cada hora)."""
    async with SessionLocal() as db:
        contas = await sync.contas_ativas(db)
        total = {"anuncios": 0, "perguntas": 0, "reclamacoes": 0, "campanhas": 0}
        for conta in contas:
            if conta.channel == Canal.MERCADO_PAGO:
                continue
            total["anuncios"] += (await sync.sincronizar_anuncios(db, conta)).atualizados
            total["perguntas"] += (await sync.sincronizar_perguntas(db, conta)).atualizados
            total["reclamacoes"] += (await sync.sincronizar_reclamacoes(db, conta)).atualizados
            total["campanhas"] += (await sync.sincronizar_campanhas(db, conta)).atualizados
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


def _recalcular_liquido(pedido: Any) -> None:
    """Refaz o líquido a partir das parcelas já conhecidas do pedido.

    Só recalcula quando o líquido foi **calculado** pelo sistema. Se o canal
    informou o valor ou ele veio de um repasse liquidado, o número dele vale
    mais que qualquer conta nossa — sobrescrevê-lo faria o painel divergir do
    extrato que o vendedor confere.
    """
    from app.models.enums import FonteLiquido, StatusPedido
    from app.services.finance import arredondar

    if pedido.net_source not in (FonteLiquido.CALCULADO, None, ""):
        return
    if pedido.status == StatusPedido.CANCELADO:
        return

    pedido.net_amount = arredondar(
        Decimal(str(pedido.gross_amount or 0))
        + Decimal(str(pedido.shipping_revenue or 0))
        - Decimal(str(pedido.platform_fee or 0))
        - Decimal(str(pedido.payment_fee or 0))
        - Decimal(str(pedido.shipping_cost or 0))
        - Decimal(str(pedido.tax_amount or 0))
        + Decimal(str(pedido.discount_amount or 0))
        - Decimal(str(pedido.refund_amount or 0))
    )


async def enriquecer_pedidos(_ctx: dict[str, Any], limite: int = 150) -> dict[str, Any]:
    """Preenche o custo de frete dos pedidos importados sem enriquecimento.

    O backfill de volume alto importa só o que vem no payload da busca, para não
    triplicar as chamadas à API. Esta tarefa cobre a diferença depois, em lotes
    pequenos e espaçados.

    O identificador do envio vem do **payload bruto do próprio pedido**, não da
    tabela de envios: quando o backfill pula o enriquecimento, nenhum registro
    de envio chega a ser criado, então não há de onde partir a não ser do que
    foi guardado na importação.

    Processa os mais recentes primeiro — são os que o vendedor olha.
    """
    from app.connectors import obter_conector
    from app.models.channel import ChannelAccount
    from app.models.order import Order, Shipment
    from app.services import ingest, tokens

    async with SessionLocal() as db:
        pendentes = list(
            (
                await db.execute(
                    select(Order)
                    .where(
                        Order.status != "cancelled",
                        # A fila é "pedido sem registro de envio", não "pedido
                        # com frete zero". Frete zero é resultado legítimo —
                        # frete grátis existe —, e usá-lo como critério faz o
                        # pedido voltar à fila depois de enriquecido, num laço
                        # que nunca termina.
                        ~select(Shipment.id)
                        .where(Shipment.order_id == Order.id)
                        .exists(),
                    )
                    .order_by(Order.date_created.desc())
                    .limit(limite)
                )
            ).scalars()
        )
        if not pendentes:
            return {"enriquecidos": 0, "restantes": 0}

        enriquecidos = 0
        erros = 0
        sem_envio = 0
        por_conta: dict[int, tuple[Any, str]] = {}

        for pedido in pendentes:
            id_envio = _id_do_envio(pedido)
            if not id_envio:
                # Venda sem envio (retirada, digital) ou payload sem o campo.
                # Marcar com custo negativo mínimo distorceria o número, então
                # apenas se registra e segue — o pedido não volta à fila porque
                # não há o que buscar.
                sem_envio += 1
                continue

            if pedido.channel_account_id not in por_conta:
                conta = await db.get(ChannelAccount, pedido.channel_account_id)
                if conta is None:
                    continue
                try:
                    por_conta[conta.id] = (conta, await tokens.obter_access_token(db, conta))
                except Exception:
                    continue

            conta, token = por_conta[pedido.channel_account_id]
            conector = obter_conector(conta.channel)
            try:
                envio = await conector.fetch_shipment(
                    token, id_envio, shop_id=conta.external_account_id
                )
                if envio:
                    registro = await ingest.salvar_envio(db, conta, envio)
                    # Vínculo explícito: o enriquecimento parte do pedido, então
                    # a relação é conhecida aqui. Deixá-la a cargo do
                    # `external_order_id` devolvido pelo canal faz o envio ficar
                    # órfão quando o canal não repete esse campo na consulta de
                    # envio — e sem o vínculo o pedido nunca sai da fila.
                    registro.order_id = pedido.id
                    pedido.shipping_cost = envio.cost_seller
                    # Recalcular o líquido é obrigatório, não opcional: o frete
                    # entra na fórmula, e gravá-lo sem refazer a conta deixaria o
                    # pedido com o custo registrado e o líquido de antes,
                    # inflado exatamente pelo valor do frete.
                    _recalcular_liquido(pedido)
                    enriquecidos += 1
            except Exception:
                # Falha num pedido não pode parar o lote: ele continua na fila e
                # a próxima execução tenta de novo.
                erros += 1

        await db.commit()

    log.info(
        "enriquecimento_concluido",
        enriquecidos=enriquecidos,
        erros=erros,
        sem_envio=sem_envio,
    )
    return {"enriquecidos": enriquecidos, "erros": erros, "sem_envio": sem_envio}


def _id_do_envio(pedido: Any) -> str:
    """Extrai o identificador do envio do payload guardado na importação."""
    bruto = pedido.raw or {}
    if pedido.channel == "shopee":
        return str(bruto.get("package_number") or "")
    envio = bruto.get("shipping") or {}
    return str(envio.get("id") or "")
