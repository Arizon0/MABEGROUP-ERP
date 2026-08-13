"""Sincronização incremental por marca d'água.

É a rede de segurança do sistema: webhooks se perdem — isso é fato operacional,
não hipótese. O polling garante completude; o webhook garante latência baixa.
Ambos terminam no mesmo UPSERT idempotente de ``services/ingest.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import obter_conector
from app.core.config import settings
from app.events import bus
from app.models.channel import ChannelAccount, SyncCursor
from app.models.enums import Canal, StatusConta
from app.services import finance, ingest, tokens

log = structlog.get_logger(__name__)


class Recurso:
    PEDIDOS = "orders"
    PAGAMENTOS = "payments"
    ENVIOS = "shipments"
    ANUNCIOS = "listings"
    PERGUNTAS = "questions"
    RECLAMACOES = "claims"
    CAMPANHAS = "campaigns"
    ESCROW = "escrow"
    REPASSES = "settlements"
    REPUTACAO = "reputation"


@dataclass(slots=True)
class ResultadoSync:
    recurso: str
    conta_id: int
    criados: int = 0
    atualizados: int = 0
    erros: list[str] = field(default_factory=list)
    duracao_s: float = 0.0

    def como_dict(self) -> dict[str, Any]:
        return {
            "recurso": self.recurso,
            "conta_id": self.conta_id,
            "criados": self.criados,
            "atualizados": self.atualizados,
            "erros": self.erros,
            "duracao_s": round(self.duracao_s, 2),
        }


async def obter_cursor(db: AsyncSession, conta_id: int, recurso: str) -> SyncCursor:
    cursor = await db.scalar(
        select(SyncCursor).where(
            SyncCursor.channel_account_id == conta_id, SyncCursor.resource == recurso
        )
    )
    if cursor is None:
        cursor = SyncCursor(channel_account_id=conta_id, resource=recurso)
        db.add(cursor)
        await db.flush()
    return cursor


def _janela(cursor: SyncCursor) -> tuple[datetime, datetime]:
    """Calcula a janela a consultar, com sobreposição de segurança.

    A sobreposição de :data:`settings.SYNC_OVERLAP_MINUTES` compensa relógio
    dessincronizado e escrita atrasada do lado do marketplace. Sem ela, um pedido
    criado durante a janela anterior desaparece para sempre — e nada dá erro,
    que é o pior tipo de falha.
    """
    agora = datetime.now(UTC)
    if cursor.last_synced_at is None:
        return agora - timedelta(days=settings.BACKFILL_DAYS), agora
    inicio = cursor.last_synced_at
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=UTC)
    return inicio - timedelta(minutes=settings.SYNC_OVERLAP_MINUTES), agora


#: Pedidos gravados por transação durante o backfill. Lote pequeno demais paga
#: um commit por pedido; grande demais devolve ao ponto de perder muito trabalho
#: numa falha. Cinquenta mantém a perda máxima em segundos de trabalho.
TAMANHO_DO_LOTE = 50

#: Acima disto a sincronização é tratada como backfill: importa o essencial e
#: deixa o enriquecimento por pedido para o worker. Duzentos pedidos com três
#: chamadas cada já são seiscentas requisições — o teto do que faz sentido pagar
#: dentro de uma única execução.
LIMIAR_DE_BACKFILL = 200


async def sincronizar_pedidos(
    db: AsyncSession, conta: ChannelAccount, *, enriquecer: bool | None = None
) -> ResultadoSync:
    """Sincroniza pedidos alterados desde a última marca d'água.

    ``enriquecer`` controla a busca de frete e pagamento **por pedido**. Cada
    pedido custa de duas a três chamadas extras à API, e num backfill de
    milhares de pedidos isso multiplica o volume por três — o suficiente para
    esbarrar no limite de requisições do canal e transformar uma importação de
    minutos em horas de espera com ``Retry-After``.

    O padrão (``None``) decide pelo tamanho: acima de :data:`LIMIAR_DE_BACKFILL`
    pedidos, importa só o essencial — que já vem no próprio payload da busca:
    totais, itens, comprador, status. O custo de frete fica pendente e é
    preenchido depois, aos poucos, pelo worker. Assim o vendedor vê o
    faturamento no mesmo dia em vez de esperar a noite inteira.
    """
    import time

    inicio_exec = time.monotonic()
    resultado = ResultadoSync(recurso=Recurso.PEDIDOS, conta_id=conta.id)
    cursor = await obter_cursor(db, conta.id, Recurso.PEDIDOS)
    desde, ate = _janela(cursor)

    try:
        token = await tokens.obter_access_token(db, conta)
        conector = obter_conector(conta.channel)
        pedidos = await conector.fetch_orders(
            token,
            since=desde,
            until=ate,
            seller_id=conta.external_account_id,
            shop_id=conta.external_account_id,
        )

        # Decide pelo volume: um backfill grande não pode pagar três chamadas
        # por pedido, uma sincronização incremental de dez pedidos pode.
        enriquecer_agora = (
            enriquecer if enriquecer is not None else len(pedidos) <= LIMIAR_DE_BACKFILL
        )
        if not enriquecer_agora and pedidos:
            log.info(
                "backfill_sem_enriquecimento",
                conta=conta.id,
                pedidos=len(pedidos),
                motivo=(
                    "volume alto: frete e pagamento serão preenchidos depois pelo "
                    "worker, para não multiplicar por três as chamadas à API"
                ),
            )

        maior_atualizacao = cursor.last_synced_at
        # Gravar em lotes, e não só no fim: um backfill de 90 dias percorre
        # centenas de pedidos, e um único commit ao final significa que uma
        # falha no pedido 690 de 700 descarta os 689 já importados — o
        # ``rollback`` do except leva tudo junto. Com lote, o que já entrou
        # fica, o painel mostra progresso enquanto roda, e a reexecução retoma
        # de onde parou em vez de começar do zero.
        for indice, canonico in enumerate(pedidos, start=1):
            envio: Any | None = None
            pagamentos: list[Any] = []
            if enriquecer_agora:
                envio, pagamentos = await _coletar_complementos(
                    conta, conector, token, canonico
                )

            # O pedido precisa existir ANTES de envio e pagamento: os dois se
            # vinculam a ele pela chave externa, e gravá-los primeiro deixaria
            # `order_id` nulo — o pedido apareceria eternamente "sem pagamento"
            # na conciliação.
            _, criado = await ingest.salvar_pedido(db, conta, canonico)
            if criado:
                resultado.criados += 1
            else:
                resultado.atualizados += 1

            if envio is not None:
                await ingest.salvar_envio(db, conta, envio)
            for pagamento in pagamentos:
                await ingest.salvar_pagamento(db, conta, pagamento)

            referencia = canonico.date_last_updated or canonico.date_created
            if referencia and referencia.tzinfo is None:
                referencia = referencia.replace(tzinfo=UTC)
            if referencia and (maior_atualizacao is None or referencia > _aware(maior_atualizacao)):
                maior_atualizacao = referencia

            if indice % TAMANHO_DO_LOTE == 0:
                cursor.last_synced_at = maior_atualizacao or cursor.last_synced_at
                cursor.progress_pct = int(indice / len(pedidos) * 100)
                await db.commit()
                log.info(
                    "sync_pedidos_parcial",
                    conta=conta.id,
                    processados=indice,
                    total=len(pedidos),
                )

        # Avança pela maior data observada, não por ``now()``: gravar o relógio
        # local abriria uma janela cega do tamanho da própria sincronização.
        cursor.last_synced_at = maior_atualizacao or ate
        cursor.status = "ok"
        cursor.consecutive_failures = 0
        cursor.last_error = ""
        cursor.backfill_done = True
        cursor.progress_pct = 100
        conta.last_sync_at = datetime.now(UTC)
        conta.status = StatusConta.CONECTADA
        await db.commit()

        await bus.publicar(
            bus.TipoEvento.SINCRONIZACAO_CONCLUIDA,
            conta.tenant_id,
            {"recurso": Recurso.PEDIDOS, **resultado.como_dict()},
            channel=conta.channel,
            account_id=conta.id,
        )
    except Exception as exc:
        await db.rollback()
        await _registrar_falha(db, conta, Recurso.PEDIDOS, exc)
        resultado.erros.append(str(exc)[:400])

    resultado.duracao_s = time.monotonic() - inicio_exec
    log.info("sync_pedidos", conta=conta.id, **resultado.como_dict())
    return resultado


#: Falhas consecutivas de detalhe de pagamento por conta, dentro do processo.
_FALHAS_DE_PAGAMENTO: dict[int, int] = {}

#: A partir daqui o detalhe de pagamento para de ser tentado nesta execução.
LIMITE_DE_FALHAS_DE_PAGAMENTO = 5


def desistir_de_pagamentos(conta_id: int) -> bool:
    return _FALHAS_DE_PAGAMENTO.get(conta_id, 0) >= LIMITE_DE_FALHAS_DE_PAGAMENTO


def _contar_falha_de_pagamento(conta: ChannelAccount) -> None:
    """Interrompe a busca de detalhe de pagamento após falhas seguidas.

    ``/v1/payments/{id}`` pertence ao Mercado Pago. Quando só o Mercado Livre
    está conectado, o token não alcança o recurso e **toda** chamada volta 404 —
    um terço das requisições do backfill gasto em respostas inúteis, que ainda
    consomem cota de limite de uso.

    O contador zera assim que um pagamento é obtido com sucesso, e o estado não
    é persistido: cada execução recomeça a tentar, então conectar o Mercado Pago
    depois volta a enriquecer os pedidos sozinho, sem intervenção.

    O pedido não perde valor com isso: bruto, líquido e taxas já vêm no próprio
    payload do pedido. O detalhe adiciona a quebra de tarifas, não o total.
    """
    atual = _FALHAS_DE_PAGAMENTO.get(conta.id, 0) + 1
    _FALHAS_DE_PAGAMENTO[conta.id] = atual
    if atual == LIMITE_DE_FALHAS_DE_PAGAMENTO:
        log.warning(
            "detalhe_de_pagamento_desativado",
            conta=conta.id,
            canal=conta.channel,
            motivo=(
                "falhas consecutivas ao buscar o detalhe do pagamento; no "
                "Mercado Livre isso normalmente significa que o Mercado Pago "
                "não está conectado. Os totais do pedido não dependem disso."
            ),
        )


async def _coletar_complementos(
    conta: ChannelAccount, conector: Any, token: str, canonico: Any
) -> tuple[Any | None, list[Any]]:
    """Busca envio, pagamentos e escrow do pedido, sem persistir nada.

    É onde estão o custo real do frete e o líquido oficial. Cada falha é
    isolada: perder o custo do frete é ruim, perder o pedido inteiro é pior.

    A função apenas coleta e aplica os valores ao pedido canônico — a gravação
    fica a cargo de quem chama, para garantir que o pedido seja persistido antes
    dos registros que se vinculam a ele.
    """
    envio = None
    if canonico.external_shipment_id:
        try:
            envio = await conector.fetch_shipment(
                token, canonico.external_shipment_id, shop_id=conta.external_account_id
            )
            if envio:
                canonico.shipping_cost = envio.cost_seller
        except Exception as exc:
            log.debug("enriquecimento_envio_falhou", pedido=canonico.external_id, erro=str(exc))

    pagamentos: list[Any] = []
    if conta.channel == Canal.SHOPEE and hasattr(conector, "fetch_escrow"):
        # Na Shopee o escrow **é** o pagamento — não existe registro separado.
        # Buscar os dois e somar contaria o mesmo dinheiro duas vezes.
        try:
            escrow = await conector.fetch_escrow(
                token, canonico.external_id, shop_id=conta.external_account_id
            )
            if escrow and escrow.net_received_amount:
                pagamentos.append(escrow)
        except Exception:
            pass  # antes da conclusão do pedido o escrow ainda não existe
    elif not desistir_de_pagamentos(conta.id):
        for id_pagamento in getattr(canonico, "external_payment_ids", []) or []:
            try:
                pagamento = await conector.fetch_payment(token, id_pagamento)
                if pagamento:
                    pagamentos.append(pagamento)
                    _FALHAS_DE_PAGAMENTO.pop(conta.id, None)
                else:
                    _contar_falha_de_pagamento(conta)
            except Exception as exc:
                _contar_falha_de_pagamento(conta)
                log.debug("enriquecimento_pagamento_falhou", id=id_pagamento, erro=str(exc))

    if pagamentos:
        finance.aplicar_pagamentos(canonico, pagamentos)

    return envio, pagamentos


async def sincronizar_anuncios(db: AsyncSession, conta: ChannelAccount) -> ResultadoSync:
    """Sincroniza anúncios e grava a fotografia de estoque do momento."""
    import time

    from app.models.catalog import InventorySnapshot

    inicio = time.monotonic()
    resultado = ResultadoSync(recurso=Recurso.ANUNCIOS, conta_id=conta.id)
    cursor = await obter_cursor(db, conta.id, Recurso.ANUNCIOS)

    try:
        token = await tokens.obter_access_token(db, conta)
        conector = obter_conector(conta.channel)
        anuncios = await conector.fetch_listings(
            token, seller_id=conta.external_account_id, shop_id=conta.external_account_id
        )
        agora = datetime.now(UTC)
        for canonico in anuncios:
            anuncio = await ingest.salvar_anuncio(db, conta, canonico)
            resultado.atualizados += 1
            # A série de estoque é o que permite medir dias em ruptura depois.
            db.add(
                InventorySnapshot(
                    tenant_id=conta.tenant_id,
                    listing_id=anuncio.id,
                    available=canonico.available_quantity,
                    captured_at=agora,
                )
            )
        cursor.last_synced_at = agora
        cursor.status = "ok"
        cursor.consecutive_failures = 0
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _registrar_falha(db, conta, Recurso.ANUNCIOS, exc)
        resultado.erros.append(str(exc)[:400])

    resultado.duracao_s = time.monotonic() - inicio
    return resultado


async def sincronizar_perguntas(db: AsyncSession, conta: ChannelAccount) -> ResultadoSync:
    import time

    inicio = time.monotonic()
    resultado = ResultadoSync(recurso=Recurso.PERGUNTAS, conta_id=conta.id)
    try:
        token = await tokens.obter_access_token(db, conta)
        conector = obter_conector(conta.channel)
        if not hasattr(conector, "fetch_questions"):
            return resultado
        for pergunta in await conector.fetch_questions(
            token, seller_id=conta.external_account_id, shop_id=conta.external_account_id
        ):
            await ingest.salvar_pergunta(db, conta, pergunta)
            resultado.atualizados += 1
        cursor = await obter_cursor(db, conta.id, Recurso.PERGUNTAS)
        cursor.last_synced_at = datetime.now(UTC)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _registrar_falha(db, conta, Recurso.PERGUNTAS, exc)
        resultado.erros.append(str(exc)[:400])
    resultado.duracao_s = time.monotonic() - inicio
    return resultado


async def sincronizar_reclamacoes(db: AsyncSession, conta: ChannelAccount) -> ResultadoSync:
    """Reclamações, mediações e devoluções.

    Alimenta a aba de Atendimento e o diagnóstico de divergência financeira —
    uma reclamação em aberto costuma explicar um repasse menor que o previsto.
    """
    import time

    inicio = time.monotonic()
    resultado = ResultadoSync(recurso=Recurso.RECLAMACOES, conta_id=conta.id)
    try:
        token = await tokens.obter_access_token(db, conta)
        conector = obter_conector(conta.channel)
        if not hasattr(conector, "fetch_claims"):
            return resultado
        for reclamacao in await conector.fetch_claims(
            token, shop_id=conta.external_account_id
        ):
            await ingest.salvar_reclamacao(db, conta, reclamacao)
            resultado.atualizados += 1
        cursor = await obter_cursor(db, conta.id, Recurso.RECLAMACOES)
        cursor.last_synced_at = datetime.now(UTC)
        cursor.status = "ok"
        cursor.consecutive_failures = 0
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _registrar_falha(db, conta, Recurso.RECLAMACOES, exc)
        resultado.erros.append(str(exc)[:400])
    resultado.duracao_s = time.monotonic() - inicio
    return resultado


async def sincronizar_campanhas(db: AsyncSession, conta: ChannelAccount) -> ResultadoSync:
    """Campanhas, cupons e promoções ativas.

    Não cobre mídia paga: a Ads API da Shopee exige whitelist separada e o
    Mercado Livre não expõe custo por campanha de forma consolidada — esse
    valor entra por lançamento manual (ver ``docs/10-riscos-limitacoes.md``).
    """
    import time

    inicio = time.monotonic()
    resultado = ResultadoSync(recurso=Recurso.CAMPANHAS, conta_id=conta.id)
    try:
        token = await tokens.obter_access_token(db, conta)
        conector = obter_conector(conta.channel)
        if not hasattr(conector, "fetch_campaigns"):
            return resultado
        for campanha in await conector.fetch_campaigns(
            token, shop_id=conta.external_account_id
        ):
            await ingest.salvar_campanha(db, conta, campanha)
            resultado.atualizados += 1
        cursor = await obter_cursor(db, conta.id, Recurso.CAMPANHAS)
        cursor.last_synced_at = datetime.now(UTC)
        cursor.status = "ok"
        cursor.consecutive_failures = 0
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _registrar_falha(db, conta, Recurso.CAMPANHAS, exc)
        resultado.erros.append(str(exc)[:400])
    resultado.duracao_s = time.monotonic() - inicio
    return resultado


async def capturar_reputacao(db: AsyncSession, conta: ChannelAccount) -> None:
    """Fotografa a reputação do dia.

    A API só devolve o estado atual — do lado do marketplace não existe
    histórico. Sem esta captura diária, o gráfico de evolução de reputação é
    impossível de construir depois, porque o dado do passado simplesmente não
    existe em lugar nenhum.
    """
    from app.models.metrics import MetricSnapshot

    try:
        token = await tokens.obter_access_token(db, conta)
        conector = obter_conector(conta.channel)
        if not hasattr(conector, "fetch_seller_reputation"):
            return
        reputacao = await conector.fetch_seller_reputation(
            token, seller_id=conta.external_account_id
        )
        if not reputacao:
            return

        hoje = datetime.now(UTC).date()
        avaliacoes = (reputacao.get("transactions") or {}).get("ratings") or {}
        metricas = {
            "reputation_level": (str(reputacao.get("level_id") or ""), None),
            "power_seller": (str(reputacao.get("power_seller_status") or ""), None),
            "positive_rate": ("", avaliacoes.get("positive")),
            "claims_rate": ("", ((reputacao.get("metrics") or {}).get("claims") or {}).get("rate")),
            "cancellation_rate": (
                "",
                ((reputacao.get("metrics") or {}).get("cancellations") or {}).get("rate"),
            ),
        }

        for nome, (texto, numero) in metricas.items():
            existente = await db.scalar(
                select(MetricSnapshot).where(
                    MetricSnapshot.channel_account_id == conta.id,
                    MetricSnapshot.day == hoje,
                    MetricSnapshot.metric == nome,
                )
            )
            if existente:
                continue
            from decimal import Decimal

            db.add(
                MetricSnapshot(
                    tenant_id=conta.tenant_id,
                    channel_account_id=conta.id,
                    day=hoje,
                    metric=nome,
                    value_text=texto,
                    value_num=Decimal(str(numero)) if numero is not None else None,
                    payload=reputacao if nome == "reputation_level" else {},
                    captured_at=datetime.now(UTC),
                )
            )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        log.debug("captura_reputacao_falhou", conta=conta.id, erro=str(exc))


async def sincronizar_conta(db: AsyncSession, conta: ChannelAccount) -> list[ResultadoSync]:
    """Sincronização completa de uma conta — todos os módulos disponíveis.

    Cada recurso é independente: uma falha em campanhas não impede a
    importação dos pedidos, que é o dado crítico.
    """
    resultados = [await sincronizar_pedidos(db, conta)]
    if conta.channel != Canal.MERCADO_PAGO:
        resultados.append(await sincronizar_anuncios(db, conta))
        resultados.append(await sincronizar_perguntas(db, conta))
        resultados.append(await sincronizar_reclamacoes(db, conta))
        resultados.append(await sincronizar_campanhas(db, conta))
        await capturar_reputacao(db, conta)
    return resultados


async def contas_ativas(db: AsyncSession, canal: str | None = None) -> list[ChannelAccount]:
    consulta = select(ChannelAccount).where(ChannelAccount.status == StatusConta.CONECTADA)
    if canal:
        consulta = consulta.where(ChannelAccount.channel == canal)
    return list((await db.execute(consulta)).scalars())


async def _registrar_falha(
    db: AsyncSession, conta: ChannelAccount, recurso: str, exc: Exception
) -> None:
    """Registra a falha sem avançar o cursor.

    Não avançar é essencial: avançar após um erro pularia definitivamente a
    janela que falhou, e o dado perdido nunca seria recuperado.
    """
    cursor = await obter_cursor(db, conta.id, recurso)
    cursor.consecutive_failures += 1
    cursor.status = "error"
    cursor.last_error = str(exc)[:500]
    conta.last_error = str(exc)[:500]
    if cursor.consecutive_failures >= 5:
        conta.status = StatusConta.ERRO
    await db.commit()
    log.warning(
        "sync_falhou", conta=conta.id, canal=conta.channel, recurso=recurso, erro=str(exc)
    )


def _aware(valor: datetime | None) -> datetime:
    if valor is None:
        return datetime.min.replace(tzinfo=UTC)
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
