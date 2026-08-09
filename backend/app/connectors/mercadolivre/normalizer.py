"""Tradução dos payloads do Mercado Livre para o modelo canônico.

Isolado do cliente HTTP de propósito: normalizador é função pura sobre `dict`,
o que permite testá-lo com payloads reais gravados, sem rede.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.connectors.base import (
    CanonicalClaim,
    CanonicalFee,
    CanonicalListing,
    CanonicalOrder,
    CanonicalOrderItem,
    CanonicalPayment,
    CanonicalQuestion,
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

# --- Tabelas de tradução -----------------------------------------------------

STATUS_PEDIDO = {
    "confirmed": StatusPedido.PENDENTE,
    "payment_required": StatusPedido.PENDENTE,
    "payment_in_process": StatusPedido.PENDENTE,
    "partially_paid": StatusPedido.PENDENTE,
    "paid": StatusPedido.PAGO,
    "partially_refunded": StatusPedido.PAGO,
    "pending_cancel": StatusPedido.PROCESSANDO,
    "cancelled": StatusPedido.CANCELADO,
    "invalid": StatusPedido.CANCELADO,
}

STATUS_ENVIO = {
    "pending": StatusEnvio.PENDENTE,
    "handling": StatusEnvio.PRONTO_PARA_ENVIO,
    "ready_to_ship": StatusEnvio.PRONTO_PARA_ENVIO,
    "shipped": StatusEnvio.ENVIADO,
    "delivered": StatusEnvio.ENTREGUE,
    "not_delivered": StatusEnvio.NAO_ENTREGUE,
    "returning_to_sender": StatusEnvio.DEVOLVIDO,
    "cancelled": StatusEnvio.CANCELADO,
}

STATUS_PAGAMENTO = {
    "pending": StatusPagamento.PENDENTE,
    "approved": StatusPagamento.APROVADO,
    "authorized": StatusPagamento.AUTORIZADO,
    "in_process": StatusPagamento.EM_ANALISE,
    "in_mediation": StatusPagamento.EM_ANALISE,
    "rejected": StatusPagamento.REJEITADO,
    "refunded": StatusPagamento.DEVOLVIDO,
    "charged_back": StatusPagamento.ESTORNADO,
    "cancelled": StatusPagamento.CANCELADO,
}

#: ``logistic_type`` do ML → canal logístico canônico.
CANAL_LOGISTICO = {
    "fulfillment": CanalLogistico.FULFILLMENT,      # ML Full
    "self_service": CanalLogistico.FLEX,            # ML Flex
    "cross_docking": CanalLogistico.CROSS_DOCKING,  # Coleta
    "drop_off": CanalLogistico.AGENCIA,             # Agência/Correios
    "xd_drop_off": CanalLogistico.AGENCIA,
}

TIPO_TAXA = {
    "mercadopago_fee": TipoTaxa.TAXA_PAGAMENTO,
    "financing_fee": TipoTaxa.TAXA_PARCELAMENTO,
    "shipping_fee": TipoTaxa.TAXA_ENVIO,
    "application_fee": TipoTaxa.TAXA_APLICACAO,
    "discount_fee": TipoTaxa.OUTRA,
    "coupon_fee": TipoTaxa.OUTRA,
}


# --- Utilidades --------------------------------------------------------------

def dec(valor: Any, padrao: Decimal = ZERO) -> Decimal:
    """Converte para ``Decimal`` passando por ``str``.

    ``Decimal(0.1)`` traz o ruído binário do float junto; ``Decimal("0.1")`` não.
    Como os valores chegam do JSON já como float, a conversão via ``str`` é o que
    mantém a exatidão do centavo.
    """
    if valor is None or valor == "":
        return padrao
    try:
        return Decimal(str(valor))
    except (ArithmeticError, ValueError, TypeError):
        return padrao


def dt(valor: Any) -> datetime | None:
    """Converte a data ISO-8601 do ML (com offset) para ``datetime`` com fuso."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor
    texto = str(valor).strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


# --- Normalizadores ----------------------------------------------------------

def normalizar_pedido(payload: dict[str, Any]) -> CanonicalOrder:
    """Converte ``GET /orders/{id}`` para :class:`CanonicalOrder`.

    ``sale_fee`` de cada item é a fonte mais confiável de comissão por pedido —
    melhor do que estimar pela tabela de ``listing_prices``, porque já reflete
    descontos e condições específicas daquela venda.
    """
    status_raw = str(payload.get("status") or "")
    itens: list[CanonicalOrderItem] = []
    bruto = ZERO
    comissao = ZERO

    for linha in payload.get("order_items") or []:
        item = linha.get("item") or {}
        qtd = dec(linha.get("quantity"), Decimal("1"))
        preco = dec(linha.get("unit_price"))
        taxa = dec(linha.get("sale_fee"))
        total_linha = (preco * qtd).quantize(Decimal("0.0001"))
        # sale_fee vem por unidade; o total da comissão da linha acompanha a
        # quantidade vendida.
        comissao_linha = (taxa * qtd).quantize(Decimal("0.0001"))
        bruto += total_linha
        comissao += comissao_linha

        variacao = ""
        for atributo in item.get("variation_attributes") or []:
            variacao = f"{variacao} {atributo.get('value_name', '')}".strip()

        itens.append(
            CanonicalOrderItem(
                external_item_id=str(item.get("id") or ""),
                sku_channel=str(item.get("seller_sku") or item.get("seller_custom_field") or ""),
                title=str(item.get("title") or ""),
                variation_name=variacao,
                external_variation_id=str(item.get("variation_id") or ""),
                quantity=qtd,
                unit_price=preco,
                gross_amount=total_linha,
                platform_fee=comissao_linha,
            )
        )

    envio = payload.get("shipping") or {}
    comprador = payload.get("buyer") or {}
    endereco = (envio.get("receiver_address") or {}) if isinstance(envio, dict) else {}
    estado = (endereco.get("state") or {}).get("name", "") if isinstance(endereco, dict) else ""
    cidade = (endereco.get("city") or {}).get("name", "") if isinstance(endereco, dict) else ""

    pagamentos = payload.get("payments") or []
    tipo_logistico = str(envio.get("logistic_type") or "") if isinstance(envio, dict) else ""

    return CanonicalOrder(
        external_id=str(payload.get("id") or ""),
        channel=Canal.MERCADO_LIVRE,
        status=STATUS_PEDIDO.get(status_raw, StatusPedido.PENDENTE),
        status_raw=status_raw,
        status_detail=str(payload.get("status_detail") or ""),
        external_pack_id=str(payload["pack_id"]) if payload.get("pack_id") else None,
        date_created=dt(payload.get("date_created")) or datetime.now().astimezone(),
        date_closed=dt(payload.get("date_closed")),
        date_last_updated=dt(payload.get("last_updated")),
        currency=str(payload.get("currency_id") or "BRL"),
        items=itens,
        gross_amount=bruto,
        platform_fee=comissao,
        # O ML devolve o total já com desconto aplicado; a diferença para o bruto
        # dos itens é o desconto concedido.
        discount_amount=max(ZERO, bruto - dec(payload.get("total_amount"), bruto)),
        buyer_external_id=str(comprador.get("id")) if comprador.get("id") else None,
        buyer_nickname=str(comprador.get("nickname") or ""),
        ship_state=estado,
        ship_city=cidade,
        logistic_type=CANAL_LOGISTICO.get(tipo_logistico, CanalLogistico.OUTRO),
        external_shipment_id=str(envio.get("id")) if envio.get("id") else None,
        external_payment_ids=[str(p.get("id")) for p in pagamentos if p.get("id")],
        raw=payload,
    )


def normalizar_envio(payload: dict[str, Any], custos: dict[str, Any] | None = None) -> CanonicalShipment:
    """Converte ``GET /shipments/{id}`` (+ ``/costs``) para o modelo canônico.

    O custo real do frete só existe em ``/shipments/{id}/costs``: ``senders[].cost``
    é o que o vendedor efetivamente pagou e ``receiver_cost`` o que o comprador
    pagou. Sem essa segunda chamada, o frete fica subestimado no líquido.
    """
    status_raw = str(payload.get("status") or "")
    custos = custos or {}

    custo_vendedor = ZERO
    for remetente in custos.get("senders") or []:
        custo_vendedor += dec(remetente.get("cost"))

    endereco = payload.get("receiver_address") or {}

    return CanonicalShipment(
        external_id=str(payload.get("id") or ""),
        channel=Canal.MERCADO_LIVRE,
        status=STATUS_ENVIO.get(status_raw, StatusEnvio.PENDENTE),
        status_raw=status_raw,
        substatus=str(payload.get("substatus") or ""),
        external_order_id=str(payload.get("order_id") or ""),
        tracking_number=str(payload.get("tracking_number") or ""),
        carrier=str(payload.get("tracking_method") or ""),
        logistic_type=str(payload.get("logistic_type") or ""),
        date_shipped=dt((payload.get("status_history") or {}).get("date_shipped")),
        date_delivered=dt((payload.get("status_history") or {}).get("date_delivered")),
        estimated_delivery=dt(
            (payload.get("estimated_delivery_time") or {}).get("date")
            if isinstance(payload.get("estimated_delivery_time"), dict)
            else payload.get("estimated_delivery_time")
        ),
        cost_seller=custo_vendedor,
        cost_buyer=dec(custos.get("receiver_cost")),
        receiver_state=(endereco.get("state") or {}).get("name", "") if endereco else "",
        receiver_city=(endereco.get("city") or {}).get("name", "") if endereco else "",
        raw=payload,
    )


def normalizar_pagamento(payload: dict[str, Any]) -> CanonicalPayment:
    """Converte ``GET /v1/payments/{id}`` do Mercado Pago.

    ``transaction_details.net_received_amount`` é o líquido oficial. Quando
    presente, é usado como fonte primária e marcado ``api_reported`` — recalcular
    aqui só produziria divergência com o extrato que o vendedor vê no painel do MP.
    """
    status_raw = str(payload.get("status") or "")
    detalhes = payload.get("transaction_details") or {}

    taxas = [
        CanonicalFee(
            fee_type=TIPO_TAXA.get(str(t.get("type")), TipoTaxa.OUTRA),
            fee_type_raw=str(t.get("type") or ""),
            amount=dec(t.get("amount")),
            payer=str(t.get("fee_payer") or "collector"),
        )
        for t in payload.get("fee_details") or []
    ]

    return CanonicalPayment(
        external_id=str(payload.get("id") or ""),
        channel=Canal.MERCADO_LIVRE,
        provider=Canal.MERCADO_PAGO,
        status=STATUS_PAGAMENTO.get(status_raw, StatusPagamento.PENDENTE),
        status_raw=status_raw,
        status_detail=str(payload.get("status_detail") or ""),
        external_order_id=str(payload.get("order_id") or payload.get("external_reference") or ""),
        payment_method=str(payload.get("payment_method_id") or ""),
        installments=int(payload.get("installments") or 1),
        currency=str(payload.get("currency_id") or "BRL"),
        transaction_amount=dec(payload.get("transaction_amount")),
        total_paid_amount=dec(detalhes.get("total_paid_amount")),
        shipping_amount=dec(payload.get("shipping_amount")),
        taxes_amount=dec(payload.get("taxes_amount")),
        net_received_amount=dec(detalhes.get("net_received_amount")),
        fees=taxas,
        date_approved=dt(payload.get("date_approved")),
        money_release_date=dt(payload.get("money_release_date")),
        money_release_status=str(payload.get("money_release_status") or ""),
        refunds=payload.get("refunds") or [],
        raw=payload,
    )


def normalizar_anuncio(payload: dict[str, Any]) -> CanonicalListing:
    """Converte ``GET /items/{id}``."""
    envio = payload.get("shipping") or {}
    variacoes = [
        CanonicalVariation(
            external_variation_id=str(v.get("id") or ""),
            sku_channel=str(v.get("seller_sku") or ""),
            name=" / ".join(
                str(a.get("value_name", "")) for a in (v.get("attribute_combinations") or [])
            ),
            price=dec(v.get("price")),
            available_quantity=int(v.get("available_quantity") or 0),
            attributes={
                str(a.get("name")): a.get("value_name")
                for a in (v.get("attribute_combinations") or [])
            },
        )
        for v in payload.get("variations") or []
    ]

    return CanonicalListing(
        external_id=str(payload.get("id") or ""),
        channel=Canal.MERCADO_LIVRE,
        title=str(payload.get("title") or ""),
        status=str(payload.get("status") or "active"),
        listing_type=str(payload.get("listing_type_id") or ""),
        category_id=str(payload.get("category_id") or ""),
        sku_channel=str(payload.get("seller_custom_field") or ""),
        price=dec(payload.get("price")),
        available_quantity=int(payload.get("available_quantity") or 0),
        sold_quantity=int(payload.get("sold_quantity") or 0),
        permalink=str(payload.get("permalink") or ""),
        thumbnail=str(payload.get("thumbnail") or ""),
        logistic_type=str(envio.get("logistic_type") or ""),
        health=dec(payload.get("health")) if payload.get("health") is not None else None,
        variations=variacoes,
        raw=payload,
    )


def normalizar_pergunta(payload: dict[str, Any]) -> CanonicalQuestion:
    """Converte ``GET /questions/search``."""
    resposta = payload.get("answer") or {}
    return CanonicalQuestion(
        external_id=str(payload.get("id") or ""),
        channel=Canal.MERCADO_LIVRE,
        text=str(payload.get("text") or ""),
        date_created=dt(payload.get("date_created")) or datetime.now().astimezone(),
        external_listing_id=str(payload.get("item_id") or ""),
        answer_text=str(resposta.get("text") or ""),
        status="answered" if resposta.get("text") else "unanswered",
        asker_external_id=str((payload.get("from") or {}).get("id") or "") or None,
        date_answered=dt(resposta.get("date_created")),
        raw=payload,
    )


def normalizar_reclamacao(payload: dict[str, Any]) -> CanonicalClaim:
    """Converte ``GET /post-purchase/v1/claims/{id}``."""
    return CanonicalClaim(
        external_id=str(payload.get("id") or ""),
        channel=Canal.MERCADO_LIVRE,
        opened_at=dt(payload.get("date_created")) or datetime.now().astimezone(),
        external_order_id=str(payload.get("resource_id") or ""),
        type=str(payload.get("type") or "claim"),
        stage=str(payload.get("stage") or ""),
        status=str(payload.get("status") or "opened"),
        reason_code=str(payload.get("reason_id") or ""),
        resolution=str((payload.get("resolution") or {}).get("reason") or ""),
        closed_at=dt(payload.get("date_closed")),
        raw=payload,
    )


def aplicar_liquido_do_pagamento(
    pedido: CanonicalOrder, pagamentos: list[CanonicalPayment]
) -> CanonicalOrder:
    """Consolida no pedido os valores financeiros vindos do Mercado Pago.

    Quando o MP informa ``net_received_amount``, ele passa a ser o líquido do
    pedido com procedência ``api_reported``. É a fonte mais fiel disponível antes
    da liquidação efetiva.
    """
    if not pagamentos:
        return pedido

    aprovados = [p for p in pagamentos if p.status == StatusPagamento.APROVADO]
    considerados = aprovados or pagamentos

    taxa_pagamento = ZERO
    liquido = ZERO
    for pagamento in considerados:
        taxa_pagamento += sum(
            (f.amount for f in pagamento.fees if f.fee_type != TipoTaxa.COMISSAO_MARKETPLACE),
            ZERO,
        )
        liquido += pagamento.net_received_amount

    pedido.payment_fee = taxa_pagamento
    if liquido > ZERO:
        pedido.net_amount = liquido
        pedido.net_source = FonteLiquido.REPORTADO_API
    return pedido
