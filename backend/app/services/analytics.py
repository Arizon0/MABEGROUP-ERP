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
