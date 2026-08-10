"""Conector Shopee Open Platform v2: autorização por loja e assinatura HMAC.

A Shopee não usa OAuth padrão — cada requisição é assinada individualmente com
HMAC-SHA256 sobre uma string base de ordem fixa. Três detalhes derrubam a
integração se ignorados, e todos estão tratados aqui: a ordem de concatenação,
o timestamp em segundos com tolerância de ±5 min, e o fato de endpoints públicos
assinarem sem ``access_token``/``shop_id``.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import structlog

from app.connectors.base import (
    AccountInfo,
    CanonicalCampaign,
    CanonicalListing,
    CanonicalOrder,
    CanonicalPayment,
    CanonicalShipment,
    TokenBundle,
    WebhookNotification,
)
from app.connectors.http import ClienteMarketplace
from app.connectors.shopee import normalizer as norm
from app.core.config import settings
from app.core.errors import ErroIntegracao
from app.models.enums import Canal

log = structlog.get_logger(__name__)

#: Limite rígido da API: cada consulta de pedidos cobre no máximo 15 dias.
#: Um backfill de 2 anos são ~49 janelas sequenciais por loja.
JANELA_DIAS = 15

#: ``get_order_detail`` aceita até 50 pedidos por chamada.
LOTE_PEDIDOS = 50

#: Sem pedir explicitamente, o payload volta sem itens e sem endereço.
CAMPOS_PEDIDO = (
    "buyer_user_id,buyer_username,estimated_shipping_fee,item_list,"
    "recipient_address,payment_method,total_amount,shipping_carrier,"
    "package_list,cancel_reason,actual_shipping_fee,note"
)


class ConectorShopee:
    """Implementa o protocolo :class:`~app.connectors.base.Connector`."""

    channel = Canal.SHOPEE
    API_VERSION = "v2"

    def __init__(self, cliente: ClienteMarketplace | None = None) -> None:
        self._cliente = cliente

    def _api(self, chave_limite: str | None = None) -> ClienteMarketplace:
        return self._cliente or ClienteMarketplace(
            self.channel, base_url=settings.SHOPEE_API_BASE, chave_limite=chave_limite
        )

    # --- Assinatura ---------------------------------------------------------

    @staticmethod
    def assinar(caminho: str, timestamp: int, token: str = "", shop_id: str = "") -> str:
        """HMAC-SHA256 sobre ``partner_id + path + timestamp + token + shop_id``.

        A ordem é fixa e **não** é alfabética. Endpoints públicos omitem os dois
        últimos componentes.
        """
        base = f"{settings.SHOPEE_PARTNER_ID}{caminho}{timestamp}{token}{shop_id}"
        return hmac.new(
            settings.SHOPEE_PARTNER_KEY.encode(), base.encode(), hashlib.sha256
        ).hexdigest()

    def _params_assinados(
        self, caminho: str, token: str = "", shop_id: str = "", extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        agora = int(time.time())
        params: dict[str, Any] = {
            "partner_id": int(settings.SHOPEE_PARTNER_ID or 0),
            "timestamp": agora,
            "sign": self.assinar(caminho, agora, token, shop_id),
        }
        if token:
            params["access_token"] = token
        if shop_id:
            params["shop_id"] = int(shop_id)
        params.update(extra or {})
        return params

    async def _chamar(
        self,
        caminho: str,
        *,
        token: str = "",
        shop_id: str = "",
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        metodo: str = "GET",
    ) -> dict[str, Any]:
        """Executa a chamada assinada e trata o erro no corpo.

        A Shopee responde HTTP 200 mesmo em erro de negócio, sinalizando pelo
        campo ``error`` do corpo. Tratar só o status HTTP deixaria a falha passar
        silenciosamente como sucesso.
        """
        query = self._params_assinados(caminho, token, shop_id, params)
        resposta = await self._api(chave_limite=f"shopee:{shop_id or 'public'}").requisitar(
            metodo, caminho, params=query, json_body=json_body
        )
        resposta = resposta or {}
        if resposta.get("error"):
            raise ErroIntegracao(
                f"Shopee retornou erro: {resposta.get('error')} — {resposta.get('message', '')}",
                canal=self.channel,
                detalhes={"request_id": resposta.get("request_id")},
            )
        return resposta

    # --- Autorização --------------------------------------------------------

    async def build_authorization_url(self, state: str, _code_verifier: str | None = None) -> str:
        caminho = "/api/v2/shop/auth_partner"
        agora = int(time.time())
        params = {
            "partner_id": int(settings.SHOPEE_PARTNER_ID or 0),
            "timestamp": agora,
            "sign": self.assinar(caminho, agora),
            # A Shopee não tem parâmetro ``state``; carregamos no redirect para
            # preservar a proteção anti-CSRF do nosso lado.
            "redirect": f"{settings.SHOPEE_REDIRECT_URI}?state={state}",
        }
        return f"{settings.SHOPEE_API_BASE}{caminho}?{urlencode(params)}"

    async def exchange_code(
        self, code: str, _code_verifier: str | None = None, *, shop_id: str = "", **_: Any
    ) -> TokenBundle:
        dados = await self._chamar(
            "/api/v2/auth/token/get",
            metodo="POST",
            json_body={
                "code": code,
                "shop_id": int(shop_id) if shop_id else None,
                "partner_id": int(settings.SHOPEE_PARTNER_ID or 0),
            },
        )
        return self._para_tokens(dados, shop_id)

    async def refresh(self, refresh_token: str, *, shop_id: str = "", **_: Any) -> TokenBundle:
        """Renova o token.

        O ciclo da Shopee é mais curto que o do ML: o access token vale 4 horas e
        o refresh token, 30 dias — e também rotaciona a cada uso.
        """
        dados = await self._chamar(
            "/api/v2/auth/access_token/get",
            metodo="POST",
            json_body={
                "refresh_token": refresh_token,
                "shop_id": int(shop_id) if shop_id else None,
                "partner_id": int(settings.SHOPEE_PARTNER_ID or 0),
            },
        )
        return self._para_tokens(dados, shop_id)

    @staticmethod
    def _para_tokens(dados: dict[str, Any], shop_id: str) -> TokenBundle:
        agora = datetime.now(UTC)
        return TokenBundle(
            access_token=str(dados.get("access_token") or ""),
            refresh_token=str(dados.get("refresh_token") or ""),
            expires_at=agora + timedelta(seconds=int(dados.get("expire_in") or 14400)),
            refresh_expires_at=agora + timedelta(days=30),
            external_account_id=str(dados.get("shop_id") or shop_id),
        )

    async def fetch_account_info(self, token: str, *, shop_id: str = "", **_: Any) -> AccountInfo:
        dados = await self._chamar("/api/v2/shop/get_shop_info", token=token, shop_id=shop_id)
        return AccountInfo(
            external_account_id=str(shop_id),
            nickname=str(dados.get("shop_name") or ""),
            site_id=str(dados.get("region") or "BR"),
            metadata={"status": dados.get("status"), "is_cb": dados.get("is_cb")},
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
        shop_id: str = "",
        **_: Any,
    ) -> list[CanonicalOrder]:
        """Busca pedidos respeitando a janela de 15 dias e o lote de 50 detalhes."""
        numeros: list[str] = []
        inicio = since

        while inicio < until:
            fim = min(inicio + timedelta(days=JANELA_DIAS), until)
            cursor = ""
            while True:
                resposta = await self._chamar(
                    "/api/v2/order/get_order_list",
                    token=token,
                    shop_id=shop_id,
                    params={
                        "time_range_field": "update_time",
                        "time_from": int(inicio.timestamp()),
                        "time_to": int(fim.timestamp()),
                        "page_size": 100,
                        "cursor": cursor,
                    },
                )
                bloco = resposta.get("response") or {}
                numeros.extend(
                    str(p.get("order_sn")) for p in bloco.get("order_list") or [] if p.get("order_sn")
                )
                if not bloco.get("more"):
                    break
                cursor = str(bloco.get("next_cursor") or "")
                if not cursor:
                    break
            inicio = fim

        return await self._detalhar_pedidos(token, shop_id, numeros)

    async def _detalhar_pedidos(
        self, token: str, shop_id: str, numeros: list[str]
    ) -> list[CanonicalOrder]:
        pedidos: list[CanonicalOrder] = []
        for i in range(0, len(numeros), LOTE_PEDIDOS):
            grupo = numeros[i : i + LOTE_PEDIDOS]
            resposta = await self._chamar(
                "/api/v2/order/get_order_detail",
                token=token,
                shop_id=shop_id,
                params={"order_sn_list": ",".join(grupo), "response_optional_fields": CAMPOS_PEDIDO},
            )
            for bruto in (resposta.get("response") or {}).get("order_list") or []:
                pedido = norm.normalizar_pedido(bruto)
                # Sem escrow ainda: entra com estimativa declarada.
                pedidos.append(norm.estimar_liquido(pedido))
        return pedidos

    async def fetch_order(
        self, token: str, external_id: str, *, shop_id: str = "", **_: Any
    ) -> CanonicalOrder | None:
        pedidos = await self._detalhar_pedidos(token, shop_id, [external_id])
        return pedidos[0] if pedidos else None

    async def fetch_escrow(
        self, token: str, order_sn: str, *, shop_id: str = "", **_: Any
    ) -> CanonicalPayment | None:
        """Busca o detalhe financeiro definitivo de um pedido concluído."""
        resposta = await self._chamar(
            "/api/v2/payment/get_escrow_detail",
            token=token,
            shop_id=shop_id,
            params={"order_sn": order_sn},
        )
        bloco = resposta.get("response") or {}
        return norm.normalizar_escrow(bloco, order_sn) if bloco else None

    async def fetch_shipment(
        self, token: str, order_sn: str, *, shop_id: str = "", **_: Any
    ) -> CanonicalShipment | None:
        detalhe = await self._chamar(
            "/api/v2/order/get_order_detail",
            token=token,
            shop_id=shop_id,
            params={"order_sn_list": order_sn, "response_optional_fields": CAMPOS_PEDIDO},
        )
        lista = (detalhe.get("response") or {}).get("order_list") or []
        if not lista:
            return None
        try:
            rastreio = await self._chamar(
                "/api/v2/logistics/get_tracking_info",
                token=token,
                shop_id=shop_id,
                params={"order_sn": order_sn},
            )
            rastreio = rastreio.get("response") or {}
        except ErroIntegracao:
            rastreio = {}
        return norm.normalizar_envio(lista[0], rastreio)

    # --- Catálogo -----------------------------------------------------------

    async def fetch_listings(
        self, token: str, *, shop_id: str = "", **_: Any
    ) -> list[CanonicalListing]:
        ids: list[int] = []
        offset = 0
        while True:
            resposta = await self._chamar(
                "/api/v2/product/get_item_list",
                token=token,
                shop_id=shop_id,
                params={"offset": offset, "page_size": 100, "item_status": "NORMAL"},
            )
            bloco = resposta.get("response") or {}
            lote = bloco.get("item") or []
            ids.extend(int(i["item_id"]) for i in lote if i.get("item_id"))
            if not bloco.get("has_next_page"):
                break
            offset = int(bloco.get("next_offset") or offset + 100)

        anuncios: list[CanonicalListing] = []
        for i in range(0, len(ids), 50):
            grupo = ids[i : i + 50]
            base = await self._chamar(
                "/api/v2/product/get_item_base_info",
                token=token,
                shop_id=shop_id,
                params={"item_id_list": ",".join(map(str, grupo))},
            )
            for item in (base.get("response") or {}).get("item_list") or []:
                modelos: list[dict[str, Any]] = []
                try:
                    resp_modelos = await self._chamar(
                        "/api/v2/product/get_model_list",
                        token=token,
                        shop_id=shop_id,
                        params={"item_id": item.get("item_id")},
                    )
                    modelos = (resp_modelos.get("response") or {}).get("model") or []
                except ErroIntegracao:
                    pass  # anúncio sem variação
                anuncios.append(norm.normalizar_anuncio(item, modelos))
        return anuncios

    async def fetch_campaigns(
        self, token: str, *, shop_id: str = "", **_: Any
    ) -> list[CanonicalCampaign]:
        """Promoções ativas.

        Não cobre anúncios pagos: a Ads API da Shopee exige whitelist separada
        (ver ``docs/10-riscos-limitacoes.md``). O custo de mídia entra por
        lançamento manual em ``campaigns.manual_media_cost``.
        """
        resposta = await self._chamar(
            "/api/v2/discount/get_discount_list",
            token=token,
            shop_id=shop_id,
            params={"discount_status": "ongoing", "page_size": 100},
        )
        return [
            norm.normalizar_campanha(d, "discount")
            for d in (resposta.get("response") or {}).get("discount_list") or []
        ]

    # --- Webhooks -----------------------------------------------------------

    def parse_webhook(self, body: dict[str, Any], _headers: dict[str, str]) -> WebhookNotification:
        """Normaliza o push da Shopee (identificado por ``code`` numérico)."""
        codigo = str(body.get("code") or "")
        dados = body.get("data") or {}
        mapa = {
            "1": "shop_authorization",
            "2": "shop_deauthorization",
            "3": "order_status",
            "4": "tracking_number",
            "5": "shop_info",
            "6": "banned_item",
            "9": "promotion",
            "10": "webchat",
            "15": "tracking_update",
        }
        return WebhookNotification(
            channel=self.channel,
            topic=mapa.get(codigo, f"code_{codigo}"),
            resource=str(dados.get("ordersn") or dados.get("order_sn") or ""),
            external_account_id=str(body.get("shop_id") or ""),
            external_event_id=f"{codigo}:{dados.get('ordersn', '')}:{body.get('timestamp', '')}",
            raw=body,
        )

    def verify_signature(self, body: bytes, headers: dict[str, str], url: str = "") -> bool:
        """Valida o push: ``HMAC-SHA256(partner_key, "{url}|{corpo_cru}")``.

        ⚠️ O corpo precisa ser os bytes exatos recebidos. JSON reserializado muda
        espaçamento e ordem de chaves, e a assinatura nunca confere.
        """
        if not settings.SHOPEE_PARTNER_KEY:
            return True
        recebido = headers.get("authorization") or headers.get("Authorization") or ""
        base = f"{url}|{body.decode('utf-8', errors='replace')}"
        esperado = hmac.new(
            settings.SHOPEE_PARTNER_KEY.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(recebido.strip(), esperado)
