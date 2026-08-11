"""Conectores simulados para desenvolvimento local, testes e demonstração.

Existem por uma razão prática: homologação da Shopee leva semanas, credenciais de
produção do Mercado Livre exigem uma conta de vendedor ativa, e nada disso pode
ser pré-requisito para alguém rodar o projeto pela primeira vez. Com
``USE_MOCK_CONNECTORS=1`` o sistema inteiro funciona ponta a ponta — painel ao
vivo, conciliação e relatórios — sem uma única chamada de rede.

Os dados gerados são deterministicamente aleatórios (semente fixa por conta), com
mix de produtos e faixa de preço compatíveis com a operação real de autopeças.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.connectors.base import (
    AccountInfo,
    CanonicalFee,
    CanonicalListing,
    CanonicalOrder,
    CanonicalOrderItem,
    CanonicalPayment,
    CanonicalQuestion,
    CanonicalShipment,
    CanonicalVariation,
    TokenBundle,
    WebhookNotification,
)
from app.models.enums import (
    Canal,
    CanalLogistico,
    FonteLiquido,
    StatusEnvio,
    StatusPagamento,
    StatusPedido,
    TipoTaxa,
)

# Catálogo espelhando a operação real: retentores, anéis, bronzinas e vedadores.
CATALOGO = [
    ("5338", "Retentor de Válvula Motor AP 1.6/1.8/2.0", Decimal("38.90"), Decimal("14.20")),
    ("8126", "Jogo de Anéis de Pistão 0.50mm Fire 1.0", Decimal("129.90"), Decimal("58.40")),
    ("5245", "Bronzina de Biela STD Motor Zetec 1.8", Decimal("94.50"), Decimal("41.30")),
    ("7712", "Vedador de Cabeçote Motor EA111 1.6", Decimal("67.80"), Decimal("28.90")),
    ("3390", "Retentor Dianteiro Virabrequim Corsa 1.0", Decimal("42.30"), Decimal("16.75")),
    ("9104", "Kit Junta Motor Completo Palio Fire", Decimal("218.00"), Decimal("104.60")),
    ("6621", "Bronzina de Mancal 0.25mm Gol 1.0 8V", Decimal("88.40"), Decimal("38.20")),
    ("4457", "Retentor Traseiro Câmbio HB20 1.0", Decimal("54.90"), Decimal("22.15")),
]

ESTADOS = ["SP", "MG", "RJ", "PR", "RS", "BA", "SC", "GO", "PE", "CE"]
CIDADES = {
    "SP": ["São Paulo", "Campinas", "Santos"], "MG": ["Belo Horizonte", "Uberlândia"],
    "RJ": ["Rio de Janeiro", "Niterói"], "PR": ["Curitiba", "Londrina"],
    "RS": ["Porto Alegre", "Caxias do Sul"], "BA": ["Salvador", "Feira de Santana"],
    "SC": ["Florianópolis", "Joinville"], "GO": ["Goiânia", "Anápolis"],
    "PE": ["Recife", "Olinda"], "CE": ["Fortaleza", "Caucaia"],
}


def _sku_do_canal(sku: str, canal: str, rnd: random.Random) -> str:
    """Devolve o código como o canal o expõe.

    Reproduz de propósito a bagunça real: o mesmo produto aparece como ``8126``,
    ``8126STD`` ou ``8126a`` no Mercado Livre e ``8126STA`` na Shopee. É
    exatamente isso que torna o de-para de SKU obrigatório para consolidar.
    """
    if canal == Canal.SHOPEE:
        return rnd.choice([sku, f"{sku}STA", f"{sku}-SP"])
    return rnd.choice([sku, f"{sku}STD", f"{sku}a"])


class ConectorMock:
    """Simula um marketplace com dados realistas e consistentes."""

    API_VERSION = "mock-1"

    def __init__(self, canal: str = Canal.MERCADO_LIVRE, semente: int = 42) -> None:
        self.channel = canal
        self._semente = semente
        # Registro dos pagamentos emitidos junto de cada pedido gerado. Sem ele,
        # `fetch_payment` devolveria um valor aleatório desvinculado do pedido —
        # e o painel exibiria líquido maior que o bruto, que é justamente o tipo
        # de número impossível que uma demonstração não pode mostrar.
        self._pagamentos: dict[str, CanonicalPayment] = {}

    def _rnd(self, sufixo: str = "") -> random.Random:
        return random.Random(f"{self.channel}:{self._semente}:{sufixo}")

    # --- Autorização --------------------------------------------------------

    async def build_authorization_url(self, state: str, _code_verifier: str | None = None) -> str:
        return f"http://localhost:8000/api/v1/oauth/{self.channel}/callback?code=MOCK-CODE&state={state}"

    async def exchange_code(self, code: str, _cv: str | None = None, **kw: Any) -> TokenBundle:
        agora = datetime.now(UTC)
        return TokenBundle(
            access_token=f"MOCK-ACCESS-{self.channel}-{code[:8]}",
            refresh_token=f"MOCK-REFRESH-{self.channel}",
            expires_at=agora + timedelta(hours=6),
            refresh_expires_at=agora + timedelta(days=180),
            scopes=["read", "offline_access"],
            external_account_id=str(kw.get("shop_id") or self._rnd("conta").randint(10**8, 10**9)),
        )

    async def refresh(self, refresh_token: str, **kw: Any) -> TokenBundle:
        return await self.exchange_code("REFRESHED", **kw)

    async def fetch_account_info(self, _token: str, **kw: Any) -> AccountInfo:
        rnd = self._rnd("conta")
        nomes = {
            Canal.MERCADO_LIVRE: "AUTOPECAS-DEMO-ML",
            Canal.SHOPEE: "Autopeças Demo Shopee",
            Canal.MERCADO_PAGO: "Conta MP Demo",
        }
        return AccountInfo(
            external_account_id=str(kw.get("shop_id") or rnd.randint(10**8, 10**9)),
            nickname=nomes.get(self.channel, "Conta Demo"),
            site_id="MLB" if self.channel != Canal.SHOPEE else "BR",
            metadata={"mock": True},
        )

    # --- Pedidos ------------------------------------------------------------

    async def fetch_orders(
        self, _token: str, *, since: datetime, until: datetime, **_: Any
    ) -> list[CanonicalOrder]:
        """Gera pedidos distribuídos no período, com sazonalidade por hora."""
        rnd = self._rnd(f"pedidos:{since.date()}:{until.date()}")
        dias = max(1, (until - since).days)
        # Volume compatível com a operação real: ML vende ~3× mais que Shopee.
        por_dia = 11 if self.channel == Canal.MERCADO_LIVRE else 4

        pedidos: list[CanonicalOrder] = []
        for dia in range(dias):
            data_base = since + timedelta(days=dia)
            for _ in range(rnd.randint(max(1, por_dia - 3), por_dia + 3)):
                pedidos.append(self._gerar_pedido(rnd, data_base))
        return pedidos

    def _gerar_pedido(self, rnd: random.Random, data_base: datetime) -> CanonicalOrder:
        # Curva de compra concentrada entre 9h e 22h, como no varejo real.
        hora = rnd.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 3, 5, 7, 9, 10, 10,
                                               9, 8, 9, 10, 10, 9, 8, 7, 6, 5, 3, 2])[0]
        criado = data_base.replace(hour=hora, minute=rnd.randint(0, 59), second=0, microsecond=0)

        qtd_itens = 1 if rnd.random() < 0.82 else 2
        itens: list[CanonicalOrderItem] = []
        bruto = Decimal("0")

        for _ in range(qtd_itens):
            sku, titulo, preco, _custo = rnd.choice(CATALOGO)
            unidades = Decimal(str(rnd.choices([1, 2, 3], weights=[75, 18, 7])[0]))
            total = (preco * unidades).quantize(Decimal("0.01"))
            bruto += total
            itens.append(
                CanonicalOrderItem(
                    external_item_id=f"MLB{rnd.randint(10**9, 10**10)}",
                    sku_channel=_sku_do_canal(sku, self.channel, rnd),
                    title=titulo,
                    quantity=unidades,
                    unit_price=preco,
                    gross_amount=total,
                )
            )

        estado = rnd.choice(ESTADOS)
        cancelado = rnd.random() < 0.045  # taxa de cancelamento realista
        idade_dias = (datetime.now(UTC) - criado.replace(tzinfo=UTC)).days

        if cancelado:
            status = StatusPedido.CANCELADO
        elif idade_dias > 9:
            status = StatusPedido.ENTREGUE
        elif idade_dias > 4:
            status = StatusPedido.ENVIADO
        elif idade_dias > 1:
            status = StatusPedido.PROCESSANDO
        else:
            status = StatusPedido.PAGO

        if self.channel == Canal.MERCADO_LIVRE:
            # 93% dos envios da operação real são Full.
            logistica = CanalLogistico.FULFILLMENT if rnd.random() < 0.93 else CanalLogistico.FLEX
            comissao = (bruto * Decimal("0.155")).quantize(Decimal("0.01"))
            frete_custo = (
                Decimal("0") if bruto < Decimal("79") else Decimal(str(rnd.uniform(15, 32))).quantize(Decimal("0.01"))
            )
            taxa_pagamento = (bruto * Decimal("0.012")).quantize(Decimal("0.01"))
            liquido = bruto - comissao - frete_custo - taxa_pagamento
            fonte = FonteLiquido.REPORTADO_API
        else:
            logistica = CanalLogistico.SHOPEE_XPRESS
            comissao = (bruto * Decimal("0.20")).quantize(Decimal("0.01"))
            frete_custo = Decimal("0")
            taxa_pagamento = Decimal("4.00")
            liquido = bruto - comissao - taxa_pagamento
            # Escrow da Shopee só existe após conclusão do pedido.
            fonte = (
                FonteLiquido.LIQUIDADO
                if status == StatusPedido.ENTREGUE
                else FonteLiquido.CALCULADO
            )

        if cancelado:
            liquido = Decimal("0")

        externo = (
            f"200{rnd.randint(10**9, 10**10)}"
            if self.channel == Canal.MERCADO_LIVRE
            else f"26{rnd.randint(10**10, 10**11)}"
        )
        # Na Shopee o escrow é buscado pelo número do pedido; no Mercado Livre o
        # pagamento tem identificador próprio.
        eh_shopee = self.channel == Canal.SHOPEE
        id_pagamento = externo if eh_shopee else str(rnd.randint(10**10, 10**11))

        # Pagamento coerente com este pedido: mesmos valores, mesmas taxas.
        liberado = status in (StatusPedido.ENTREGUE, StatusPedido.ENVIADO) and not cancelado
        self._pagamentos[id_pagamento] = CanonicalPayment(
            external_id=id_pagamento,
            channel=self.channel,
            provider=Canal.MERCADO_PAGO if self.channel != Canal.SHOPEE else "shopee_escrow",
            status=StatusPagamento.CANCELADO if cancelado else StatusPagamento.APROVADO,
            external_order_id=externo,
            payment_method=rnd.choice(["credit_card", "pix", "boleto"]),
            installments=rnd.choices([1, 2, 3, 6, 10], weights=[55, 12, 12, 13, 8])[0],
            transaction_amount=bruto,
            total_paid_amount=bruto,
            net_received_amount=liquido if not cancelado else Decimal("0"),
            fees=[
                CanonicalFee(fee_type=TipoTaxa.COMISSAO_MARKETPLACE, amount=comissao),
                CanonicalFee(fee_type=TipoTaxa.TAXA_PAGAMENTO, amount=taxa_pagamento),
            ],
            date_approved=criado.replace(tzinfo=UTC),
            money_release_date=criado.replace(tzinfo=UTC) + timedelta(days=14),
            money_release_status="released" if liberado else "pending",
        )

        return CanonicalOrder(
            external_id=externo,
            channel=self.channel,
            status=status,
            status_raw=status.value,
            date_created=criado.replace(tzinfo=UTC),
            date_last_updated=criado.replace(tzinfo=UTC) + timedelta(hours=rnd.randint(1, 48)),
            items=itens,
            gross_amount=bruto,
            shipping_revenue=Decimal("0") if frete_custo else Decimal(str(rnd.choice([0, 0, 19.9]))),
            shipping_cost=frete_custo,
            platform_fee=comissao,
            payment_fee=taxa_pagamento,
            net_amount=liquido,
            net_source=fonte,
            # Cerca de um terço das vendas vai para um comprador recorrente.
            # Sortear um comprador novo a cada pedido produziria uma base sem
            # nenhuma recompra, e a análise de coorte — que existe justamente
            # para medir isso — apareceria vazia na demonstração.
            buyer_external_id=str(
                rnd.randint(10**8, 10**8 + 400)
                if rnd.random() < 0.35
                else rnd.randint(10**8, 10**9)
            ),
            buyer_nickname=f"COMPRADOR{rnd.randint(1000, 9999)}",
            ship_state=estado,
            ship_city=rnd.choice(CIDADES[estado]),
            logistic_type=logistica,
            external_shipment_id=str(rnd.randint(10**10, 10**11)),
            # A Shopee não expõe identificador de pagamento no pedido: o valor
            # vem do escrow, consultado pelo próprio número do pedido.
            external_payment_ids=[] if eh_shopee else [id_pagamento],
            raw={"mock": True},
        )

    async def fetch_order(self, token: str, external_id: str, **_: Any) -> CanonicalOrder | None:
        rnd = self._rnd(f"pedido:{external_id}")
        pedido = self._gerar_pedido(rnd, datetime.now(UTC) - timedelta(hours=rnd.randint(0, 6)))
        pedido.external_id = external_id
        return pedido

    async def fetch_shipment(self, _token: str, external_id: str, **_: Any) -> CanonicalShipment:
        rnd = self._rnd(f"envio:{external_id}")
        enviado = datetime.now(UTC) - timedelta(days=rnd.randint(1, 8))
        estado = rnd.choice(ESTADOS)
        return CanonicalShipment(
            external_id=external_id,
            channel=self.channel,
            status=rnd.choice([StatusEnvio.ENVIADO, StatusEnvio.EM_TRANSITO, StatusEnvio.ENTREGUE]),
            tracking_number=f"BR{rnd.randint(10**8, 10**9)}BR",
            carrier=rnd.choice(["Mercado Envios", "Correios", "Shopee Xpress", "Loggi"]),
            date_shipped=enviado,
            estimated_delivery=enviado + timedelta(days=rnd.randint(3, 9)),
            cost_seller=Decimal(str(rnd.uniform(12, 34))).quantize(Decimal("0.01")),
            receiver_state=estado,
            receiver_city=rnd.choice(CIDADES[estado]),
        )

    async def fetch_payment(self, _token: str, external_id: str, **_: Any) -> CanonicalPayment:
        # Pagamento emitido junto do pedido, quando conhecido.
        registrado = self._pagamentos.get(external_id)
        if registrado is not None:
            return registrado

        rnd = self._rnd(f"pgto:{external_id}")
        valor = Decimal(str(rnd.uniform(40, 320))).quantize(Decimal("0.01"))
        taxa = (valor * Decimal("0.0499")).quantize(Decimal("0.01"))
        return CanonicalPayment(
            external_id=external_id,
            channel=self.channel,
            provider=Canal.MERCADO_PAGO if self.channel != Canal.SHOPEE else "shopee_escrow",
            status=StatusPagamento.APROVADO,
            transaction_amount=valor,
            total_paid_amount=valor,
            net_received_amount=valor - taxa,
            fees=[CanonicalFee(fee_type=TipoTaxa.TAXA_PAGAMENTO, amount=taxa)],
            date_approved=datetime.now(UTC) - timedelta(days=rnd.randint(0, 20)),
            money_release_date=datetime.now(UTC) + timedelta(days=rnd.randint(-5, 14)),
            money_release_status="released" if rnd.random() < 0.6 else "pending",
        )

    async def fetch_escrow(self, token: str, order_sn: str, **kw: Any) -> CanonicalPayment:
        return await self.fetch_payment(token, order_sn, **kw)

    # --- Catálogo e atendimento --------------------------------------------

    async def fetch_listings(self, _token: str, *_a: Any, **_kw: Any) -> list[CanonicalListing]:
        rnd = self._rnd("anuncios")
        anuncios = []
        for sku, titulo, preco, _custo in CATALOGO:
            estoque = rnd.choices([0, 2, 8, 25, 60], weights=[8, 12, 25, 35, 20])[0]
            anuncios.append(
                CanonicalListing(
                    external_id=f"MLB{rnd.randint(10**9, 10**10)}",
                    channel=self.channel,
                    title=titulo,
                    status="active",
                    listing_type="gold_pro" if rnd.random() < 0.6 else "gold_special",
                    sku_channel=_sku_do_canal(sku, self.channel, rnd),
                    price=preco,
                    available_quantity=estoque,
                    sold_quantity=rnd.randint(20, 400),
                    visits_30d=rnd.randint(180, 4200),
                    logistic_type="fulfillment",
                    health=Decimal(str(round(rnd.uniform(0.65, 1.0), 2))),
                    variations=[
                        CanonicalVariation(
                            external_variation_id=str(rnd.randint(10**10, 10**11)),
                            sku_channel=_sku_do_canal(sku, self.channel, rnd),
                            name="Padrão",
                            price=preco,
                            available_quantity=estoque,
                        )
                    ],
                )
            )
        return anuncios

    async def fetch_questions(self, _token: str, *_a: Any, **_kw: Any) -> list[CanonicalQuestion]:
        rnd = self._rnd("perguntas")
        textos = [
            "Serve no Gol G5 2012?", "Tem nota fiscal?", "Qual o prazo para o CEP 01310-100?",
            "É original ou paralelo?", "Vem o jogo completo?", "Aceita cartão parcelado?",
        ]
        return [
            CanonicalQuestion(
                external_id=str(rnd.randint(10**10, 10**11)),
                channel=self.channel,
                text=rnd.choice(textos),
                date_created=datetime.now(UTC) - timedelta(hours=rnd.randint(1, 72)),
                external_listing_id=f"MLB{rnd.randint(10**9, 10**10)}",
                status="unanswered" if rnd.random() < 0.4 else "answered",
            )
            for _ in range(rnd.randint(4, 12))
        ]

    async def fetch_claims(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []

    async def fetch_campaigns(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []

    async def fetch_seller_reputation(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
        rnd = self._rnd("reputacao")
        return {
            "level_id": "5_green",
            "power_seller_status": "platinum",
            "transactions": {
                "total": rnd.randint(800, 2500),
                "ratings": {"positive": 0.97, "neutral": 0.02, "negative": 0.01},
            },
            "metrics": {"claims": {"rate": 0.008}, "cancellations": {"rate": 0.012}},
        }

    # --- Webhooks -----------------------------------------------------------

    def parse_webhook(self, body: dict[str, Any], _headers: dict[str, str]) -> WebhookNotification:
        return WebhookNotification(
            channel=self.channel,
            topic=str(body.get("topic") or body.get("type") or "orders_v2"),
            resource=str(body.get("resource") or ""),
            external_account_id=str(body.get("user_id") or body.get("shop_id") or ""),
            external_event_id=str(body.get("_id") or ""),
            raw=body,
        )

    def verify_signature(self, _body: bytes, _headers: dict[str, str], _url: str = "") -> bool:
        return True
