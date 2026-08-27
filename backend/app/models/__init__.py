"""Registro central dos models.

Importar tudo aqui garante que o ``Base.metadata`` esteja completo para o
Alembic autogenerate e para ``create_all`` em desenvolvimento.
"""
from app.db.base import Base
from app.models.catalog import (
    InventorySnapshot,
    Listing,
    ListingVariation,
    Product,
    SkuLink,
    SkuPendency,
)
from app.models.costs import MonthlyClose, OperatingExpense, TaxRule
from app.models.channel import (
    ChannelAccount,
    ChannelCredential,
    IntegrationLog,
    OAuthState,
    SyncCursor,
    WebhookEvent,
)
from app.models.finance import (
    Payment,
    PaymentFee,
    Reconciliation,
    Refund,
    Settlement,
    SettlementEntry,
)
from app.models.marketing import AdSpend, Campaign, CampaignItem
from app.models.metrics import Alert, AlertRule, MetricDaily, MetricHourly, MetricSnapshot
from app.models.order import Order, OrderEvent, OrderItem, Shipment, ShipmentEvent
from app.models.support import Claim, ClaimEvent, Message, Question, Review
from app.models.tenant import AuditLog, Tenant, User, UserSession

__all__ = [
    "Base",
    # SaaS
    "Tenant", "User", "UserSession", "AuditLog",
    # Integração
    "ChannelAccount", "ChannelCredential", "OAuthState", "SyncCursor",
    "WebhookEvent", "IntegrationLog",
    # Catálogo
    "Product", "Listing", "ListingVariation", "SkuLink", "SkuPendency",
    "InventorySnapshot",
    # Comercial
    "Order", "OrderItem", "OrderEvent", "Shipment", "ShipmentEvent",
    # Financeiro
    "Payment", "PaymentFee", "Refund", "Settlement", "SettlementEntry",
    "Reconciliation",
    # Atendimento
    "Question", "Message", "Claim", "ClaimEvent", "Review",
    # Marketing
    "AdSpend", "Campaign", "CampaignItem",
    # Custos e impostos
    "TaxRule", "OperatingExpense", "MonthlyClose",
    # Métricas
    "MetricHourly", "MetricDaily", "MetricSnapshot", "AlertRule", "Alert",
]
