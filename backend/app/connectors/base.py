"""Contratos da camada de conectores (padrão anticorrupção).

Estes DTOs são a fronteira do sistema. Tudo que vem de um marketplace é
convertido para cá **antes** de tocar em qualquer regra de negócio. Em troca,
nenhum serviço, endpoint ou componente de interface precisa saber que o Mercado
Livre chama a comissão de ``sale_fee`` e a Shopee de ``commission_fee``.

O ganho concreto: adicionar Amazon ou Magalu depois é escrever um pacote novo em
``connectors/``, sem tocar em ``services/``, ``api/`` nem no frontend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.models.enums import (
    CanalLogistico,
    FonteLiquido,
    StatusEnvio,
    StatusPagamento,
    StatusPedido,
)

ZERO = Decimal("0")


# --- Autenticação ------------------------------------------------------------

@dataclass(slots=True)
class TokenBundle:
    """Tokens devolvidos por uma troca ou renovação de credencial."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    external_account_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AccountInfo:
    """Identidade da conta conectada, lida logo após a autorização."""

    external_account_id: str
    nickname: str = ""
    site_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# --- Comercial ---------------------------------------------------------------

@dataclass(slots=True)
class CanonicalOrderItem:
    external_item_id: str = ""
    sku_channel: str = ""
    title: str = ""
    variation_name: str = ""
    external_variation_id: str = ""
    quantity: Decimal = ZERO
    unit_price: Decimal = ZERO
    gross_amount: Decimal = ZERO
    platform_fee: Decimal = ZERO
    discount_amount: Decimal = ZERO


@dataclass(slots=True)
class CanonicalOrder:
    """Pedido no vocabulário do domínio, independente de canal."""

    external_id: str
    channel: str
    date_created: datetime
    status: StatusPedido = StatusPedido.PENDENTE
    status_raw: str = ""
    status_detail: str = ""
    external_pack_id: str | None = None
    date_closed: datetime | None = None
    date_last_updated: datetime | None = None
    currency: str = "BRL"

    items: list[CanonicalOrderItem] = field(default_factory=list)

    gross_amount: Decimal = ZERO
    shipping_revenue: Decimal = ZERO
    shipping_cost: Decimal = ZERO
    platform_fee: Decimal = ZERO
    payment_fee: Decimal = ZERO
    discount_amount: Decimal = ZERO
    refund_amount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    #: Preenchido só quando o canal informa o líquido de verdade. ``None`` faz o
    #: serviço financeiro calcular a estimativa e marcar como ``computed``.
    net_amount: Decimal | None = None
    net_source: FonteLiquido = FonteLiquido.CALCULADO

    buyer_external_id: str | None = None
    buyer_nickname: str = ""
    ship_state: str = ""
    ship_city: str = ""
    logistic_type: CanalLogistico | str = ""

    external_shipment_id: str | None = None
    external_payment_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalShipment:
    external_id: str
    channel: str
    status: StatusEnvio = StatusEnvio.PENDENTE
    status_raw: str = ""
    substatus: str = ""
    external_order_id: str = ""
    tracking_number: str = ""
    carrier: str = ""
    logistic_type: str = ""
    date_shipped: datetime | None = None
    date_delivered: datetime | None = None
    estimated_delivery: datetime | None = None
    cost_seller: Decimal = ZERO
    cost_buyer: Decimal = ZERO
    receiver_state: str = ""
    receiver_city: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# --- Financeiro --------------------------------------------------------------

@dataclass(slots=True)
class CanonicalFee:
    """Taxa individual já traduzida para ``TipoTaxa``."""

    fee_type: str
    amount: Decimal
    fee_type_raw: str = ""
    payer: str = "collector"


@dataclass(slots=True)
class CanonicalPayment:
    external_id: str
    channel: str
    provider: str
    status: StatusPagamento = StatusPagamento.PENDENTE
    status_raw: str = ""
    status_detail: str = ""
    external_order_id: str = ""
    payment_method: str = ""
    installments: int = 1
    currency: str = "BRL"
    transaction_amount: Decimal = ZERO
    total_paid_amount: Decimal = ZERO
    shipping_amount: Decimal = ZERO
    taxes_amount: Decimal = ZERO
    net_received_amount: Decimal = ZERO
    fees: list[CanonicalFee] = field(default_factory=list)
    date_approved: datetime | None = None
    money_release_date: datetime | None = None
    money_release_status: str = ""
    refunds: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def fee_total(self) -> Decimal:
        return sum((f.amount for f in self.fees), ZERO)


@dataclass(slots=True)
class CanonicalSettlement:
    """Repasse: o crédito efetivamente liberado ao vendedor."""

    external_id: str
    channel: str
    settlement_date: datetime
    gross_amount: Decimal = ZERO
    fee_amount: Decimal = ZERO
    net_amount: Decimal = ZERO
    currency: str = "BRL"
    status: str = ""
    source: str = ""
    bank_reference: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# --- Catálogo e atendimento --------------------------------------------------

@dataclass(slots=True)
class CanonicalVariation:
    external_variation_id: str
    sku_channel: str = ""
    name: str = ""
    price: Decimal = ZERO
    available_quantity: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalListing:
    external_id: str
    channel: str
    title: str = ""
    status: str = "active"
    listing_type: str = ""
    category_id: str = ""
    sku_channel: str = ""
    price: Decimal = ZERO
    available_quantity: int = 0
    sold_quantity: int = 0
    permalink: str = ""
    thumbnail: str = ""
    logistic_type: str = ""
    health: Decimal | None = None
    visits_30d: int = 0
    variations: list[CanonicalVariation] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalQuestion:
    external_id: str
    channel: str
    text: str
    date_created: datetime
    external_listing_id: str = ""
    answer_text: str = ""
    status: str = "unanswered"
    asker_external_id: str | None = None
    date_answered: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalClaim:
    external_id: str
    channel: str
    opened_at: datetime
    external_order_id: str = ""
    type: str = "claim"
    stage: str = ""
    status: str = "opened"
    reason_code: str = ""
    reason_text: str = ""
    resolution: str = ""
    amount_involved: Decimal = ZERO
    closed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalCampaign:
    external_id: str
    channel: str
    name: str = ""
    type: str = "discount"
    status: str = "active"
    start_at: datetime | None = None
    end_at: datetime | None = None
    budget: Decimal = ZERO
    items: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookNotification:
    """Notificação normalizada, produzida pelo conector a partir do corpo cru."""

    channel: str
    topic: str
    resource: str = ""
    external_account_id: str = ""
    external_event_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# --- Protocolo ---------------------------------------------------------------

@runtime_checkable
class Connector(Protocol):
    """Contrato que todo marketplace precisa cumprir.

    Métodos não suportados por um canal levantam ``NotImplementedError`` e a
    interface esconde a funcionalidade correspondente, em vez de exibir um erro
    recorrente ao usuário (caso, por exemplo, da Livestream API da Shopee, que
    não está liberada em todas as regiões).
    """

    channel: str
    API_VERSION: str

    async def build_authorization_url(self, state: str, code_verifier: str | None) -> str: ...

    async def exchange_code(self, code: str, code_verifier: str | None, **kw: Any) -> TokenBundle: ...

    async def refresh(self, refresh_token: str, **kw: Any) -> TokenBundle: ...

    async def fetch_account_info(self, token: str, **kw: Any) -> AccountInfo: ...

    async def fetch_orders(
        self, token: str, *, since: datetime, until: datetime, **kw: Any
    ) -> list[CanonicalOrder]: ...

    async def fetch_order(self, token: str, external_id: str, **kw: Any) -> CanonicalOrder | None: ...

    def parse_webhook(self, body: dict[str, Any], headers: dict[str, str]) -> WebhookNotification: ...

    def verify_signature(self, body: bytes, headers: dict[str, str], url: str = "") -> bool: ...
