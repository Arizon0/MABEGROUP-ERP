"""Cliente HTTP resiliente para as APIs de marketplace.

Concentra aqui — uma única vez — tudo que separa um script de integração de uma
integração de produção: retentativa com backoff e jitter, respeito a
``Retry-After``, limitador de taxa por conta, disjuntor e classificação de erro.

Sem esse cuidado, uma instabilidade momentânea do marketplace vira uma tempestade
de retentativas que estoura o rate limit e deixa a conta bloqueada por horas.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.errors import CredencialInvalida, ErroIntegracao, LimiteDeTaxa

log = structlog.get_logger(__name__)

#: Códigos que valem uma nova tentativa. 4xx (fora 408/429) é erro do nosso lado
#: — repetir só desperdiça cota e atrasa a descoberta do bug.
STATUS_RETENTAVEIS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class TokenBucket:
    """Limitador de taxa por conta.

    Cada conta tem seu próprio balde: um vendedor rodando backfill de 24 meses
    não consome a cota de outro. Isolar o ruído entre tenants é o que evita que
    um onboarding pesado degrade a operação de todo mundo.
    """

    capacidade: int
    por_minuto: int
    tokens: float = field(init=False)
    ultimo: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacidade)
        self.ultimo = time.monotonic()

    async def adquirir(self, quantidade: int = 1) -> None:
        while True:
            agora = time.monotonic()
            self.tokens = min(
                self.capacidade, self.tokens + (agora - self.ultimo) * (self.por_minuto / 60.0)
            )
            self.ultimo = agora
            if self.tokens >= quantidade:
                self.tokens -= quantidade
                return
            faltam = quantidade - self.tokens
            await asyncio.sleep(max(0.05, faltam / (self.por_minuto / 60.0)))


@dataclass
class CircuitBreaker:
    """Disjuntor: para de bater numa API que já está claramente fora do ar.

    Sem ele, cada job continua tentando e cada tentativa espera o timeout, o que
    entope a fila e transforma uma indisponibilidade externa em indisponibilidade
    interna.
    """

    limite_falhas: int = 5
    espera_segundos: float = 60.0
    falhas: int = 0
    aberto_ate: float = 0.0

    @property
    def aberto(self) -> bool:
        return time.monotonic() < self.aberto_ate

    def registrar_sucesso(self) -> None:
        self.falhas = 0
        self.aberto_ate = 0.0

    def registrar_falha(self) -> None:
        self.falhas += 1
        if self.falhas >= self.limite_falhas:
            self.aberto_ate = time.monotonic() + self.espera_segundos


_baldes: dict[str, TokenBucket] = {}
_disjuntores: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)


def balde_de(chave: str) -> TokenBucket:
    if chave not in _baldes:
        _baldes[chave] = TokenBucket(
            capacidade=max(10, settings.RATE_LIMIT_PER_MINUTE // 4),
            por_minuto=settings.RATE_LIMIT_PER_MINUTE,
        )
    return _baldes[chave]


class ClienteMarketplace:
    """Cliente HTTP assíncrono com as políticas de resiliência aplicadas.

    Uso::

        async with ClienteMarketplace("mercadolivre", base_url=...) as cli:
            dados = await cli.get("/orders/123", token="...")
    """

    def __init__(
        self,
        canal: str,
        *,
        base_url: str,
        chave_limite: str | None = None,
        timeout: float | None = None,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        self.canal = canal
        self.base_url = base_url.rstrip("/")
        self.chave_limite = chave_limite or canal
        self._proprio = cliente is None
        self._cliente = cliente or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or settings.HTTP_TIMEOUT_SECONDS),
            follow_redirects=True,
            headers={"User-Agent": f"MarketplaceHub/1.0 (+{canal})"},
        )

    async def __aenter__(self) -> ClienteMarketplace:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.fechar()

    async def fechar(self) -> None:
        if self._proprio:
            await self._cliente.aclose()

    # -- API pública ---------------------------------------------------------

    async def get(self, caminho: str, **kw: Any) -> Any:
        return await self.requisitar("GET", caminho, **kw)

    async def post(self, caminho: str, **kw: Any) -> Any:
        return await self.requisitar("POST", caminho, **kw)

    async def put(self, caminho: str, **kw: Any) -> Any:
        return await self.requisitar("PUT", caminho, **kw)

    async def requisitar(
        self,
        metodo: str,
        caminho: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        max_tentativas: int | None = None,
    ) -> Any:
        url = caminho if caminho.startswith("http") else f"{self.base_url}{caminho}"
        cabecalhos = dict(headers or {})
        if token:
            cabecalhos.setdefault("Authorization", f"Bearer {token}")

        disjuntor = _disjuntores[self.chave_limite]
        if disjuntor.aberto:
            raise ErroIntegracao(
                f"Circuito aberto para {self.canal}: a API vem falhando de forma "
                f"consecutiva. Nova tentativa em instantes.",
                canal=self.canal,
            )

        tentativas = max_tentativas or settings.HTTP_MAX_RETRIES
        ultimo_erro: Exception | None = None

        for tentativa in range(1, tentativas + 1):
            await balde_de(self.chave_limite).adquirir()
            inicio = time.monotonic()
            try:
                resp = await self._cliente.request(
                    metodo, url, params=params, json=json_body, data=data, headers=cabecalhos
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                ultimo_erro = exc
                disjuntor.registrar_falha()
                if tentativa == tentativas:
                    break
                await asyncio.sleep(self._espera(tentativa))
                continue

            duracao = int((time.monotonic() - inicio) * 1000)
            log.debug(
                "chamada_externa",
                canal=self.canal,
                metodo=metodo,
                caminho=caminho,
                status=resp.status_code,
                ms=duracao,
                tentativa=tentativa,
            )

            if resp.status_code == 429:
                disjuntor.registrar_falha()
                espera = self._retry_after(resp) or self._espera(tentativa)
                if tentativa == tentativas:
                    raise LimiteDeTaxa(
                        f"Limite de requisições atingido em {self.canal}.",
                        canal=self.canal,
                        retry_after=espera,
                    )
                await asyncio.sleep(espera)
                continue

            if resp.status_code in (401, 403):
                disjuntor.registrar_sucesso()  # a API respondeu: não está fora do ar
                raise CredencialInvalida(
                    f"Credencial recusada por {self.canal} (HTTP {resp.status_code}). "
                    f"O token pode ter expirado, sido revogado ou não ter o escopo "
                    f"necessário para este recurso.",
                    canal=self.canal,
                    status_externo=resp.status_code,
                )

            if resp.status_code in STATUS_RETENTAVEIS:
                disjuntor.registrar_falha()
                ultimo_erro = ErroIntegracao(
                    f"{self.canal} respondeu HTTP {resp.status_code}.",
                    canal=self.canal,
                    status_externo=resp.status_code,
                )
                if tentativa == tentativas:
                    break
                await asyncio.sleep(self._espera(tentativa))
                continue

            if resp.status_code >= 400:
                disjuntor.registrar_sucesso()
                raise ErroIntegracao(
                    f"{self.canal} recusou a requisição (HTTP {resp.status_code}).",
                    canal=self.canal,
                    status_externo=resp.status_code,
                    detalhes={"corpo": resp.text[:800]},
                )

            disjuntor.registrar_sucesso()
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text

        raise ErroIntegracao(
            f"Falha ao chamar {self.canal} após {tentativas} tentativas: {ultimo_erro}",
            canal=self.canal,
        )

    # -- Auxiliares ----------------------------------------------------------

    @staticmethod
    def _espera(tentativa: int) -> float:
        """Backoff exponencial com jitter.

        O jitter não é detalhe: sem ele, todos os workers que falharam ao mesmo
        tempo voltam a tentar exatamente ao mesmo tempo, e o efeito manada
        derruba a API de novo no instante em que ela começa a se recuperar.
        """
        base = min(2 ** (tentativa - 1), 16)
        return base + random.uniform(0, base * 0.3)

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        valor = resp.headers.get("Retry-After")
        if not valor:
            return None
        try:
            return float(valor)
        except ValueError:
            return None


def resetar_estado_resiliencia() -> None:
    """Zera baldes e disjuntores. Usado entre testes."""
    _baldes.clear()
    _disjuntores.clear()
