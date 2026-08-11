"""Consultas analíticas e rollups que alimentam o painel.

Estratégia de leitura em três camadas (ver ``docs/07-dashboards-metricas.md``):
janela curta consulta ``orders`` direto pelo índice parcial; janelas médias e
longas leem os rollups pré-agregados. Um dashboard que soma milhões de linhas a
cada F5 não escala, e o usuário sente isso antes de qualquer métrica de servidor
acusar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import StatusPedido
from app.models.metrics import MetricDaily, MetricHourly
from app.models.order import Order, OrderItem
from app.services.finance import arredondar

log = structlog.get_logger(__name__)
ZERO = Decimal("0")


@dataclass(slots=True)
class Filtro:
    """Filtros globais compartilhados por todas as abas do painel."""

    tenant_id: int
    inicio: datetime
    fim: datetime
    channel: str | None = None
    account_id: int | None = None
    status: str | None = None
    logistic_type: str | None = None
    sku: str | None = None
    state: str | None = None

    def aplicar(self, consulta: Select) -> Select:
        consulta = consulta.where(
            Order.tenant_id == self.tenant_id,
            Order.date_created >= self.inicio,
            Order.date_created <= self.fim,
        )
        if self.channel:
            consulta = consulta.where(Order.channel == self.channel)
        if self.account_id:
            consulta = consulta.where(Order.channel_account_id == self.account_id)
        if self.status:
            consulta = consulta.where(Order.status == self.status)
        if self.logistic_type:
            consulta = consulta.where(Order.logistic_type == self.logistic_type)
        if self.state:
            consulta = consulta.where(Order.ship_state == self.state)
        return consulta

    @property
    def dias(self) -> int:
        return max(1, (self.fim - self.inicio).days)


async def visao_geral(db: AsyncSession, filtro: Filtro) -> dict[str, Any]:
    """KPIs da aba Visão Geral, com comparação contra o período anterior."""
    atual = await _agregar(db, filtro)

    duracao = filtro.fim - filtro.inicio
    anterior = await _agregar(
        db,
        Filtro(
            tenant_id=filtro.tenant_id,
            inicio=filtro.inicio - duracao,
            fim=filtro.inicio,
            channel=filtro.channel,
            account_id=filtro.account_id,
        ),
    )

    contagens = await _contagem_por_status(db, filtro)
    validos = atual["pedidos"]
    bruto = atual["receita_bruta"]

    return {
        "periodo": {"inicio": filtro.inicio, "fim": filtro.fim, "dias": filtro.dias},
        "kpis": {
            "pedidos": _kpi(validos, anterior["pedidos"]),
            "unidades": _kpi(atual["unidades"], anterior["unidades"]),
            "receita_bruta": _kpi(bruto, anterior["receita_bruta"]),
            "receita_liquida": _kpi(atual["receita_liquida"], anterior["receita_liquida"]),
            "taxas": _kpi(atual["taxas"], anterior["taxas"]),
            "frete": _kpi(atual["frete"], anterior["frete"]),
            "cmv": _kpi(atual["cmv"], anterior["cmv"]),
            "ticket_medio": _kpi(
                arredondar(bruto / validos) if validos else ZERO,
                arredondar(anterior["receita_bruta"] / anterior["pedidos"])
                if anterior["pedidos"]
                else ZERO,
            ),
            "cancelados": _kpi(atual["cancelados"], anterior["cancelados"]),
        },
        "derivados": {
            "taxa_efetiva_pct": str(arredondar(atual["taxas"] / bruto * 100)) if bruto else "0.00",
            "margem_contribuicao": str(
                arredondar(atual["receita_liquida"] - atual["cmv"])
            ),
            "margem_pct": str(
                arredondar((atual["receita_liquida"] - atual["cmv"]) / bruto * 100)
            )
            if bruto
            else "0.00",
            "taxa_cancelamento_pct": str(
                arredondar(
                    Decimal(atual["cancelados"]) / (validos + atual["cancelados"]) * 100
                )
            )
            if (validos + atual["cancelados"])
            else "0.00",
        },
        "por_status": contagens,
    }


async def _agregar(db: AsyncSession, filtro: Filtro) -> dict[str, Any]:
    """Uma única query com todos os somatórios do período."""
    consulta = filtro.aplicar(
        select(
            func.count(Order.id).filter(Order.status != StatusPedido.CANCELADO),
            func.coalesce(
                func.sum(Order.gross_amount).filter(Order.status != StatusPedido.CANCELADO), 0
            ),
            func.coalesce(
                func.sum(Order.net_amount).filter(Order.status != StatusPedido.CANCELADO), 0
            ),
            func.coalesce(
                func.sum(Order.platform_fee + Order.payment_fee).filter(
                    Order.status != StatusPedido.CANCELADO
                ),
                0,
            ),
            func.coalesce(
                func.sum(Order.shipping_cost).filter(Order.status != StatusPedido.CANCELADO), 0
            ),
            func.coalesce(
                func.sum(Order.cogs).filter(Order.status != StatusPedido.CANCELADO), 0
            ),
            func.count(Order.id).filter(Order.status == StatusPedido.CANCELADO),
            func.coalesce(
                func.sum(Order.gross_amount).filter(Order.status == StatusPedido.CANCELADO), 0
            ),
        )
    )
    linha = (await db.execute(consulta)).one()

    unidades = await db.scalar(
        filtro.aplicar(
            select(func.coalesce(func.sum(OrderItem.quantity), 0)).join(
                Order, Order.id == OrderItem.order_id
            )
        ).where(Order.status != StatusPedido.CANCELADO)
    )

    return {
        "pedidos": int(linha[0] or 0),
        "receita_bruta": _d(linha[1]),
        "receita_liquida": _d(linha[2]),
        "taxas": _d(linha[3]),
        "frete": _d(linha[4]),
        "cmv": _d(linha[5]),
        "cancelados": int(linha[6] or 0),
        "valor_cancelado": _d(linha[7]),
        "unidades": _d(unidades),
    }


async def _contagem_por_status(db: AsyncSession, filtro: Filtro) -> dict[str, int]:
    linhas = (
        await db.execute(
            filtro.aplicar(select(Order.status, func.count(Order.id))).group_by(Order.status)
        )
    ).all()
    contagens = {str(s): int(q) for s, q in linhas}
    # Garante todas as chaves: o frontend não deve lidar com ausência de chave.
    for status in StatusPedido:
        contagens.setdefault(str(status.value), 0)
    return contagens


def _kpi(atual: Any, anterior: Any) -> dict[str, Any]:
    """Valor com variação percentual contra o período anterior."""
    a = _d(atual)
    b = _d(anterior)
    variacao = arredondar((a - b) / b * 100) if b else (Decimal("100") if a else ZERO)
    return {
        "valor": str(arredondar(a)) if isinstance(atual, (Decimal, float)) else atual,
        "anterior": str(arredondar(b)) if isinstance(anterior, (Decimal, float)) else anterior,
        "variacao_pct": str(variacao),
    }


async def serie_temporal(
    db: AsyncSession, filtro: Filtro, granularidade: str = "day"
) -> list[dict[str, Any]]:
    """Série de receita bruta, líquida e pedidos.

    Para janelas longas lê o rollup diário; para curtas vai direto na tabela de
    pedidos, que com o índice parcial responde em milissegundos.
    """
    if granularidade == "day" and filtro.dias > 90:
        return await _serie_do_rollup(db, filtro)

    formato = {"hour": "%Y-%m-%dT%H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}.get(
        granularidade, "%Y-%m-%d"
    )
    dialeto = db.bind.dialect.name if db.bind else "postgresql"

    if dialeto == "postgresql":
        pg = {"hour": "YYYY-MM-DD\"T\"HH24:00", "day": "YYYY-MM-DD", "month": "YYYY-MM"}
        balde = func.to_char(Order.date_created, pg.get(granularidade, "YYYY-MM-DD"))
    else:
        balde = func.strftime(formato, Order.date_created)

    linhas = (
        await db.execute(
            filtro.aplicar(
                select(
                    balde.label("balde"),
                    func.count(Order.id).filter(Order.status != StatusPedido.CANCELADO),
                    func.coalesce(
                        func.sum(Order.gross_amount).filter(
                            Order.status != StatusPedido.CANCELADO
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(Order.net_amount).filter(Order.status != StatusPedido.CANCELADO),
                        0,
                    ),
                    func.count(Order.id).filter(Order.status == StatusPedido.CANCELADO),
                )
            )
            .group_by("balde")
            .order_by("balde")
        )
    ).all()

    return [
        {
            "bucket": str(b),
            "pedidos": int(p or 0),
            "receita_bruta": str(arredondar(_d(bruto))),
            "receita_liquida": str(arredondar(_d(liquido))),
            "cancelados": int(c or 0),
        }
        for b, p, bruto, liquido, c in linhas
    ]


async def _serie_do_rollup(db: AsyncSession, filtro: Filtro) -> list[dict[str, Any]]:
    consulta = select(
        MetricDaily.day,
        func.sum(MetricDaily.orders_count),
        func.sum(MetricDaily.gross_amount),
        func.sum(MetricDaily.net_amount),
        func.sum(MetricDaily.cancelled_count),
    ).where(
        MetricDaily.tenant_id == filtro.tenant_id,
        MetricDaily.day >= filtro.inicio.date(),
        MetricDaily.day <= filtro.fim.date(),
        MetricDaily.channel_account_id == (filtro.account_id or 0),
    )
    if filtro.channel:
        consulta = consulta.where(MetricDaily.channel == filtro.channel)

    linhas = (await db.execute(consulta.group_by(MetricDaily.day).order_by(MetricDaily.day))).all()
    return [
        {
            "bucket": dia.isoformat(),
            "pedidos": int(p or 0),
            "receita_bruta": str(arredondar(_d(bruto))),
            "receita_liquida": str(arredondar(_d(liquido))),
            "cancelados": int(c or 0),
        }
        for dia, p, bruto, liquido, c in linhas
    ]


async def por_canal(db: AsyncSession, filtro: Filtro) -> list[dict[str, Any]]:
    """Comparativo entre marketplaces."""
    linhas = (
        await db.execute(
            filtro.aplicar(
                select(
                    Order.channel,
                    func.count(Order.id).filter(Order.status != StatusPedido.CANCELADO),
                    func.coalesce(
                        func.sum(Order.gross_amount).filter(
                            Order.status != StatusPedido.CANCELADO
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(Order.net_amount).filter(Order.status != StatusPedido.CANCELADO),
                        0,
                    ),
                    func.coalesce(
                        func.sum(Order.platform_fee + Order.payment_fee).filter(
                            Order.status != StatusPedido.CANCELADO
                        ),
                        0,
                    ),
                )
            )
            .group_by(Order.channel)
            .order_by(func.sum(Order.gross_amount).desc())
        )
    ).all()

    return [
        {
            "channel": str(canal),
            "pedidos": int(qtd or 0),
            "receita_bruta": str(arredondar(_d(bruto))),
            "receita_liquida": str(arredondar(_d(liquido))),
            "taxas": str(arredondar(_d(taxas))),
            "ticket_medio": str(arredondar(_d(bruto) / qtd)) if qtd else "0.00",
            "taxa_efetiva_pct": str(arredondar(_d(taxas) / _d(bruto) * 100))
            if _d(bruto)
            else "0.00",
        }
        for canal, qtd, bruto, liquido, taxas in linhas
    ]


async def ranking_produtos(
    db: AsyncSession, filtro: Filtro, *, limite: int = 20
) -> list[dict[str, Any]]:
    """Ranking por SKU base — consolidado entre canais.

    A consolidação por ``sku_base`` é o diferencial: o mesmo produto vendido em
    quatro anúncios do Mercado Livre e dois da Shopee, com códigos diferentes em
    cada canal, aparece como uma linha só. Nenhum painel nativo faz isso, porque
    nenhum enxerga os outros canais.
    """
    chave = func.coalesce(OrderItem.sku_base, OrderItem.sku_channel)
    linhas = (
        await db.execute(
            filtro.aplicar(
                select(
                    chave.label("sku"),
                    func.min(OrderItem.title),
                    func.sum(OrderItem.quantity),
                    func.sum(OrderItem.gross_amount),
                    func.sum(OrderItem.cogs),
                    func.count(func.distinct(Order.id)),
                ).join(Order, Order.id == OrderItem.order_id)
            )
            .where(Order.status != StatusPedido.CANCELADO)
            .group_by("sku")
            .order_by(func.sum(OrderItem.gross_amount).desc())
            .limit(limite)
        )
    ).all()

    return [
        {
            "sku": str(sku or "—"),
            "titulo": str(titulo or ""),
            "unidades": str(_d(unidades)),
            "receita_bruta": str(arredondar(_d(receita))),
            "cmv": str(arredondar(_d(cmv))),
            "margem_bruta": str(arredondar(_d(receita) - _d(cmv))),
            "margem_pct": str(arredondar((_d(receita) - _d(cmv)) / _d(receita) * 100))
            if _d(receita)
            else "—",
            "pedidos": int(pedidos or 0),
        }
        for sku, titulo, unidades, receita, cmv, pedidos in linhas
    ]


async def por_estado(db: AsyncSession, filtro: Filtro) -> list[dict[str, Any]]:
    """Distribuição geográfica (cidade/estado — o que as APIs permitem)."""
    linhas = (
        await db.execute(
            filtro.aplicar(
                select(
                    Order.ship_state,
                    func.count(Order.id),
                    func.coalesce(func.sum(Order.gross_amount), 0),
                )
            )
            .where(Order.status != StatusPedido.CANCELADO, Order.ship_state != "")
            .group_by(Order.ship_state)
            .order_by(func.sum(Order.gross_amount).desc())
        )
    ).all()
    return [
        {
            "estado": str(uf),
            "pedidos": int(qtd or 0),
            "receita_bruta": str(arredondar(_d(receita))),
        }
        for uf, qtd, receita in linhas
    ]


async def volume_por_minuto(
    db: AsyncSession, tenant_id: int, *, minutos: int = 60
) -> list[dict[str, Any]]:
    """Série do painel ao vivo: pedidos por minuto na última hora.

    Usa o índice parcial de 7 dias, por isso responde em milissegundos mesmo
    com milhões de pedidos históricos na tabela.
    """
    desde = datetime.now(UTC) - timedelta(minutes=minutos)
    dialeto = db.bind.dialect.name if db.bind else "postgresql"
    balde = (
        func.to_char(Order.date_created, "YYYY-MM-DD\"T\"HH24:MI")
        if dialeto == "postgresql"
        else func.strftime("%Y-%m-%dT%H:%M", Order.date_created)
    )

    linhas = (
        await db.execute(
            select(balde.label("balde"), func.count(Order.id), func.sum(Order.gross_amount))
            .where(Order.tenant_id == tenant_id, Order.date_created >= desde)
            .group_by("balde")
            .order_by("balde")
        )
    ).all()
    return [
        {"bucket": str(b), "pedidos": int(q or 0), "receita": str(arredondar(_d(v)))}
        for b, q, v in linhas
    ]


async def mapa_de_calor(db: AsyncSession, filtro: Filtro) -> list[dict[str, Any]]:
    """Distribuição hora × dia da semana — orienta horário de campanha."""
    dialeto = db.bind.dialect.name if db.bind else "postgresql"
    if dialeto == "postgresql":
        dia_semana = func.extract("dow", Order.date_created)
        hora = func.extract("hour", Order.date_created)
    else:
        dia_semana = func.cast(func.strftime("%w", Order.date_created), func.INTEGER())
        hora = func.cast(func.strftime("%H", Order.date_created), func.INTEGER())

    linhas = (
        await db.execute(
            filtro.aplicar(
                select(dia_semana.label("dow"), hora.label("h"), func.count(Order.id))
            )
            .where(Order.status != StatusPedido.CANCELADO)
            .group_by("dow", "h")
        )
    ).all()
    return [
        {"dia_semana": int(d or 0), "hora": int(h or 0), "pedidos": int(q or 0)}
        for d, h, q in linhas
    ]


# --- Rollups -----------------------------------------------------------------

async def recalcular_rollups(
    db: AsyncSession, tenant_id: int, *, horas: int = 3
) -> dict[str, int]:
    """Recalcula os rollups afetados.

    Só reprocessa os *buckets* recentes: recalcular a série histórica inteira a
    cada execução gastaria minutos de banco para reescrever números idênticos.
    """
    desde = datetime.now(UTC) - timedelta(hours=horas)
    horas_gravadas = await _rollup_horario(db, tenant_id, desde)
    dias_gravados = await _rollup_diario(db, tenant_id, desde.date())
    await db.commit()
    return {"horas": horas_gravadas, "dias": dias_gravados}


async def _rollup_horario(db: AsyncSession, tenant_id: int, desde: datetime) -> int:
    dialeto = db.bind.dialect.name if db.bind else "postgresql"
    balde = (
        func.date_trunc("hour", Order.date_created)
        if dialeto == "postgresql"
        else func.strftime("%Y-%m-%d %H:00:00", Order.date_created)
    )

    linhas = (
        await db.execute(
            select(
                balde.label("balde"),
                Order.channel,
                func.count(Order.id).filter(Order.status != StatusPedido.CANCELADO),
                func.coalesce(
                    func.sum(Order.gross_amount).filter(Order.status != StatusPedido.CANCELADO), 0
                ),
                func.coalesce(
                    func.sum(Order.net_amount).filter(Order.status != StatusPedido.CANCELADO), 0
                ),
                func.coalesce(
                    func.sum(Order.platform_fee + Order.payment_fee).filter(
                        Order.status != StatusPedido.CANCELADO
                    ),
                    0,
                ),
                func.count(Order.id).filter(Order.status == StatusPedido.CANCELADO),
            )
            .where(Order.tenant_id == tenant_id, Order.date_created >= desde)
            .group_by("balde", Order.channel)
        )
    ).all()

    gravados = 0
    for balde_valor, canal, pedidos, bruto, liquido, taxas, cancelados in linhas:
        momento = _para_datetime(balde_valor)
        existente = await db.scalar(
            select(MetricHourly).where(
                MetricHourly.tenant_id == tenant_id,
                MetricHourly.channel_account_id == 0,
                MetricHourly.channel == str(canal or ""),
                MetricHourly.bucket == momento,
            )
        )
        alvo = existente or MetricHourly(
            tenant_id=tenant_id,
            channel_account_id=0,
            channel=str(canal or ""),
            bucket=momento,
        )
        alvo.orders_count = int(pedidos or 0)
        alvo.gross_amount = _d(bruto)
        alvo.net_amount = _d(liquido)
        alvo.fees_amount = _d(taxas)
        alvo.cancelled_count = int(cancelados or 0)
        if existente is None:
            db.add(alvo)
        gravados += 1
    return gravados


async def _rollup_diario(db: AsyncSession, tenant_id: int, desde: date) -> int:
    dialeto = db.bind.dialect.name if db.bind else "postgresql"
    balde = (
        func.date(Order.date_created)
        if dialeto == "postgresql"
        else func.strftime("%Y-%m-%d", Order.date_created)
    )

    linhas = (
        await db.execute(
            select(
                balde.label("dia"),
                func.count(Order.id).filter(Order.status != StatusPedido.CANCELADO),
                func.coalesce(
                    func.sum(Order.gross_amount).filter(Order.status != StatusPedido.CANCELADO), 0
                ),
                func.coalesce(
                    func.sum(Order.net_amount).filter(Order.status != StatusPedido.CANCELADO), 0
                ),
                func.coalesce(
                    func.sum(Order.platform_fee + Order.payment_fee).filter(
                        Order.status != StatusPedido.CANCELADO
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(Order.cogs).filter(Order.status != StatusPedido.CANCELADO), 0
                ),
                func.count(Order.id).filter(Order.status == StatusPedido.CANCELADO),
                func.coalesce(
                    func.sum(Order.gross_amount).filter(Order.status == StatusPedido.CANCELADO), 0
                ),
            )
            .where(Order.tenant_id == tenant_id, func.date(Order.date_created) >= desde)
            .group_by("dia")
        )
    ).all()

    gravados = 0
    for dia_valor, pedidos, bruto, liquido, taxas, cmv, cancelados, valor_cancelado in linhas:
        dia = _para_date(dia_valor)
        existente = await db.scalar(
            select(MetricDaily).where(
                MetricDaily.tenant_id == tenant_id,
                MetricDaily.channel_account_id == 0,
                MetricDaily.day == dia,
            )
        )
        alvo = existente or MetricDaily(tenant_id=tenant_id, channel_account_id=0, day=dia)
        alvo.orders_count = int(pedidos or 0)
        alvo.gross_amount = _d(bruto)
        alvo.net_amount = _d(liquido)
        alvo.fees_amount = _d(taxas)
        alvo.cogs_amount = _d(cmv)
        alvo.cancelled_count = int(cancelados or 0)
        alvo.cancelled_amount = _d(valor_cancelado)
        alvo.avg_ticket = arredondar(_d(bruto) / pedidos) if pedidos else ZERO
        if existente is None:
            db.add(alvo)
        gravados += 1
    return gravados


# --- Auxiliares --------------------------------------------------------------

def _d(valor: Any) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if valor is None:
        return ZERO
    return Decimal(str(valor))


def _para_datetime(valor: Any) -> datetime:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(valor)).replace(tzinfo=UTC)


def _para_date(valor: Any) -> date:
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if isinstance(valor, datetime):
        return valor.date()
    return date.fromisoformat(str(valor)[:10])


def periodo_padrao(dias: int = 30) -> tuple[datetime, datetime]:
    fim = datetime.now(UTC)
    return fim - timedelta(days=dias), fim


def normalizar_periodo(
    inicio: datetime | None, fim: datetime | None, *, padrao_dias: int = 30
) -> tuple[datetime, datetime]:
    """Normaliza o período, garantindo fuso e ordem coerentes."""
    if inicio is None or fim is None:
        return periodo_padrao(padrao_dias)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=UTC)
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=UTC)
    return (fim, inicio) if inicio > fim else (inicio, fim)


__all__ = [
    "Filtro",
    "visao_geral",
    "serie_temporal",
    "por_canal",
    "ranking_produtos",
    "por_estado",
    "volume_por_minuto",
    "mapa_de_calor",
    "recalcular_rollups",
    "normalizar_periodo",
    "periodo_padrao",
]


# --- Curva ABC, coorte e média móvel -----------------------------------------

#: Cortes clássicos de Pareto sobre a receita acumulada.
CORTE_A = Decimal("80")
CORTE_B = Decimal("95")


async def curva_abc(
    db: AsyncSession, filtro: Filtro, *, limite: int = 500
) -> dict[str, Any]:
    """Classifica os SKUs por participação acumulada na receita.

    Classe **A** vai até 80% da receita acumulada, **B** até 95%, **C** o resto.
    O corte é sobre o acumulado, não sobre a posição no ranking: o que importa
    é quantos itens sustentam o faturamento, e esse número varia — pode ser 12%
    do catálogo ou 40%, e é justamente isso que a análise revela.

    A consolidação é por ``sku_base``, de modo que o mesmo produto anunciado em
    vários canais conte uma vez só. Sem isso, um produto dividido em seis
    anúncios apareceria seis vezes na cauda e nunca na classe A.
    """
    chave = func.coalesce(OrderItem.sku_base, OrderItem.sku_channel)
    linhas = (
        await db.execute(
            filtro.aplicar(
                select(
                    chave.label("sku"),
                    func.min(OrderItem.title),
                    func.sum(OrderItem.quantity),
                    func.sum(OrderItem.gross_amount),
                    func.sum(OrderItem.cogs),
                ).join(Order, Order.id == OrderItem.order_id)
            )
            .where(Order.status != StatusPedido.CANCELADO)
            .group_by("sku")
            .order_by(func.sum(OrderItem.gross_amount).desc())
            .limit(limite)
        )
    ).all()

    total = sum((_d(l[3]) for l in linhas), Decimal("0"))
    itens: list[dict[str, Any]] = []
    acumulado = Decimal("0")
    resumo = {
        classe: {"itens": 0, "receita": Decimal("0"), "margem": Decimal("0")}
        for classe in ("A", "B", "C")
    }

    for posicao, (sku, titulo, unidades, receita, cmv) in enumerate(linhas, start=1):
        valor = _d(receita)

        # A classe sai do acumulado **antes** de somar o item: quem cruza os 80%
        # é o item que fecha a fatia vital, e pertence a ela. Classificar pelo
        # acumulado já somado jogaria o último item sempre para C — inclusive
        # numa operação de produto único, onde o item que é o negócio inteiro
        # apareceria como irrelevante.
        anterior_pct = (acumulado / total * 100) if total else Decimal("0")
        classe = "A" if anterior_pct < CORTE_A else "B" if anterior_pct < CORTE_B else "C"

        acumulado += valor
        pct_acumulado = (acumulado / total * 100) if total else Decimal("0")

        margem = valor - _d(cmv)
        resumo[classe]["itens"] += 1
        resumo[classe]["receita"] += valor
        resumo[classe]["margem"] += margem

        itens.append(
            {
                "posicao": posicao,
                "sku": str(sku or "—"),
                "titulo": str(titulo or ""),
                "classe": classe,
                "unidades": str(_d(unidades)),
                "receita_bruta": str(arredondar(valor)),
                "margem_bruta": str(arredondar(margem)),
                "participacao_pct": str(arredondar(valor / total * 100)) if total else "0.00",
                "acumulado_pct": str(arredondar(pct_acumulado)),
            }
        )

    return {
        "total_receita": str(arredondar(total)),
        "total_itens": len(itens),
        "itens": itens,
        "resumo": [
            {
                "classe": classe,
                "itens": dados["itens"],
                "itens_pct": str(
                    arredondar(Decimal(dados["itens"]) / Decimal(len(itens)) * 100)
                )
                if itens
                else "0.00",
                "receita": str(arredondar(dados["receita"])),
                "receita_pct": str(arredondar(dados["receita"] / total * 100))
                if total
                else "0.00",
                "margem": str(arredondar(dados["margem"])),
            }
            for classe, dados in resumo.items()
        ],
    }


async def coorte_de_compradores(
    db: AsyncSession, tenant_id: int, *, meses: int = 12, canal: str | None = None
) -> dict[str, Any]:
    """Retenção por mês da primeira compra.

    Cada linha é o grupo que comprou pela primeira vez num mês; as colunas são
    os meses seguintes. O valor é quantos daquele grupo voltaram a comprar.

    Depende de ``buyer_hash`` — identificador derivado, sem dado pessoal. Onde
    o canal não expõe comprador algum, o pedido fica de fora e isso é dito no
    retorno em vez de diluído: uma retenção calculada sobre metade da base
    pareceria baixa por motivo errado.
    """
    limite = _somar_meses(datetime.now(UTC).replace(day=1), -(meses - 1))

    consulta = select(
        Order.buyer_hash,
        func.min(Order.date_created).label("primeira"),
        Order.date_created,
        Order.gross_amount,
    ).where(
        Order.tenant_id == tenant_id,
        Order.status != StatusPedido.CANCELADO,
        Order.buyer_hash.is_not(None),
    )
    if canal:
        consulta = consulta.where(Order.channel == canal)

    linhas = (await db.execute(consulta.group_by(Order.id, Order.buyer_hash))).all()

    # O SQLite devolve datetime sem fuso e o Postgres com fuso. Comparar os dois
    # levanta TypeError, então tudo é normalizado para UTC antes de qualquer
    # comparação — o bug só apareceria no ambiente de desenvolvimento.
    primeira_compra: dict[str, datetime] = {}
    for comprador, _min, quando, _valor in linhas:
        atual = primeira_compra.get(comprador)
        quando = _aware(quando)
        if atual is None or quando < atual:
            primeira_compra[comprador] = quando

    # grade[coorte][offset] = conjunto de compradores distintos
    grade: dict[str, dict[int, set[str]]] = {}
    receita: dict[str, dict[int, Decimal]] = {}

    for comprador, _min, quando, valor in linhas:
        quando = _aware(quando)
        origem = primeira_compra[comprador].replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if origem < limite:
            continue
        rotulo = origem.date().isoformat()[:7]
        offset = (quando.year - origem.year) * 12 + (quando.month - origem.month)
        grade.setdefault(rotulo, {}).setdefault(offset, set()).add(comprador)
        receita.setdefault(rotulo, {}).setdefault(offset, Decimal("0"))
        receita[rotulo][offset] += _d(valor)

    total_pedidos = await db.scalar(
        select(func.count(Order.id)).where(
            Order.tenant_id == tenant_id, Order.status != StatusPedido.CANCELADO
        )
    )
    sem_comprador = await db.scalar(
        select(func.count(Order.id)).where(
            Order.tenant_id == tenant_id,
            Order.status != StatusPedido.CANCELADO,
            Order.buyer_hash.is_(None),
        )
    )

    coortes = []
    for rotulo in sorted(grade):
        base = len(grade[rotulo].get(0, set()))
        if not base:
            continue
        periodos = []
        for offset in range(0, max(grade[rotulo]) + 1):
            compradores = len(grade[rotulo].get(offset, set()))
            periodos.append(
                {
                    "offset": offset,
                    "compradores": compradores,
                    "retencao_pct": str(
                        arredondar(Decimal(compradores) / Decimal(base) * 100)
                    ),
                    "receita": str(arredondar(receita[rotulo].get(offset, Decimal("0")))),
                }
            )
        coortes.append({"coorte": rotulo, "base": base, "periodos": periodos})

    cobertura = (
        arredondar(
            Decimal(int(total_pedidos or 0) - int(sem_comprador or 0))
            / Decimal(int(total_pedidos or 1))
            * 100
        )
        if total_pedidos
        else Decimal("0")
    )

    return {
        "coortes": coortes,
        "cobertura": {
            "pedidos_com_comprador_pct": str(cobertura),
            "pedidos_sem_comprador": int(sem_comprador or 0),
            "aviso": (
                f"{sem_comprador} pedidos sem identificador de comprador ficaram de "
                f"fora: o canal não o expõe. A retenção abaixo vale para os "
                f"{cobertura}% restantes."
            )
            if sem_comprador
            else "",
        },
    }


async def serie_com_media_movel(
    db: AsyncSession, filtro: Filtro, *, janela: int = 7
) -> dict[str, Any]:
    """Série diária com média móvel — a tendência sem o ruído do dia da semana.

    Venda de autopeça cai no fim de semana e sobe na segunda. Olhar o dia
    isolado faz toda segunda parecer crescimento e todo sábado, queda. A média
    móvel de 7 dias remove exatamente esse ciclo, porque a janela cobre uma
    semana inteira.

    Os primeiros ``janela - 1`` dias saem com média nula em vez de uma média
    parcial: uma média de dois dias exibida como se fosse de sete inventaria
    uma tendência que ninguém mediu.
    """
    serie = await serie_temporal(db, filtro, "day")
    janela = max(2, janela)

    pontos: list[dict[str, Any]] = []
    for indice, ponto in enumerate(serie):
        if indice + 1 >= janela:
            trecho = serie[indice + 1 - janela : indice + 1]
            media_receita = sum(
                (Decimal(p["receita_bruta"]) for p in trecho), Decimal("0")
            ) / Decimal(janela)
            media_pedidos = Decimal(sum(p["pedidos"] for p in trecho)) / Decimal(janela)
        else:
            media_receita = media_pedidos = None

        pontos.append(
            {
                **ponto,
                "media_movel_receita": str(arredondar(media_receita))
                if media_receita is not None
                else None,
                "media_movel_pedidos": str(arredondar(media_pedidos))
                if media_pedidos is not None
                else None,
            }
        )

    return {"janela": janela, "pontos": pontos, "tendencia": _tendencia(pontos)}


def _tendencia(pontos: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara a média móvel mais recente com a de uma janela atrás.

    Comparar duas médias móveis, e não dois dias, é o ponto: a diferença entre
    ontem e hoje é ruído; a diferença entre duas semanas suavizadas é sinal.
    """
    com_media = [p for p in pontos if p["media_movel_receita"] is not None]
    if len(com_media) < 2:
        return {"direcao": "indefinida", "variacao_pct": "0.00"}

    atual = Decimal(com_media[-1]["media_movel_receita"])
    anterior = Decimal(com_media[0]["media_movel_receita"])
    if anterior <= 0:
        return {"direcao": "indefinida", "variacao_pct": "0.00"}

    variacao = (atual - anterior) / anterior * 100
    return {
        "direcao": "alta" if variacao > 2 else "queda" if variacao < -2 else "estável",
        "variacao_pct": str(arredondar(variacao)),
        "media_atual": str(arredondar(atual)),
        "media_inicial": str(arredondar(anterior)),
    }


def _somar_meses(quando: datetime, delta: int) -> datetime:
    total = (quando.year * 12 + quando.month - 1) + delta
    return quando.replace(year=total // 12, month=total % 12 + 1, day=1)


def _aware(quando: datetime) -> datetime:
    """Garante fuso. O SQLite devolve naive; o Postgres, aware."""
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)
