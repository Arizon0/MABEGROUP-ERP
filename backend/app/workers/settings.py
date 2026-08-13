"""Configuração do worker ARQ: fila, cron e políticas de retentativa.

Executar com::

    arq app.workers.settings.WorkerSettings

Frequências dimensionadas em ``docs/05-tempo-real.md`` §5.6 — o orçamento total
fica em torno de 750–900 chamadas por conta por dia, com folga confortável em
relação aos limites praticados pelos marketplaces.
"""
from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import configurar_logging
from app.workers import tasks


async def ao_iniciar(ctx: dict) -> None:
    configurar_logging()
    import structlog

    structlog.get_logger(__name__).info(
        "worker_iniciado", ambiente=settings.ENVIRONMENT, mock=settings.USE_MOCK_CONNECTORS
    )


async def ao_encerrar(ctx: dict) -> None:
    from app.db.session import engine

    await engine.dispose()


class WorkerSettings:
    """Definição consumida pela CLI do ARQ."""

    functions = [
        tasks.processar_webhook,
        tasks.backfill_conta,
        tasks.semear_demonstracao,
        tasks.drenar_webhooks,
        tasks.sincronizar_pedidos_recentes,
        tasks.sincronizar_catalogo,
        tasks.renovar_tokens,
        tasks.atualizar_metricas,
        tasks.conciliar,
        tasks.capturar_snapshots,
        tasks.limpar_dados_antigos,
        tasks.verificar_alertas,
        tasks.enriquecer_pedidos,
    ]

    cron_jobs = [
        # Rede de segurança da fila: recupera eventos que não foram enfileirados
        # ou cujo worker morreu no meio do processamento.
        cron(tasks.drenar_webhooks, minute=set(range(0, 60, 2)), run_at_startup=True),
        # Completa o frete dos pedidos que o backfill importou sem
        # enriquecimento. A cada três minutos, em lotes pequenos: espaçado o
        # bastante para não competir com a sincronização pelo limite de
        # requisições do canal.
        cron(tasks.enriquecer_pedidos, minute=set(range(0, 60, 3))),
        # Polling incremental — cobre o que o webhook não entregou.
        cron(tasks.sincronizar_pedidos_recentes, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        # Rollups do painel.
        cron(tasks.atualizar_metricas, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}),
        # Catálogo, estoque e perguntas.
        cron(tasks.sincronizar_catalogo, minute={8}),
        # Renovação proativa de token, com folga sobre a validade de 4 h da Shopee.
        cron(tasks.renovar_tokens, minute={13}),
        # Alertas configurados pelo usuário.
        cron(tasks.verificar_alertas, minute={25, 55}),
        # Conciliação financeira diária, na madrugada (03:00 UTC-3 ≈ 06:00 UTC).
        cron(tasks.conciliar, hour={6}, minute={0}),
        # Fotografia diária de indicadores que o marketplace não versiona.
        cron(tasks.capturar_snapshots, hour={6}, minute={30}),
        # Retenção e limpeza.
        cron(tasks.limpar_dados_antigos, hour={7}, minute={0}),
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL or "redis://localhost:6379")
    on_startup = ao_iniciar
    on_shutdown = ao_encerrar

    max_jobs = 20
    job_timeout = 600           # backfill de janela grande é legitimamente lento
    keep_result = 3600
    max_tries = 3
    health_check_interval = 60
