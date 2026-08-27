"""Margem real de cada pedido: a análise venda-a-venda.

O painel responde "quanto sobrou no período"; esta análise responde a pergunta
que muda decisão: **quais pedidos deram prejuízo e por quê**. Para cada pedido:

    margem = líquido − CMV − Ads − Imposto do vendedor

Três dos quatro números já vivem no pedido, congelados pela ingestão:

- ``net_amount``  — o que o canal pagou (com ``net_source`` dizendo se veio
  liquidado, informado pela API ou calculado);
- ``cogs``        — custo dos produtos, congelado no momento da venda;
- ``sales_tax_amount`` — imposto do regime do vendedor (Simples progressivo,
  com vigência), apurado por ``services/taxes.py``. Ele não sai do repasse do
  canal — é pago depois, à Receita — e é exatamente por isso que entra aqui:
  ignorá-lo faria todo pedido parecer mais lucrativo do que é.

O quarto — **Ads** — nenhuma API entrega por pedido. O investimento lançado em
``AdSpend`` é rateado entre os pedidos da competência proporcionalmente à
receita bruta, pelo lançamento mais específico que casar: anúncio, depois SKU,
depois o canal inteiro. Um pedido nunca recebe verba de dois lançamentos.

ACOS e TACOS são coisas distintas — e a diferença é o motivo de existir a
coluna ``attributed_revenue``:

- ACOS  = investimento ÷ receita **que o canal atribuiu à publicidade**. Só o
  relatório de Ads sabe esse número; sem ele, ACOS é indeterminado ("—").
- TACOS = investimento ÷ receita **total** do pedido. Sempre calculável.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import StatusPedido
from app.models.marketing import AdSpend, EscopoAds
from app.models.order import Order, OrderItem
from app.services.analytics import Filtro
from app.services.finance import arredondar

log = structlog.get_logger(__name__)

ZERO = Decimal("0")
CEM = Decimal("100")
#: Tolerância da conferência do líquido: um centavo de arredondamento do canal.
TOLERANCIA = Decimal("0.01")

# --- Recortes (os "chips" da tela) ----------------------------------------- #
RECORTE_TODOS = "todos"
RECORTE_NEGATIVOS = "negativos"
RECORTE_SEM_CUSTO = "sem-custo"
RECORTE_SEM_COMISSAO = "sem-comissao"
RECORTE_SEM_FRETE = "sem-frete"
RECORTE_PACOTES = "pacotes"
RECORTE_REVISAR = "revisar"

RECORTES = (
    RECORTE_TODOS, RECORTE_NEGATIVOS, RECORTE_SEM_CUSTO, RECORTE_SEM_COMISSAO,
    RECORTE_SEM_FRETE, RECORTE_PACOTES, RECORTE_REVISAR,
)

# --- Ordenações ------------------------------------------------------------- #
ORDEM_PIOR_MARGEM_VALOR = "pior-margem-valor"
ORDEM_PIOR_MARGEM_PCT = "pior-margem-pct"
ORDEM_MELHOR_MARGEM_VALOR = "melhor-margem-valor"
ORDEM_MELHOR_MARGEM_PCT = "melhor-margem-pct"
ORDEM_MAIOR_VENDA = "maior-venda"
ORDEM_MAIOR_FRETE = "maior-frete"
ORDEM_DATA = "data"

ORDENACOES = (
    ORDEM_PIOR_MARGEM_VALOR, ORDEM_PIOR_MARGEM_PCT, ORDEM_MELHOR_MARGEM_VALOR,
    ORDEM_MELHOR_MARGEM_PCT, ORDEM_MAIOR_VENDA, ORDEM_MAIOR_FRETE, ORDEM_DATA,
)

# --- Alertas de qualidade do dado ------------------------------------------ #
ALERTA_SEM_SKU = "sem_sku"
ALERTA_SEM_CUSTO = "sem_custo"
ALERTA_SEM_COMISSAO = "sem_comissao"
ALERTA_LIQUIDO_DIVERGE = "liquido_diverge"

ALERTAS = (ALERTA_SEM_SKU, ALERTA_SEM_CUSTO, ALERTA_SEM_COMISSAO, ALERTA_LIQUIDO_DIVERGE)

TAMANHO_PAGINA_PADRAO = 50
TAMANHO_PAGINA_MAX = 200


def _d(valor: Any) -> Decimal:
    if valor is None:
        return ZERO
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def _pct(numerador: Decimal, base: Decimal) -> Decimal | None:
    """Percentual sobre a base; ``None`` quando não há base.

    Sem receita não existe percentual — devolver 0 faria um pedido de receita
    zero aparecer como margem neutra, escondendo o caso a investigar.
    """
    if base == ZERO:
        return None
    return arredondar(numerador / base * CEM)


# --------------------------------------------------------------------------- #
# Análise de um pedido                                                          #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class PedidoAnalisado:
    """Um pedido com os quatro componentes da margem resolvidos."""

    pedido: Order
    ads: Decimal = ZERO
    acos_pct: Decimal | None = None

    @property
    def receita(self) -> Decimal:
        return _d(self.pedido.gross_amount)

    @property
    def liquido(self) -> Decimal:
        return _d(self.pedido.net_amount)

    @property
    def cmv(self) -> Decimal:
        return _d(self.pedido.cogs)

    @property
    def imposto(self) -> Decimal:
        return _d(self.pedido.sales_tax_amount)

    @property
    def comissao(self) -> Decimal:
        """Comissão + taxa de pagamento: o custo de vender pelo canal."""
        return _d(self.pedido.platform_fee) + _d(self.pedido.payment_fee)

    @property
    def frete(self) -> Decimal:
        """Custo de envio pago pelo vendedor (o repasse do comprador não abate)."""
        return _d(self.pedido.shipping_cost)

    @property
    def margem_valor(self) -> Decimal:
        return arredondar(self.liquido - self.cmv - self.ads - self.imposto)

    @property
    def margem_pct(self) -> Decimal | None:
        return _pct(self.margem_valor, self.receita)

    @property
    def tacos_pct(self) -> Decimal | None:
        return _pct(self.ads, self.receita)

    @property
    def diferenca_liquido(self) -> Decimal:
        """Reconstrução do líquido a partir dos componentes, menos o informado.

        Espelha ``finance.calcular_liquido``: bruto + frete cobrado − comissão −
        taxa de pagamento − frete pago − imposto retido + descontos − reembolsos.
        Divergência acima da tolerância marca o pedido para revisão — ou o canal
        lançou uma tarifa que não veio discriminada, ou a ingestão perdeu algo.
        """
        p = self.pedido
        reconstruido = (
            _d(p.gross_amount)
            + _d(p.shipping_revenue)
            - _d(p.platform_fee)
            - _d(p.payment_fee)
            - _d(p.shipping_cost)
            - _d(p.tax_amount)
            + _d(p.discount_amount)
            - _d(p.refund_amount)
        )
        return arredondar(reconstruido - self.liquido)

    @property
    def skus(self) -> list[str]:
        vistos: list[str] = []
        for item in self.pedido.items:
            sku = item.sku_base or item.sku_channel
            if sku and sku not in vistos:
                vistos.append(sku)
        return vistos

    @property
    def titulo(self) -> str:
        return self.pedido.items[0].title if self.pedido.items else ""

    @property
    def alertas(self) -> list[str]:
        problemas: list[str] = []
        if any(not (i.sku_base or "").strip() for i in self.pedido.items) or not self.pedido.items:
            problemas.append(ALERTA_SEM_SKU)
        if self.cmv == ZERO:
            problemas.append(ALERTA_SEM_CUSTO)
        if self.comissao == ZERO:
            problemas.append(ALERTA_SEM_COMISSAO)
        if abs(self.diferenca_liquido) > TOLERANCIA:
            problemas.append(ALERTA_LIQUIDO_DIVERGE)
        return problemas


# --------------------------------------------------------------------------- #
# Rateio de publicidade                                                         #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Bucket:
    valor: Decimal
    receita_ads: Decimal | None
    pedidos: list[PedidoAnalisado] = field(default_factory=list)


async def ratear_ads(
    db: AsyncSession, tenant_id: int, analisados: Sequence[PedidoAnalisado]
) -> Decimal:
    """Distribui os lançamentos de ``AdSpend`` entre os pedidos.

    Cada pedido é coberto por **um só** lançamento — o mais específico que
    casar: anúncio (id externo do listing), depois SKU, depois o canal inteiro.
    O rateio é proporcional à receita bruta, com o resto de arredondamento no
    maior pedido, para Σ ads == valor lançado.

    Devolve o total **não alocado**: lançamento de competência em que aquele
    anúncio/SKU não vendeu, ou cujos pedidos somam receita zero. Esse resto
    aparece no resumo em vez de sumir — se for alto, a margem por pedido está
    subestimando o custo de publicidade.
    """
    linhas = (
        await db.execute(select(AdSpend).where(AdSpend.tenant_id == tenant_id))
    ).scalars().all()
    if not linhas:
        return ZERO

    buckets: dict[tuple, _Bucket] = {
        (a.channel, a.year, a.month, a.scope, a.reference or ""): _Bucket(
            valor=_d(a.amount),
            receita_ads=_d(a.attributed_revenue) if a.attributed_revenue is not None else None,
        )
        for a in linhas
    }

    for analisado in analisados:
        pedido = analisado.pedido
        quando = pedido.date_created
        if quando is None:
            continue
        chave_base = (pedido.channel, quando.year, quando.month)
        bucket = None
        for item in pedido.items:
            listing_externo = _listing_externo(item)
            if listing_externo:
                bucket = buckets.get((*chave_base, EscopoAds.ANUNCIO, listing_externo))
                if bucket is not None:
                    break
        if bucket is None:
            for item in pedido.items:
                if item.sku_base:
                    bucket = buckets.get((*chave_base, EscopoAds.SKU, item.sku_base))
                    if bucket is not None:
                        break
        if bucket is None:
            bucket = buckets.get((*chave_base, EscopoAds.CANAL, ""))
        if bucket is not None:
            bucket.pedidos.append(analisado)

    nao_alocado = ZERO
    for bucket in buckets.values():
        base = sum((a.receita for a in bucket.pedidos), ZERO)
        if base <= ZERO:
            nao_alocado += bucket.valor
            continue

        acos = None
        if bucket.receita_ads is not None and bucket.receita_ads > ZERO:
            acos = arredondar(bucket.valor / bucket.receita_ads * CEM)

        distribuido = ZERO
        for analisado in bucket.pedidos:
            parte = arredondar(bucket.valor * analisado.receita / base)
            analisado.ads += parte
            analisado.acos_pct = acos
            distribuido += parte
        sobra = arredondar(bucket.valor - distribuido)
        if sobra != ZERO:
            maior = max(bucket.pedidos, key=lambda a: a.receita)
            maior.ads += sobra

    return arredondar(nao_alocado)


def _listing_externo(item: OrderItem) -> str:
    """Id externo do anúncio, quando a ingestão o guardou no item."""
    return (item.external_item_id or "").strip()


# --------------------------------------------------------------------------- #
# Recorte, ordenação e serialização                                             #
# --------------------------------------------------------------------------- #


def _casa_recorte(a: PedidoAnalisado, recorte: str) -> bool:
    if recorte == RECORTE_NEGATIVOS:
        return a.margem_valor < ZERO
    if recorte == RECORTE_SEM_CUSTO:
        return a.cmv == ZERO
    if recorte == RECORTE_SEM_COMISSAO:
        return a.comissao == ZERO
    if recorte == RECORTE_SEM_FRETE:
        return a.frete == ZERO
    if recorte == RECORTE_PACOTES:
        return bool(a.pedido.has_multiple_items)
    if recorte == RECORTE_REVISAR:
        return bool(a.alertas)
    return True


def _ordenar(analisados: list[PedidoAnalisado], ordem: str) -> list[PedidoAnalisado]:
    """Percentuais nulos vão para o fim em qualquer sentido: um pedido sem
    receita não tem margem % e não pode encabeçar a lista de piores."""

    def pct_asc(a: PedidoAnalisado):
        v = a.margem_pct
        return (v is None, v if v is not None else ZERO)

    def pct_desc(a: PedidoAnalisado):
        v = a.margem_pct
        return (v is None, -(v if v is not None else ZERO))

    if ordem == ORDEM_PIOR_MARGEM_VALOR:
        return sorted(analisados, key=lambda a: a.margem_valor)
    if ordem == ORDEM_MELHOR_MARGEM_VALOR:
        return sorted(analisados, key=lambda a: a.margem_valor, reverse=True)
    if ordem == ORDEM_PIOR_MARGEM_PCT:
        return sorted(analisados, key=pct_asc)
    if ordem == ORDEM_MELHOR_MARGEM_PCT:
        return sorted(analisados, key=pct_desc)
    if ordem == ORDEM_MAIOR_VENDA:
        return sorted(analisados, key=lambda a: a.receita, reverse=True)
    if ordem == ORDEM_MAIOR_FRETE:
        return sorted(analisados, key=lambda a: a.frete, reverse=True)
    return sorted(
        analisados,
        key=lambda a: a.pedido.date_created,
        reverse=True,
    )


def _serializar(a: PedidoAnalisado) -> dict[str, Any]:
    p = a.pedido
    margem_pct = a.margem_pct
    tacos = a.tacos_pct
    return {
        "id": p.id,
        "external_id": p.external_id,
        "channel": p.channel,
        "date_created": p.date_created.isoformat() if p.date_created else None,
        "status": p.status,
        "titulo": a.titulo,
        "skus": a.skus,
        "logistic_type": p.logistic_type,
        "has_multiple_items": bool(p.has_multiple_items),
        "itens": len(p.items),
        "total": str(arredondar(a.receita)),
        "custo": str(arredondar(a.cmv)),
        "frete": str(arredondar(a.frete)),
        "comissao": str(arredondar(a.comissao)),
        "liquido": str(arredondar(a.liquido)),
        "net_source": p.net_source,
        "ads": str(arredondar(a.ads)),
        "acos_pct": str(a.acos_pct) if a.acos_pct is not None else None,
        "tacos_pct": str(tacos) if tacos is not None else None,
        "imposto": str(arredondar(a.imposto)),
        "margem_valor": str(a.margem_valor),
        "margem_pct": str(margem_pct) if margem_pct is not None else None,
        "diferenca_liquido": str(a.diferenca_liquido),
        "alertas": a.alertas,
    }


def _bate_busca(a: PedidoAnalisado, alvo: str) -> bool:
    campos = [a.pedido.external_id, a.titulo, *a.skus]
    return any(alvo in (campo or "").lower() for campo in campos)


def _resumo(analisados: Sequence[PedidoAnalisado], ads_nao_alocado: Decimal) -> dict[str, Any]:
    total = len(analisados)
    negativos = sum(1 for a in analisados if a.margem_valor < ZERO)
    revisar = sum(1 for a in analisados if a.alertas)
    receita = sum((a.receita for a in analisados), ZERO)
    margem = sum((a.margem_valor for a in analisados), ZERO)
    return {
        "pedidos": total,
        "negativos": negativos,
        "pct_negativos": str(_pct(Decimal(negativos), Decimal(total)) or ZERO),
        "para_revisar": revisar,
        "total": str(arredondar(receita)),
        "custo": str(arredondar(sum((a.cmv for a in analisados), ZERO))),
        "frete": str(arredondar(sum((a.frete for a in analisados), ZERO))),
        "comissao": str(arredondar(sum((a.comissao for a in analisados), ZERO))),
        "ads": str(arredondar(sum((a.ads for a in analisados), ZERO))),
        "imposto": str(arredondar(sum((a.imposto for a in analisados), ZERO))),
        "liquido": str(arredondar(sum((a.liquido for a in analisados), ZERO))),
        "margem_valor": str(arredondar(margem)),
        "margem_pct": str(_pct(margem, receita) or ZERO),
        "prejuizo_dos_negativos": str(
            arredondar(sum((a.margem_valor for a in analisados if a.margem_valor < ZERO), ZERO))
        ),
        "ads_nao_alocado": str(ads_nao_alocado),
    }


# --------------------------------------------------------------------------- #
# Entrada principal                                                             #
# --------------------------------------------------------------------------- #


async def analisar(
    db: AsyncSession,
    filtro: Filtro,
    *,
    recorte: str = RECORTE_TODOS,
    ordem: str = ORDEM_DATA,
    busca: str | None = None,
    pagina: int = 1,
    tamanho: int = TAMANHO_PAGINA_PADRAO,
    incluir_cancelados: bool = False,
) -> dict[str, Any]:
    """Página da análise de margem por pedido, com resumo e contagens.

    Carrega os pedidos do período em memória de uma vez: recortes como
    "margem negativa" dependem do rateio de Ads, que precisa do conjunto
    inteiro para ser proporcional — filtrar no SQL retornaria proporções
    erradas. O filtro de período é quem limita o volume.
    """
    recorte = recorte if recorte in RECORTES else RECORTE_TODOS
    ordem = ordem if ordem in ORDENACOES else ORDEM_DATA
    pagina = max(1, pagina)
    tamanho = max(1, min(tamanho, TAMANHO_PAGINA_MAX))

    consulta = filtro.aplicar(
        select(Order).options(selectinload(Order.items))
    )
    if not incluir_cancelados:
        consulta = consulta.where(Order.status != StatusPedido.CANCELADO)

    pedidos = (await db.execute(consulta)).scalars().unique().all()
    analisados = [PedidoAnalisado(pedido=p) for p in pedidos]
    ads_nao_alocado = await ratear_ads(db, filtro.tenant_id, analisados)

    if busca:
        alvo = busca.strip().lower()
        analisados = [a for a in analisados if _bate_busca(a, alvo)]

    contagem = {r: sum(1 for a in analisados if _casa_recorte(a, r)) for r in RECORTES}

    filtrados = [a for a in analisados if _casa_recorte(a, recorte)]
    ordenados = _ordenar(filtrados, ordem)

    total = len(ordenados)
    inicio = (pagina - 1) * tamanho
    fim = min(inicio + tamanho, total)
    janela = ordenados[inicio:fim] if inicio < total else []

    return {
        "filtros": {
            "recorte": recorte,
            "ordem": ordem,
            "busca": busca or None,
            "incluir_cancelados": incluir_cancelados,
        },
        "resumo": _resumo(filtrados, ads_nao_alocado),
        "contagem_por_recorte": contagem,
        "paginacao": {
            "pagina": pagina,
            "tamanho": tamanho,
            "total": total,
            "paginas": (total + tamanho - 1) // tamanho if total else 0,
            "de": inicio + 1 if janela else 0,
            "ate": fim,
        },
        "pedidos": [_serializar(a) for a in janela],
    }
