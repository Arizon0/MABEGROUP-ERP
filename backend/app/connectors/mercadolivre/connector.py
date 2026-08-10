"""Conector do Mercado Livre: OAuth 2.0 + PKCE, cliente e sincronização."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import structlog

from app.connectors.base import (
    AccountInfo,
    CanonicalClaim,
    CanonicalListing,
    CanonicalOrder,
    CanonicalPayment,
    CanonicalQuestion,
    CanonicalShipment,
    TokenBundle,
    WebhookNotification,
)
from app.connectors.http import ClienteMarketplace
from app.connectors.mercadolivre import normalizer as norm
from app.core.config import settings
from app.core.errors import ErroIntegracao
from app.models.enums import Canal

log = structlog.get_logger(__name__)

#: Multiget aceita até 20 IDs. Usar o limite reduz em 20× o consumo de cota —
#: é a diferença entre um backfill que cabe no rate limit e um que não cabe.
LOTE_MULTIGET = 20

#: A busca de pedidos tem offset máximo de 1.000 e janela útil limitada. Varrer
#: por janelas de data com ordenação ascendente é a única forma de garantir que
#: nada é perdido em silêncio.
JANELA_DIAS = 30


class ConectorMercadoLivre:
    """Implementa o protocolo :class:`~app.connectors.base.Connector`."""

    channel = Canal.MERCADO_LIVRE
    API_VERSION = "2026-01"

    def __init__(self, cliente: ClienteMarketplace | None = None) -> None:
        self._cliente = cliente

    def _api(self, chave_limite: str | None = None) -> ClienteMarketplace:
        return self._cliente or ClienteMarketplace(
            self.channel, base_url=settings.ML_API_BASE, chave_limite=chave_limite
        )

    # --- OAuth --------------------------------------------------------------

    async def build_authorization_url(self, state: str, code_verifier: str | None = None) -> str:
        """URL de autorização com PKCE (``code_challenge_method=S256``)."""
        from app.core.security import code_challenge_de

        params = {
            "response_type": "code",
            "client_id": settings.ML_CLIENT_ID,
            "redirect_uri": settings.ML_REDIRECT_URI,
            "state": state,
        }
        if code_verifier:
            params["code_challenge"] = code_challenge_de(code_verifier)
            params["code_challenge_method"] = "S256"
        return f"{settings.ML_AUTH_BASE}/authorization?{urlencode(params)}"

    async def exchange_code(
        self, code: str, code_verifier: str | None = None, **_: Any
    ) -> TokenBundle:
        """Troca o ``code`` da autorização pelo par de tokens."""
        corpo = {
            "grant_type": "authorization_code",
            "client_id": settings.ML_CLIENT_ID,
            "client_secret": settings.ML_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.ML_REDIRECT_URI,
        }
        if code_verifier:
            corpo["code_verifier"] = code_verifier
        return self._para_tokens(await self._api().post("/oauth/token", data=corpo))

    async def refresh(self, refresh_token: str, **_: Any) -> TokenBundle:
        """Renova o access token.

        ⚠️ O refresh token do Mercado Livre é de **uso único**: esta chamada
        invalida o anterior e emite um novo. Duas renovações concorrentes
        desconectam a conta e exigem reautorização manual do vendedor — por isso
        ``services/tokens.py`` serializa a operação com lock distribuído.
        """
        return self._para_tokens(
            await self._api().post(
                "/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.ML_CLIENT_ID,
                    "client_secret": settings.ML_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                },
            )
        )

    @staticmethod
    def _para_tokens(dados: dict[str, Any]) -> TokenBundle:
        agora = datetime.now(UTC)
        return TokenBundle(
            access_token=str(dados.get("access_token") or ""),
            refresh_token=dados.get("refresh_token"),
            expires_at=agora + timedelta(seconds=int(dados.get("expires_in") or 21600)),
            # O refresh token do ML vale 6 meses.
            refresh_expires_at=agora + timedelta(days=180),
            scopes=str(dados.get("scope") or "").split(),
            external_account_id=str(dados.get("user_id") or ""),
        )

    async def fetch_account_info(self, token: str, **_: Any) -> AccountInfo:
        dados = await self._api().get("/users/me", token=token)
        return AccountInfo(
            external_account_id=str(dados.get("id") or ""),
            nickname=str(dados.get("nickname") or ""),
            site_id=str(dados.get("site_id") or "MLB"),
            metadata={
                "user_type": dados.get("user_type"),
                "reputation": dados.get("seller_reputation"),
            },
        )

    # --- Pedidos ------------------------------------------------------------
    # Todas as assinaturas de busca aceitam argumentos extras: o serviço de
    # sincronização é genérico e passa `seller_id` e `shop_id` para qualquer
    # canal, sem saber qual dos dois aquele marketplace usa. Sem o `**_`, a
    # chamada quebraria com TypeError — e só com credenciais reais, porque os
    # conectores simulados já aceitavam tudo.


    async def fetch_orders(
        self,
        token: str,
        *,
        since: datetime,
        until: datetime,
        seller_id: str = "",
        **_: Any,
    ) -> list[CanonicalOrder]:
        """Busca pedidos por janelas de data, contornando o limite de offset.

        Percorre o intervalo em fatias de :data:`JANELA_DIAS`, cada uma paginada
        até o teto seguro de offset. Um laço ingênuo de offset pararia em 1.000
        pedidos sem erro nenhum — e a ausência dos demais passaria despercebida.
        """
        api = self._api(chave_limite=f"ml:{seller_id}")
        pedidos: list[CanonicalOrder] = []
        inicio = since

        while inicio < until:
            fim = min(inicio + timedelta(days=JANELA_DIAS), until)
            offset = 0
            while True:
                resposta = await api.get(
                    "/orders/search",
                    token=token,
                    params={
                        "seller": seller_id,
                        "order.date_created.from": _iso(inicio),
                        "order.date_created.to": _iso(fim),
                        "sort": "date_asc",
                        "offset": offset,
                        "limit": 50,
                    },
                )
                resultados = (resposta or {}).get("results") or []
                pedidos.extend(norm.normalizar_pedido(p) for p in resultados)
                total = int(((resposta or {}).get("paging") or {}).get("total") or 0)
                offset += 50
                if offset >= min(total, 1000) or not resultados:
                    break
            inicio = fim

        return pedidos

    async def fetch_order(self, token: str, external_id: str, **_: Any) -> CanonicalOrder | None:
        dados = await self._api().get(f"/orders/{external_id}", token=token)
        return norm.normalizar_pedido(dados) if dados else None

    async def fetch_shipment(
        self, token: str, external_id: str, **_: Any
    ) -> CanonicalShipment | None:
        """Busca o envio junto dos custos reais.

        A chamada a ``/costs`` é separada e obrigatória: sem ela não há como
        saber quanto o vendedor pagou de frete, e o líquido fica superestimado.
        """
        api = self._api()
        dados = await api.get(f"/shipments/{external_id}", token=token)
        if not dados:
            return None
        try:
            custos = await api.get(f"/shipments/{external_id}/costs", token=token)
        except ErroIntegracao:
            # Envios muito antigos ou de tipos específicos não expõem custos.
            # Perder o custo é ruim, perder o envio inteiro é pior.
            custos = None
            log.debug("custos_envio_indisponiveis", envio=external_id)
        return norm.normalizar_envio(dados, custos)

    async def fetch_payment(
        self, token: str, external_id: str, **_: Any
    ) -> CanonicalPayment | None:
        dados = await self._api().get(f"/v1/payments/{external_id}", token=token)
        return norm.normalizar_pagamento(dados) if dados else None

    # --- Catálogo -----------------------------------------------------------

    async def fetch_listings(
        self, token: str, seller_id: str = "", **_: Any
    ) -> list[CanonicalListing]:
        """Lista os anúncios do vendedor usando ``scroll`` e multiget."""
        api = self._api(chave_limite=f"ml:{seller_id}")
        ids: list[str] = []
        scroll: str | None = None

        while True:
            params: dict[str, Any] = {"search_type": "scan", "limit": 100}
            if scroll:
                params["scroll_id"] = scroll
            resposta = await api.get(f"/users/{seller_id}/items/search", token=token, params=params)
            lote = (resposta or {}).get("results") or []
            ids.extend(str(i) for i in lote)
            scroll = (resposta or {}).get("scroll_id")
            if not lote or not scroll:
                break

        anuncios: list[CanonicalListing] = []
        for i in range(0, len(ids), LOTE_MULTIGET):
            grupo = ids[i : i + LOTE_MULTIGET]
            resposta = await api.get("/items", token=token, params={"ids": ",".join(grupo)})
            for entrada in resposta or []:
                corpo = entrada.get("body") if isinstance(entrada, dict) else None
                if corpo:
                    anuncios.append(norm.normalizar_anuncio(corpo))
        return anuncios

    async def fetch_questions(
        self, token: str, seller_id: str = "", **_: Any
    ) -> list[CanonicalQuestion]:
        resposta = await self._api().get(
            "/questions/search",
            token=token,
            params={"seller_id": seller_id, "sort_fields": "date_created", "sort_types": "DESC"},
        )
        return [norm.normalizar_pergunta(q) for q in (resposta or {}).get("questions") or []]

    async def fetch_claims(self, token: str, **_: Any) -> list[CanonicalClaim]:
        resposta = await self._api().get("/post-purchase/v1/claims/search", token=token)
        return [norm.normalizar_reclamacao(c) for c in (resposta or {}).get("data") or []]

    async def fetch_seller_reputation(
        self, token: str, seller_id: str = "", **_: Any
    ) -> dict[str, Any]:
        """Reputação atual — fotografada diariamente em ``metrics_snapshots``.

        A API só devolve o estado de agora; o histórico não existe do lado deles.
        Sem a fotografia diária, o gráfico de evolução é impossível de construir.
        """
        dados = await self._api().get(f"/users/{seller_id}", token=token)
        return (dados or {}).get("seller_reputation") or {}

    # --- Webhooks -----------------------------------------------------------

    def parse_webhook(self, body: dict[str, Any], _headers: dict[str, str]) -> WebhookNotification:
        """Normaliza a notificação.

        O payload do ML é magro de propósito: ``{topic, resource, user_id}``. É um
        "vá buscar", não um "aqui está" — o detalhe vem de uma chamada posterior.
        """
        recurso = str(body.get("resource") or "")
        return WebhookNotification(
            channel=self.channel,
            topic=str(body.get("topic") or ""),
            resource=recurso,
            external_account_id=str(body.get("user_id") or ""),
            external_event_id=str(body.get("_id") or body.get("id") or ""),
            raw=body,
        )

    def verify_signature(self, body: bytes, headers: dict[str, str], url: str = "") -> bool:
        """Valida a origem da notificação.

        O Mercado Livre não assina as notificações com HMAC como o Mercado Pago.
        A defesa oficial é a URL secreta somada à allowlist de IP na borda. Aqui
        aceitamos um segredo compartilhado opcional no cabeçalho; quando não há
        segredo configurado, o evento é aceito e a validação real fica a cargo da
        confirmação subsequente contra a API (que exige token válido) — nenhum
        dado entra no sistema sem essa segunda chamada autenticada.
        """
        segredo = settings.MP_WEBHOOK_SECRET
        if not segredo:
            return True
        recebido = headers.get("x-hub-signature") or headers.get("x-signature") or ""
        esperado = hashlib.sha256(segredo.encode() + body).hexdigest()
        import hmac as _hmac

        return _hmac.compare_digest(recebido, esperado)


def _iso(momento: datetime) -> str:
    """Formata a data no padrão aceito pelos filtros do ML."""
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
