"""Barramento de eventos que alimenta o painel ao vivo.

O problema concreto: a API roda em N réplicas. O navegador do vendedor está
conectado por SSE à réplica 2, mas quem processou o pedido foi um *worker*, em
outro processo e possivelmente em outra máquina. O Redis pub/sub liga os dois.

Duas implementações atrás do mesmo protocolo:

* :class:`BarramentoRedis` — produção e qualquer cenário multiprocesso.
* :class:`BarramentoMemoria` — desenvolvimento e testes, sem infraestrutura.

O código de negócio publica eventos sem saber qual está em uso. É isso que
permite ``docker compose up`` funcionar sem Redis e o teste de SSE rodar sem
subir nada.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


class TipoEvento:
    """Tipos publicados no barramento (consumidos pelo frontend por nome)."""

    PEDIDO_CRIADO = "order.created"
    PEDIDO_ATUALIZADO = "order.updated"
    PEDIDO_CANCELADO = "order.cancelled"
    ENVIO_ATUALIZADO = "shipment.updated"
    PAGAMENTO_APROVADO = "payment.approved"
    PERGUNTA_RECEBIDA = "question.received"
    RECLAMACAO_ABERTA = "claim.opened"
    SINCRONIZACAO_CONCLUIDA = "sync.completed"
    ALERTA = "alert.raised"


@dataclass(slots=True)
class Evento:
    """Evento do painel ao vivo."""

    type: str
    tenant_id: int
    payload: dict[str, Any] = field(default_factory=dict)
    channel: str = ""
    account_id: int | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> str:
        dados = asdict(self)
        dados["occurred_at"] = self.occurred_at.isoformat()
        return json.dumps(dados, default=_serializar, ensure_ascii=False)

    @staticmethod
    def from_json(texto: str) -> Evento:
        dados = json.loads(texto)
        dados["occurred_at"] = datetime.fromisoformat(dados["occurred_at"])
        return Evento(**dados)


def _serializar(valor: Any) -> Any:
    # Decimal vira string, não float: no JSON do evento o valor é exibido, e
    # converter para float aqui reintroduziria o erro binário que o resto do
    # sistema evita usando Decimal.
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, datetime):
        return valor.isoformat()
    return str(valor)


def canal_do_tenant(tenant_id: int) -> str:
    return f"tenant:{tenant_id}:live"


class Barramento(Protocol):
    async def publicar(self, evento: Evento) -> None: ...
    def assinar(self, tenant_id: int) -> AsyncGenerator[Evento, None]: ...
    async def fechar(self) -> None: ...


class BarramentoMemoria:
    """Fila em processo. Só serve quando API e worker são o mesmo processo."""

    def __init__(self, tamanho_max: int = 500) -> None:
        self._assinantes: dict[int, list[asyncio.Queue[Evento]]] = defaultdict(list)
        self._tamanho_max = tamanho_max

    async def publicar(self, evento: Evento) -> None:
        for fila in list(self._assinantes.get(evento.tenant_id, [])):
            try:
                fila.put_nowait(evento)
            except asyncio.QueueFull:
                # Assinante lento não pode travar a publicação: descartamos o
                # evento para ele e seguimos. O painel se recupera na próxima
                # revalidação do TanStack Query.
                log.warning("assinante_lento_evento_descartado", tenant=evento.tenant_id)

    async def assinar(self, tenant_id: int) -> AsyncGenerator[Evento, None]:
        fila: asyncio.Queue[Evento] = asyncio.Queue(maxsize=self._tamanho_max)
        self._assinantes[tenant_id].append(fila)
        try:
            while True:
                yield await fila.get()
        finally:
            self._assinantes[tenant_id].remove(fila)

    async def fechar(self) -> None:
        self._assinantes.clear()


class BarramentoRedis:
    """Pub/sub do Redis: entrega o evento a todas as réplicas da API."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._publicador: Any = None

    async def _obter_publicador(self) -> Any:
        if self._publicador is None:
            import redis.asyncio as redis

            self._publicador = redis.from_url(self._url, decode_responses=True)
        return self._publicador

    async def publicar(self, evento: Evento) -> None:
        try:
            cliente = await self._obter_publicador()
            await cliente.publish(canal_do_tenant(evento.tenant_id), evento.to_json())
        except Exception as exc:
            # Falha no barramento não pode derrubar a ingestão: o dado já está
            # persistido, e o painel se atualiza na próxima revalidação.
            log.warning("falha_ao_publicar_evento", erro=str(exc), tipo=evento.type)

    async def assinar(self, tenant_id: int) -> AsyncGenerator[Evento, None]:
        import redis.asyncio as redis

        cliente = redis.from_url(self._url, decode_responses=True)
        pubsub = cliente.pubsub()
        await pubsub.subscribe(canal_do_tenant(tenant_id))
        try:
            async for mensagem in pubsub.listen():
                if mensagem.get("type") != "message":
                    continue
                try:
                    yield Evento.from_json(mensagem["data"])
                except (ValueError, KeyError, TypeError) as exc:
                    log.warning("evento_malformado_no_barramento", erro=str(exc))
        finally:
            await pubsub.unsubscribe(canal_do_tenant(tenant_id))
            await pubsub.aclose()
            await cliente.aclose()

    async def fechar(self) -> None:
        if self._publicador is not None:
            await self._publicador.aclose()
            self._publicador = None


_barramento: Barramento | None = None


def obter_barramento() -> Barramento:
    """Devolve o barramento configurado (Redis quando há URL, memória senão)."""
    global _barramento
    if _barramento is None:
        _barramento = (
            BarramentoRedis(settings.REDIS_URL) if settings.REDIS_URL else BarramentoMemoria()
        )
        log.info(
            "barramento_inicializado",
            tipo="redis" if settings.REDIS_URL else "memoria",
        )
    return _barramento


def definir_barramento(barramento: Barramento | None) -> None:
    """Injeta um barramento (usado em testes)."""
    global _barramento
    _barramento = barramento


async def publicar(
    tipo: str,
    tenant_id: int,
    payload: dict[str, Any],
    *,
    channel: str = "",
    account_id: int | None = None,
) -> None:
    """Atalho de publicação usado pelos serviços."""
    await obter_barramento().publicar(
        Evento(
            type=tipo,
            tenant_id=tenant_id,
            payload=payload,
            channel=channel,
            account_id=account_id,
        )
    )
