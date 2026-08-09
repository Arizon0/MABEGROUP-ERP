"""Tradução dos payloads da Shopee para o modelo canônico."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.connectors.base import (
    CanonicalCampaign,
    CanonicalFee,
    CanonicalListing,
    CanonicalOrder,
    CanonicalOrderItem,
    CanonicalPayment,
    CanonicalShipment,
    CanonicalVariation,
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

ZERO = Decimal("0")

STATUS_PEDIDO = {
    "UNPAID": StatusPedido.PENDENTE,
    "READY_TO_SHIP": StatusPedido.PROCESSANDO,
    "PROCESSED": StatusPedido.PROCESSANDO,
    "RETRY_SHIP": StatusPedido.PROCESSANDO,
    "SHIPPED": StatusPedido.ENVIADO,
    "TO_CONFIRM_RECEIVE": StatusPedido.ENVIADO,
    "COMPLETED": StatusPedido.ENTREGUE,
    "IN_CANCEL": StatusPedido.PROCESSANDO,
    "CANCELLED": StatusPedido.CANCELADO,
    "INVOICE_PENDING": StatusPedido.PAGO,
    "TO_RETURN": StatusPedido.DEVOLVIDO,
}

STATUS_ENVIO = {
    "READY_TO_SHIP": StatusEnvio.PRONTO_PARA_ENVIO,
    "PROCESSED": StatusEnvio.PRONTO_PARA_ENVIO,
    "SHIPPED": StatusEnvio.ENVIADO,
    "TO_CONFIRM_RECEIVE": StatusEnvio.EM_TRANSITO,
    "COMPLETED": StatusEnvio.ENTREGUE,
    "CANCELLED": StatusEnvio.CANCELADO,
    "TO_RETURN": StatusEnvio.DEVOLVIDO,
}


def dec(valor: Any, padrao: Decimal = ZERO) -> Decimal:
    if valor is None or valor == "":
        return padrao
    try:
        return Decimal(str(valor))
    except (ArithmeticError, ValueError, TypeError):
        return padrao


def ts(valor: Any) -> datetime | None:
    """A Shopee usa epoch em segundos (UTC) em todos os campos de data."""
    if not valor:
        return None
    try:
        return datetime.fromtimestamp(int(valor), tz=UTC)
    except (ValueError, OSError, TypeError):
        return None


def normalizar_pedido(payload: dict[str, Any]) -> CanonicalOrder:
    """Converte ``get_order_detail``.

    ⚠️ ``optional_fields`` precisa ser pedido explicitamente na requisição. Sem
    isso a Shopee devolve um objeto que *parece* completo mas vem sem
    ``item_list`` e sem ``recipient_address`` — e o pedido entraria no sistema
    zerado, sem nenhum erro.
    """
    status_raw = str(payload.get("order_status") or "")
    itens: list[CanonicalOrderItem] = []
    bruto = ZERO

    for linha in payload.get("item_list") or []:
        qtd = dec(linha.get("model_quantity_purchased"), Decimal("1"))
        preco = dec(linha.get("model_discounted_price") or linha.get("model_original_price"))
        total = (preco * qtd).quantize(Decimal("0.0001"))
        bruto += total
        itens.append(
            CanonicalOrderItem(
                external_item_id=str(linha.get("item_id") or ""),
                sku_channel=str(linha.get("model_sku") or linha.get("item_sku") or ""),
                title=str(linha.get("item_name") or ""),
                variation_name=str(linha.get("model_name") or ""),
                external_variation_id=str(linha.get("model_id") or ""),
                quantity=qtd,
                unit_price=preco,
                gross_amount=total,
                discount_amount=(
                    dec(linha.get("model_original_price")) - dec(linha.get("model_discounted_price"))
                ).max(ZERO)
                * qtd,
            )
        )

    endereco = payload.get("recipient_address") or {}

    return CanonicalOrder(
        external_id=str(payload.get("order_sn") or ""),
        channel=Canal.SHOPEE,
        status=STATUS_PEDIDO.get(status_raw, StatusPedido.PENDENTE),
        status_raw=status_raw,
        status_detail=str(payload.get("cancel_reason") or ""),
        date_created=ts(payload.get("create_time")) or datetime.now(UTC),
        date_last_updated=ts(payload.get("update_time")),
        currency=str(payload.get("currency") or "BRL"),
        items=itens,
        gross_amount=bruto,
        shipping_revenue=dec(payload.get("estimated_shipping_fee")),
        buyer_external_id=str(payload.get("buyer_user_id") or "") or None,
        buyer_nickname=str(payload.get("buyer_username") or ""),
        ship_state=str(endereco.get("state") or ""),
        ship_city=str(endereco.get("city") or ""),
        logistic_type=CanalLogistico.SHOPEE_XPRESS,
        external_shipment_id=str(payload.get("package_number") or "") or None,
        raw=payload,
    )


def normalizar_escrow(payload: dict[str, Any], order_sn: str = "") -> CanonicalPayment:
    """Converte ``get_escrow_detail`` — a fonte da verdade financeira da Shopee.

    ``escrow_amount`` é o líquido efetivamente recebido pelo vendedor. Diferente
    do Mercado Livre, ele só existe **após** o comprador confirmar o recebimento
    (7 a 15 dias). Antes disso não há líquido nenhum informado, e o sistema
    trabalha com estimativa marcada como ``computed`` — ver
    ``docs/06-financeiro-conciliacao.md``.
    """
    receita = payload.get("order_income") or {}

    taxas = []
    for campo, tipo in (
        ("commission_fee", TipoTaxa.COMISSAO_MARKETPLACE),
        ("service_fee", TipoTaxa.TAXA_SERVICO),
        ("transaction_fee", TipoTaxa.TAXA_PAGAMENTO),
        ("seller_transaction_fee", TipoTaxa.TAXA_PAGAMENTO),
        ("credit_card_transaction_fee", TipoTaxa.TAXA_PAGAMENTO),
    ):
        valor = dec(receita.get(campo))
        if valor:
            taxas.append(CanonicalFee(fee_type=tipo, fee_type_raw=campo, amount=valor))

    frete_real = dec(receita.get("actual_shipping_fee"))
    frete_comprador = dec(receita.get("buyer_paid_shipping_fee"))
    frete_reverso = dec(receita.get("reverse_shipping_fee"))
    if frete_real or frete_reverso:
        taxas.append(
            CanonicalFee(
                fee_type=TipoTaxa.TAXA_ENVIO,
                fee_type_raw="actual_shipping_fee",
                amount=frete_real + frete_reverso,
            )
        )

    liquido = dec(receita.get("escrow_amount"))
    liberacao = ts(payload.get("escrow_release_time") or receita.get("escrow_release_time"))

    return CanonicalPayment(
        external_id=str(payload.get("order_sn") or order_sn),
        channel=Canal.SHOPEE,
        provider="shopee_escrow",
        # O escrow só é emitido para pedidos já concluídos financeiramente.
        status=StatusPagamento.APROVADO if liquido else StatusPagamento.PENDENTE,
        status_raw="escrow",
        external_order_id=str(payload.get("order_sn") or order_sn),
        payment_method=str(payload.get("payment_method") or ""),
        currency=str(receita.get("currency") or "BRL"),
        transaction_amount=dec(receita.get("original_price")),
        total_paid_amount=dec(receita.get("buyer_total_amount")),
        shipping_amount=frete_comprador,
        net_received_amount=liquido,
        fees=taxas,
        money_release_date=liberacao,
        money_release_status="released" if liquido else "pending",
        raw=payload,
    )


def normalizar_envio(payload: dict[str, Any], rastreio: dict[str, Any] | None = None) -> CanonicalShipment:
    """Converte os dados logísticos de um pedido."""
    rastreio = rastreio or {}
    status_raw = str(payload.get("order_status") or "")
    eventos = rastreio.get("tracking_info") or []

    entregue = None
    enviado = None
    for evento in eventos:
        status = str(evento.get("logistics_status") or "")
        if status == "LOGISTICS_DELIVERY_DONE":
            entregue = ts(evento.get("update_time"))
        elif status in ("LOGISTICS_PICKUP_DONE", "LOGISTICS_REQUEST_CREATED") and not enviado:
            enviado = ts(evento.get("update_time"))

    return CanonicalShipment(
        external_id=str(payload.get("package_number") or payload.get("order_sn") or ""),
        channel=Canal.SHOPEE,
        status=STATUS_ENVIO.get(status_raw, StatusEnvio.PENDENTE),
        status_raw=status_raw,
        external_order_id=str(payload.get("order_sn") or ""),
        tracking_number=str(payload.get("tracking_number") or rastreio.get("tracking_number") or ""),
        carrier=str(payload.get("shipping_carrier") or ""),
        logistic_type=str(payload.get("checkout_shipping_carrier") or ""),
        date_shipped=enviado or ts(payload.get("ship_by_date")),
        date_delivered=entregue,
        estimated_delivery=ts(payload.get("ship_by_date")),
        receiver_state=str((payload.get("recipient_address") or {}).get("state") or ""),
        receiver_city=str((payload.get("recipient_address") or {}).get("city") or ""),
        events=[
            {
                "status": e.get("logistics_status"),
                "description": e.get("description"),
                "occurred_at": ts(e.get("update_time")),
            }
            for e in eventos
        ],
        raw=payload,
    )


def normalizar_anuncio(base: dict[str, Any], modelos: list[dict[str, Any]] | None = None) -> CanonicalListing:
    """Converte ``get_item_base_info`` + ``get_model_list``."""
    preco_info = (base.get("price_info") or [{}])[0]
    estoque_info = base.get("stock_info_v2") or {}
    resumo = (estoque_info.get("summary_info") or {}) if isinstance(estoque_info, dict) else {}

    variacoes = [
        CanonicalVariation(
            external_variation_id=str(m.get("model_id") or ""),
            sku_channel=str(m.get("model_sku") or ""),
            name=" / ".join(str(t.get("option", "")) for t in (m.get("tier_index_names") or [])),
            price=dec((m.get("price_info") or [{}])[0].get("current_price")),
            available_quantity=int(
                ((m.get("stock_info_v2") or {}).get("summary_info") or {}).get(
                    "total_available_stock", 0
                )
                or 0
            ),
        )
        for m in modelos or []
    ]

    return CanonicalListing(
        external_id=str(base.get("item_id") or ""),
        channel=Canal.SHOPEE,
        title=str(base.get("item_name") or ""),
        status=str(base.get("item_status") or "NORMAL").lower(),
        category_id=str(base.get("category_id") or ""),
        sku_channel=str(base.get("item_sku") or ""),
        price=dec(preco_info.get("current_price")),
        available_quantity=int(resumo.get("total_available_stock", 0) or 0),
        sold_quantity=int(base.get("sale") or 0),
        thumbnail=str(((base.get("image") or {}).get("image_url_list") or [""])[0]),
        visits_30d=int(base.get("views") or 0),
        variations=variacoes,
        raw=base,
    )


def normalizar_campanha(payload: dict[str, Any], tipo: str = "discount") -> CanonicalCampaign:
    """Converte ``get_discount``/``get_voucher``."""
    return CanonicalCampaign(
        external_id=str(payload.get("discount_id") or payload.get("voucher_id") or ""),
        channel=Canal.SHOPEE,
        name=str(payload.get("discount_name") or payload.get("voucher_name") or ""),
        type=tipo,
        status=str(payload.get("status") or "active").lower(),
        start_at=ts(payload.get("start_time")),
        end_at=ts(payload.get("end_time")),
        items=payload.get("item_list") or [],
        raw=payload,
    )


def estimar_liquido(pedido: CanonicalOrder) -> CanonicalOrder:
    """Estima o líquido antes do escrow existir.

    Percentuais praticados no Brasil em 2026 e configuráveis por tenant. É uma
    aproximação declarada — fica marcada como ``computed`` e é substituída pelo
    valor real quando o escrow chega. A diferença entre as duas vira métrica da
    qualidade da própria estimativa.
    """
    comissao = (pedido.gross_amount * Decimal("0.14")).quantize(Decimal("0.0001"))
    servico = (pedido.gross_amount * Decimal("0.06")).quantize(Decimal("0.0001"))
    transacao = Decimal("4.00") if pedido.gross_amount > ZERO else ZERO

    pedido.platform_fee = comissao + servico
    pedido.payment_fee = transacao
    pedido.net_amount = pedido.gross_amount - comissao - servico - transacao
    pedido.net_source = FonteLiquido.CALCULADO
    return pedido
