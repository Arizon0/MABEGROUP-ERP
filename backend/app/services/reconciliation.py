"""Conciliação financeira em três níveis.

Apontar a diferença é a parte fácil; explicar a diferença é o que faz o módulo
valer alguma coisa. Por isso cada divergência recebe um diagnóstico automático
da causa provável em ``reconciliations.notes`` — ver
``docs/06-financeiro-conciliacao.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import FonteLiquido, StatusConciliacao, StatusPedido
from app.models.finance import Payment, Reconciliation, Refund, SettlementEntry
from app.models.order import Order
from app.models.support import Claim
from app.services.finance import arredondar

log = structlog.get_logger(__name__)
ZERO = Decimal("0")


@dataclass(slots=True)
class ResultadoConciliacao:
    analisados: int = 0
    conciliados: int = 0
    divergentes: int = 0
    aguardando: int = 0
    sem_correspondencia: int = 0
    divergencia_total: Decimal = ZERO

    def como_dict(self) -> dict[str, Any]:
        return {
            "analisados": self.analisados,
            "conciliados": self.conciliados,
            "divergentes": self.divergentes,
            "aguardando_repasse": self.aguardando,
            "sem_correspondencia": self.sem_correspondencia,
            "divergencia_total": str(arredondar(self.divergencia_total)),
        }


def tolerancia() -> Decimal:
    return Decimal(settings.RECONCILIATION_TOLERANCE)


async def conciliar_periodo(
    db: AsyncSession, tenant_id: int, *, dias: int = 30
) -> ResultadoConciliacao:
    """Concilia os pedidos do período e grava o resultado por pedido."""
    limite = datetime.now(UTC) - timedelta(days=dias)
    pedidos = list(
        (
            await db.execute(
                select(Order).where(
                    Order.tenant_id == tenant_id,
                    Order.date_created >= limite,
                    Order.status != StatusPedido.CANCELADO,
                )
            )
        ).scalars()
    )

    resultado = ResultadoConciliacao()
    for pedido in pedidos:
        estado = await conciliar_pedido(db, pedido)
        resultado.analisados += 1
        if estado.status == StatusConciliacao.CONCILIADO:
            resultado.conciliados += 1
        elif estado.status == StatusConciliacao.DIVERGENTE:
            resultado.divergentes += 1
            resultado.divergencia_total += abs(estado.divergence)
        elif estado.status == StatusConciliacao.AGUARDANDO_REPASSE:
            resultado.aguardando += 1
        else:
            resultado.sem_correspondencia += 1

    await db.commit()
    log.info("conciliacao_concluida", tenant=tenant_id, **resultado.como_dict())
    return resultado


async def conciliar_pedido(db: AsyncSession, pedido: Order) -> Reconciliation:
    """Concilia um pedido nos três níveis e diagnostica a causa da divergência."""
    registro = await db.scalar(
        select(Reconciliation).where(Reconciliation.order_id == pedido.id)
    )
    if registro is None:
        registro = Reconciliation(tenant_id=pedido.tenant_id, order_id=pedido.id)
        db.add(registro)

    pagamentos = list(
        (await db.execute(select(Payment).where(Payment.order_id == pedido.id))).scalars()
    )

    esperado = Decimal(str(pedido.net_amount or ZERO))
    registro.expected_net = esperado
    registro.checked_at = datetime.now(UTC)

    # --- Nível 1: existe pagamento? -----------------------------------------
    if not pagamentos:
        registro.settled_net = ZERO
        registro.divergence = -esperado
        registro.status = (
            StatusConciliacao.SEM_CORRESPONDENCIA
            if pedido.status in (StatusPedido.PAGO, StatusPedido.ENTREGUE)
            else StatusConciliacao.AGUARDANDO_REPASSE
        )
        registro.notes = (
            "Pedido sem pagamento registrado. Pode ser atraso de sincronização do "
            "provedor ou pagamento recebido por fora do canal."
        )
        return registro

    # --- Nível 2: o repasse já ocorreu? -------------------------------------
    liquidado = ZERO
    for pagamento in pagamentos:
        total_repassado = await db.scalar(
            select(func.coalesce(func.sum(SettlementEntry.amount), 0)).where(
                SettlementEntry.payment_id == pagamento.id
            )
        )
        liquidado += Decimal(str(total_repassado or 0))

    if liquidado == ZERO:
        # Sem repasse ainda: o líquido informado pelo provedor é o melhor
        # número disponível, mas não é dinheiro em conta.
        liquidado = sum(
            (Decimal(str(p.net_received_amount or 0)) for p in pagamentos), ZERO
        )
        registro.settled_net = liquidado
        registro.divergence = arredondar(liquidado - esperado)
        registro.status = StatusConciliacao.AGUARDANDO_REPASSE
        registro.notes = _diagnosticar_pendencia(pedido, pagamentos)
        return registro

    # --- Nível 3: bate com o que caiu na conta? -----------------------------
    registro.settled_net = liquidado
    divergencia = arredondar(liquidado - esperado)
    registro.divergence = divergencia
    registro.divergence_pct = (
        arredondar(divergencia / esperado * 100) if esperado else ZERO
    )

    if abs(divergencia) <= tolerancia():
        registro.status = StatusConciliacao.CONCILIADO
        registro.notes = ""
    else:
        registro.status = StatusConciliacao.DIVERGENTE
        registro.notes = await _diagnosticar_divergencia(db, pedido, pagamentos, divergencia)

    return registro


def _diagnosticar_pendencia(pedido: Order, pagamentos: list[Payment]) -> str:
    """Explica por que o repasse ainda não aconteceu."""
    liberacoes = [p.money_release_date for p in pagamentos if p.money_release_date]
    if liberacoes:
        proxima = min(_aware(d) for d in liberacoes)
        dias = (proxima - datetime.now(UTC)).days
        if dias > 0:
            return f"Repasse previsto para {proxima.date().isoformat()} (em {dias} dias)."
        return (
            f"Liberação prevista para {proxima.date().isoformat()} já passou sem "
            f"crédito correspondente. Verificar retenção ou reclamação em aberto."
        )

    if pedido.net_source == FonteLiquido.CALCULADO:
        idade = (datetime.now(UTC) - _aware(pedido.date_created)).days
        if idade > 20:
            return (
                f"Líquido ainda estimado após {idade} dias. Na Shopee o escrow só é "
                f"emitido depois que o comprador confirma o recebimento — acima de "
                f"20 dias, verificar se há retenção ou devolução em curso."
            )
        return "Líquido estimado; aguardando o valor definitivo do canal."

    return "Aguardando repasse do canal."


async def _diagnosticar_divergencia(
    db: AsyncSession, pedido: Order, pagamentos: list[Payment], divergencia: Decimal
) -> str:
    """Identifica a causa provável, em vez de só apontar a diferença.

    A ordem das verificações vai da causa mais frequente para a mais rara, e a
    primeira que explicar a maior parte do valor é reportada.
    """
    causas: list[str] = []

    reembolsos = ZERO
    for pagamento in pagamentos:
        total = await db.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.payment_id == pagamento.id
            )
        )
        reembolsos += Decimal(str(total or 0))
    if reembolsos > ZERO:
        causas.append(f"reembolso de R$ {arredondar(reembolsos)} lançado sobre o pedido")

    parcelamento = ZERO
    for pagamento in pagamentos:
        for taxa in pagamento.fees:
            if taxa.fee_type == "financing_fee":
                parcelamento += Decimal(str(taxa.amount))
    if parcelamento > ZERO and abs(divergencia) >= parcelamento * Decimal("0.8"):
        causas.append(
            f"taxa de parcelamento de R$ {arredondar(parcelamento)} não prevista na estimativa"
        )

    reclamacao = await db.scalar(
        select(Claim).where(Claim.order_id == pedido.id).limit(1)
    )
    if reclamacao is not None:
        causas.append(
            f"reclamação {reclamacao.external_id} ({reclamacao.status}) pode ter gerado débito"
        )

    if pedido.shipping_cost and divergencia < ZERO:
        causas.append("frete pode ter sido recalculado após o despacho")

    if not causas:
        sinal = "a menos" if divergencia < ZERO else "a mais"
        causas.append(
            f"diferença de R$ {arredondar(abs(divergencia))} {sinal} sem causa "
            f"identificada automaticamente — conferir o extrato do canal"
        )

    return "Divergência: " + "; ".join(causas) + "."


async def resumo(db: AsyncSession, tenant_id: int, *, dias: int = 30) -> dict[str, Any]:
    """Agregado da conciliação para o painel."""
    limite = datetime.now(UTC) - timedelta(days=dias)
    linhas = list(
        (
            await db.execute(
                select(
                    Reconciliation.status,
                    func.count(Reconciliation.id),
                    func.coalesce(func.sum(Reconciliation.divergence), 0),
                )
                .join(Order, Order.id == Reconciliation.order_id)
                .where(Reconciliation.tenant_id == tenant_id, Order.date_created >= limite)
                .group_by(Reconciliation.status)
            )
        ).all()
    )

    por_status = {
        str(status): {"quantidade": int(qtd), "divergencia": str(arredondar(Decimal(str(soma))))}
        for status, qtd, soma in linhas
    }
    total = sum(v["quantidade"] for v in por_status.values())
    conciliados = por_status.get(StatusConciliacao.CONCILIADO, {}).get("quantidade", 0)

    return {
        "por_status": por_status,
        "total": total,
        "taxa_conciliacao_pct": str(arredondar(Decimal(conciliados) / total * 100))
        if total
        else "0.00",
    }


async def divergencias(
    db: AsyncSession, tenant_id: int, *, limite: int = 100
) -> list[dict[str, Any]]:
    """Fila de exceções, ordenada pelo maior impacto financeiro."""
    resultado = await db.execute(
        select(Reconciliation, Order)
        .join(Order, Order.id == Reconciliation.order_id)
        .where(
            Reconciliation.tenant_id == tenant_id,
            Reconciliation.status == StatusConciliacao.DIVERGENTE,
        )
        .order_by(func.abs(Reconciliation.divergence).desc())
        .limit(limite)
    )
    return [
        {
            "order_id": pedido.id,
            "external_id": pedido.external_id,
            "channel": pedido.channel,
            "date_created": pedido.date_created,
            "gross_amount": str(arredondar(Decimal(str(pedido.gross_amount)))),
            "expected_net": str(arredondar(Decimal(str(conc.expected_net)))),
            "settled_net": str(arredondar(Decimal(str(conc.settled_net)))),
            "divergence": str(arredondar(Decimal(str(conc.divergence)))),
            "divergence_pct": str(conc.divergence_pct),
            "notes": conc.notes,
        }
        for conc, pedido in resultado.all()
    ]


def _aware(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
