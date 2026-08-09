"""Vocabulário canônico do domínio.

Estes valores são a tradução única para a qual todos os marketplaces convergem.
O status original de cada canal continua preservado em ``status_raw`` — nada é
perdido, mas nada acima da camada de conector precisa conhecer o dialeto de
cada plataforma.
"""
from __future__ import annotations

from enum import StrEnum


class Canal(StrEnum):
    MERCADO_LIVRE = "mercadolivre"
    MERCADO_PAGO = "mercadopago"
    SHOPEE = "shopee"


class StatusConta(StrEnum):
    CONECTADA = "connected"
    EXPIRADA = "expired"
    REVOGADA = "revoked"
    ERRO = "error"


class StatusPedido(StrEnum):
    PENDENTE = "pending"
    PAGO = "paid"
    PROCESSANDO = "processing"
    ENVIADO = "shipped"
    ENTREGUE = "delivered"
    CANCELADO = "cancelled"
    DEVOLVIDO = "returned"


#: Status que não contam como receita realizada.
STATUS_NAO_FATURAVEIS = {StatusPedido.CANCELADO}


class StatusEnvio(StrEnum):
    PENDENTE = "pending"
    PRONTO_PARA_ENVIO = "ready_to_ship"
    ENVIADO = "shipped"
    EM_TRANSITO = "in_transit"
    ENTREGUE = "delivered"
    NAO_ENTREGUE = "not_delivered"
    DEVOLVIDO = "returned"
    CANCELADO = "cancelled"


class StatusPagamento(StrEnum):
    PENDENTE = "pending"
    APROVADO = "approved"
    AUTORIZADO = "authorized"
    EM_ANALISE = "in_process"
    REJEITADO = "rejected"
    DEVOLVIDO = "refunded"
    ESTORNADO = "charged_back"
    CANCELADO = "cancelled"


class TipoTaxa(StrEnum):
    """Tipos normalizados de taxa, unificando a nomenclatura dos três canais."""

    COMISSAO_MARKETPLACE = "marketplace_fee"   # sale_fee (ML) / commission_fee (Shopee)
    TAXA_PAGAMENTO = "payment_fee"             # mercadopago_fee / transaction_fee
    TAXA_PARCELAMENTO = "financing_fee"        # financing_fee (MP)
    TAXA_SERVICO = "service_fee"               # service_fee (Shopee)
    TAXA_ENVIO = "shipping_fee"
    TAXA_APLICACAO = "application_fee"
    IMPOSTO = "tax"
    OUTRA = "other"


class FonteLiquido(StrEnum):
    """Procedência do valor líquido — ver ``docs/06-financeiro-conciliacao.md``.

    Misturar estimativa com valor liquidado num mesmo indicador, sem distinguir,
    é o erro que faz o painel divergir do extrato do vendedor.
    """

    CALCULADO = "computed"          # estimado a partir das taxas conhecidas
    REPORTADO_API = "api_reported"  # o canal informou, mas ainda não liberou
    LIQUIDADO = "settled"           # confirmado por repasse/escrow — o dinheiro caiu


class StatusConciliacao(StrEnum):
    CONCILIADO = "matched"
    DIVERGENTE = "divergent"
    AGUARDANDO_REPASSE = "pending_settlement"
    SEM_CORRESPONDENCIA = "unmatched"


class CanalLogistico(StrEnum):
    FULFILLMENT = "fulfillment"        # ML Full
    FLEX = "self_service"              # ML Flex
    CROSS_DOCKING = "cross_docking"    # ML Coleta
    AGENCIA = "drop_off"               # ML Agência/Correios
    SHOPEE_XPRESS = "shopee_xpress"
    OUTRO = "other"


class StatusWebhook(StrEnum):
    PENDENTE = "pending"
    PROCESSANDO = "processing"
    CONCLUIDO = "done"
    FALHOU = "failed"
    MORTO = "dead"  # esgotou as tentativas — fica na DLQ, reprocessável


class PapelUsuario(StrEnum):
    PROPRIETARIO = "owner"
    ADMIN = "admin"
    ANALISTA = "analyst"
    LEITOR = "viewer"


#: Hierarquia usada pela verificação de permissão: um papel atende a exigência
#: de qualquer papel de nível igual ou inferior.
HIERARQUIA_PAPEIS = {
    PapelUsuario.LEITOR: 0,
    PapelUsuario.ANALISTA: 1,
    PapelUsuario.ADMIN: 2,
    PapelUsuario.PROPRIETARIO: 3,
}


class TipoCampanha(StrEnum):
    CUPOM = "voucher"
    DESCONTO = "discount"
    COMBO = "bundle"
    ANUNCIO_PAGO = "ads"
    OFERTA_DO_DIA = "deal_of_day"


class TipoReclamacao(StrEnum):
    RECLAMACAO = "claim"
    MEDIACAO = "mediation"
    DISPUTA = "dispute"
    DEVOLUCAO = "return"
