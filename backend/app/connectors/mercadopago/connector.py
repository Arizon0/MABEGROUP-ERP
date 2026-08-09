"""Conector Mercado Pago: camada financeira e conciliação bancária.

Complementa o Mercado Livre com o que só o MP tem: o líquido oficial por
pagamento, a data de liberação do dinheiro e os relatórios de repasse — que são
o único lugar onde existe a resposta para "quanto realmente caiu na conta".
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import structlog

from app.connectors.base import (
    AccountInfo,
    CanonicalOrder,
    CanonicalPayment,
    CanonicalSettlement,
    TokenBundle,
    WebhookNotification,
)
from app.connectors.http import ClienteMarketplace
from app.connectors.mercadolivre import normalizer as ml_norm
from app.core.config import settings
from app.models.enums import Canal

log = structlog.get_logger(__name__)


class ConectorMercadoPago:
    """Implementa a parte do protocolo aplicável a um provedor de pagamento.

    Os métodos de catálogo e pedido não se aplicam e levantam
    ``NotImplementedError`` — a interface consulta a capacidade do conector antes
    de oferecer a funcionalidade, em vez de exibir erro ao usuário.
    """

    channel = Canal.MERCADO_PAGO
    API_VERSION = "v1"

    def __init__(self, cliente: ClienteMarketplace | None = None) -> None:
        self._cliente = cliente

    def _api(self, chave_limite: str | None = None) -> ClienteMarketplace:
        return self._cliente or ClienteMarketplace(
            self.channel, base_url=settings.MP_API_BASE, chave_limite=chave_limite
        )

    # --- OAuth --------------------------------------------------------------

    async def build_authorization_url(self, state: str, _code_verifier: str | None = None) -> str:
        params = {
            "client_id": settings.MP_CLIENT_ID,
            "response_type": "code",
            "platform_id": "mp",
            "state": state,
            "redirect_uri": settings.MP_REDIRECT_URI,
        }
        return f"https://auth.mercadopago.com/authorization?{urlencode(params)}"

    async def exchange_code(
        self, code: str, _code_verifier: str | None = None, **_: Any
    ) -> TokenBundle:
        dados = await self._api().post(
            "/oauth/token",
            json_body={
                "client_id": settings.MP_CLIENT_ID,
                "client_secret": settings.MP_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.MP_REDIRECT_URI,
            },
        )
        return self._para_tokens(dados)

    async def refresh(self, refresh_token: str, **_: Any) -> TokenBundle:
        dados = await self._api().post(
            "/oauth/token",
            json_body={
                "client_id": settings.MP_CLIENT_ID,
                "client_secret": settings.MP_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        return self._para_tokens(dados)

    @staticmethod
    def _para_tokens(dados: dict[str, Any]) -> TokenBundle:
        agora = datetime.now(UTC)
        segundos = int(dados.get("expires_in") or 15552000)  # 180 dias
        return TokenBundle(
            access_token=str(dados.get("access_token") or ""),
            refresh_token=dados.get("refresh_token"),
            expires_at=agora + timedelta(seconds=segundos),
            refresh_expires_at=agora + timedelta(seconds=segundos),
            external_account_id=str(dados.get("user_id") or ""),
            extra={"public_key": dados.get("public_key"), "live_mode": dados.get("live_mode")},
        )

    async def fetch_account_info(self, token: str, **_: Any) -> AccountInfo:
        dados = await self._api().get("/users/me", token=token)
        return AccountInfo(
            external_account_id=str(dados.get("id") or ""),
            nickname=str(dados.get("nickname") or ""),
            site_id=str(dados.get("site_id") or "MLB"),
        )

    # --- Pagamentos ---------------------------------------------------------

    async def fetch_payment(self, token: str, external_id: str) -> CanonicalPayment | None:
        """Busca um pagamento. O normalizador é compartilhado com o ML."""
        dados = await self._api().get(f"/v1/payments/{external_id}", token=token)
        if not dados:
            return None
        pagamento = ml_norm.normalizar_pagamento(dados)
        pagamento.channel = Canal.MERCADO_PAGO
        return pagamento

    async def fetch_payments(
        self, token: str, *, since: datetime, until: datetime
    ) -> list[CanonicalPayment]:
        """Busca pagamentos por período, paginando."""
        api = self._api()
        pagamentos: list[CanonicalPayment] = []
        offset = 0
        while True:
            resposta = await api.get(
                "/v1/payments/search",
                token=token,
                params={
                    "sort": "date_created",
                    "criteria": "asc",
                    "range": "date_created",
                    "begin_date": _iso(since),
                    "end_date": _iso(until),
                    "offset": offset,
                    "limit": 50,
                },
            )
            resultados = (resposta or {}).get("results") or []
            for bruto in resultados:
                pagamento = ml_norm.normalizar_pagamento(bruto)
                pagamento.channel = Canal.MERCADO_PAGO
                pagamentos.append(pagamento)
            total = int(((resposta or {}).get("paging") or {}).get("total") or 0)
            offset += 50
            if offset >= total or not resultados:
                break
        return pagamentos

    async def fetch_refunds(self, token: str, payment_id: str) -> list[dict[str, Any]]:
        return await self._api().get(f"/v1/payments/{payment_id}/refunds", token=token) or []

    async def fetch_balance(self, token: str) -> dict[str, Any]:
        """Saldo disponível e a liberar — alimenta o cartão de caixa do painel."""
        return await self._api().get("/v1/account/balance", token=token) or {}

    # --- Repasses -----------------------------------------------------------

    async def solicitar_relatorio_liberacoes(
        self, token: str, *, since: datetime, until: datetime
    ) -> str:
        """Solicita o relatório de liberações (assíncrono).

        Não existe versão síncrona: o fluxo é solicitar → aguardar → baixar.
        Devolve o identificador do arquivo, que ``services/sync.py`` acompanha
        como job de duas fases com estado persistido.
        """
        resposta = await self._api().post(
            "/v1/account/release_report",
            token=token,
            json_body={"begin_date": _iso(since), "end_date": _iso(until)},
        )
        return str((resposta or {}).get("file_name") or (resposta or {}).get("id") or "")

    async def listar_relatorios(self, token: str) -> list[dict[str, Any]]:
        return await self._api().get("/v1/account/release_report/list", token=token) or []

    async def baixar_relatorio(self, token: str, nome_arquivo: str) -> str:
        """Baixa o CSV do relatório já processado."""
        return await self._api().get(
            f"/v1/account/release_report/{nome_arquivo}", token=token
        )

    @staticmethod
    def parse_relatorio_liberacoes(csv_texto: str) -> list[CanonicalSettlement]:
        """Converte o CSV de liberações em repasses canônicos.

        Agrupa por data de liberação: o extrato bancário mostra um crédito por
        dia, não um por pedido, e é nesse nível que a conciliação precisa fechar.
        """
        import csv
        import io
        from collections import defaultdict
        from decimal import Decimal

        por_dia: dict[str, list[dict[str, Any]]] = defaultdict(list)
        leitor = csv.DictReader(io.StringIO(csv_texto))
        for linha in leitor:
            dia = (linha.get("DATE") or linha.get("date") or "")[:10]
            if dia:
                por_dia[dia].append(linha)

        repasses: list[CanonicalSettlement] = []
        for dia, linhas in sorted(por_dia.items()):
            bruto = sum(
                (ml_norm.dec(l.get("GROSS_AMOUNT") or l.get("TRANSACTION_AMOUNT")) for l in linhas),
                Decimal("0"),
            )
            taxas = sum(
                (ml_norm.dec(l.get("MP_FEE_AMOUNT") or l.get("FEE_AMOUNT")) for l in linhas),
                Decimal("0"),
            )
            liquido = sum(
                (ml_norm.dec(l.get("NET_CREDIT_AMOUNT") or l.get("NET_AMOUNT")) for l in linhas),
                Decimal("0"),
            )
            repasses.append(
                CanonicalSettlement(
                    external_id=f"mp-release-{dia}",
                    channel=Canal.MERCADO_PAGO,
                    settlement_date=datetime.fromisoformat(dia).replace(tzinfo=UTC),
                    gross_amount=bruto,
                    fee_amount=taxas,
                    net_amount=liquido,
                    source="mp_release_report",
                    entries=[
                        {
                            "external_payment_id": str(
                                l.get("SOURCE_ID") or l.get("PAYMENT_ID") or ""
                            ),
                            "amount": str(
                                ml_norm.dec(l.get("NET_CREDIT_AMOUNT") or l.get("NET_AMOUNT"))
                            ),
                            "description": str(l.get("DESCRIPTION") or l.get("RECORD_TYPE") or ""),
                        }
                        for l in linhas
                    ],
                )
            )
        return repasses

    # --- Webhooks -----------------------------------------------------------

    def parse_webhook(self, body: dict[str, Any], _headers: dict[str, str]) -> WebhookNotification:
        dados = body.get("data") or {}
        return WebhookNotification(
            channel=self.channel,
            topic=str(body.get("type") or body.get("topic") or ""),
            resource=str(dados.get("id") or body.get("resource") or ""),
            external_account_id=str(body.get("user_id") or ""),
            external_event_id=str(body.get("id") or ""),
            raw=body,
        )

    def verify_signature(self, body: bytes, headers: dict[str, str], url: str = "") -> bool:
        """Valida o header ``x-signature`` do Mercado Pago.

        Formato: ``ts=<epoch>,v1=<hmac>``, sobre o manifesto
        ``id:{data.id};request-id:{x-request-id};ts:{ts};``.

        Sem esta validação, qualquer um que descubra a URL do webhook injeta
        pagamentos falsos direto no banco.
        """
        if not settings.MP_WEBHOOK_SECRET:
            return True

        assinatura = headers.get("x-signature") or headers.get("X-Signature") or ""
        partes = dict(
            p.strip().split("=", 1) for p in assinatura.split(",") if "=" in p
        )
        ts, v1 = partes.get("ts", ""), partes.get("v1", "")
        if not ts or not v1:
            return False

        import json

        try:
            corpo = json.loads(body or b"{}")
        except ValueError:
            return False

        data_id = str((corpo.get("data") or {}).get("id") or "")
        request_id = headers.get("x-request-id") or headers.get("X-Request-Id") or ""
        manifesto = f"id:{data_id};request-id:{request_id};ts:{ts};"
        esperado = hmac.new(
            settings.MP_WEBHOOK_SECRET.encode(), manifesto.encode(), hashlib.sha256
        ).hexdigest()
        # Comparação em tempo constante: `==` vazaria informação por timing.
        return hmac.compare_digest(esperado, v1)

    # --- Não aplicável ------------------------------------------------------

    async def fetch_orders(self, *_: Any, **__: Any) -> list[CanonicalOrder]:
        raise NotImplementedError(
            "O Mercado Pago é um provedor de pagamento: pedidos vêm do "
            "Mercado Livre ou da loja própria do vendedor."
        )

    async def fetch_order(self, *_: Any, **__: Any) -> CanonicalOrder | None:
        raise NotImplementedError


def _iso(momento: datetime) -> str:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
