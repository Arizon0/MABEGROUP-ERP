"""Aba Configurações: auditoria, monitor de integração e alertas."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import AdminDep, CtxDep, DbDep
from app.core.errors import NaoEncontrado
from app.models.channel import SyncCursor, WebhookEvent
from app.models.enums import StatusWebhook
from app.models.metrics import Alert, AlertRule
from app.schemas.common import RespostaOperacao
from app.services import audit, webhooks

router = APIRouter(prefix="/settings", tags=["Configurações"])


class RegraAlertaIn(BaseModel):
    name: str
    kind: str
    threshold: Decimal = Decimal("0")
    channel: str = ""
    notify_email: str = ""
    notify_webhook: str = ""
    is_active: bool = True


@router.get("/audit", summary="Log de auditoria")
async def auditoria(
    ctx: AdminDep,
    db: DbDep,
    action: str | None = None,
    limite: int = Query(100, le=500),
    offset: int = 0,
) -> list[dict[str, Any]]:
    registros = await audit.listar(
        db, ctx.tenant_id, limite=limite, offset=offset, action=action
    )
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "before": r.before_json,
            "after": r.after_json,
            "ip": r.ip,
            "created_at": r.created_at,
        }
        for r in registros
    ]


@router.get(
    "/integration-monitor",
    summary="Saúde da integração",
    description=(
        "Painel de diagnóstico: fila de webhooks, defasagem de sincronização e "
        "eventos mortos. É onde se descobre que uma conta parou de receber dados "
        "antes que o vendedor perceba o buraco no relatório."
    ),
)
async def monitor(ctx: AdminDep, db: DbDep) -> dict[str, Any]:
    desde = datetime.now(UTC) - timedelta(hours=24)

    por_status = (
        await db.execute(
            select(WebhookEvent.status, func.count(WebhookEvent.id))
            .where(WebhookEvent.tenant_id == ctx.tenant_id, WebhookEvent.received_at >= desde)
            .group_by(WebhookEvent.status)
        )
    ).all()

    por_canal = (
        await db.execute(
            select(WebhookEvent.channel, WebhookEvent.topic, func.count(WebhookEvent.id))
            .where(WebhookEvent.tenant_id == ctx.tenant_id, WebhookEvent.received_at >= desde)
            .group_by(WebhookEvent.channel, WebhookEvent.topic)
            .order_by(func.count(WebhookEvent.id).desc())
            .limit(20)
        )
    ).all()

    from app.models.channel import ChannelAccount

    cursores = (
        await db.execute(
            select(SyncCursor, ChannelAccount)
            .join(ChannelAccount, ChannelAccount.id == SyncCursor.channel_account_id)
            .where(ChannelAccount.tenant_id == ctx.tenant_id)
        )
    ).all()

    agora = datetime.now(UTC)
    return {
        "webhooks_24h": {
            "por_status": [{"status": str(s), "quantidade": int(q or 0)} for s, q in por_status],
            "por_topico": [
                {"channel": str(c), "topic": str(t), "quantidade": int(q or 0)}
                for c, t, q in por_canal
            ],
        },
        "sincronizacao": [
            {
                "conta_id": conta.id,
                "channel": conta.channel,
                "nickname": conta.nickname,
                "resource": cursor.resource,
                "last_synced_at": cursor.last_synced_at,
                "atraso_minutos": round(
                    (agora - _aware(cursor.last_synced_at)).total_seconds() / 60
                )
                if cursor.last_synced_at
                else None,
                "status": cursor.status,
                "falhas_consecutivas": cursor.consecutive_failures,
                "last_error": cursor.last_error,
            }
            for cursor, conta in cursores
        ],
    }


@router.get("/webhooks", summary="Últimos webhooks recebidos")
async def listar_webhooks(
    ctx: AdminDep,
    db: DbDep,
    status_filtro: str | None = Query(None, alias="status"),
    limite: int = Query(50, le=200),
) -> list[dict[str, Any]]:
    consulta = select(WebhookEvent).where(WebhookEvent.tenant_id == ctx.tenant_id)
    if status_filtro:
        consulta = consulta.where(WebhookEvent.status == status_filtro)
    resultado = await db.execute(
        consulta.order_by(WebhookEvent.received_at.desc()).limit(limite)
    )
    return [
        {
            "id": e.id,
            "channel": e.channel,
            "topic": e.topic,
            "resource": e.resource,
            "status": e.status,
            "attempts": e.attempts,
            "signature_valid": e.signature_valid,
            "received_at": e.received_at,
            "processed_at": e.processed_at,
            "error": e.error[:500],
        }
        for e in resultado.scalars()
    ]


@router.post(
    "/webhooks/{evento_id}/reprocess",
    response_model=RespostaOperacao,
    summary="Reprocessa um evento da DLQ",
)
async def reprocessar(evento_id: int, ctx: AdminDep, db: DbDep) -> RespostaOperacao:
    evento = await db.get(WebhookEvent, evento_id)
    if evento is None or evento.tenant_id != ctx.tenant_id:
        raise NaoEncontrado("Evento não encontrado.")

    ok = await webhooks.reprocessar(db, evento_id)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.WEBHOOK_REPROCESSADO,
        entity_type="webhook_event",
        entity_id=evento_id,
    )
    await db.commit()
    return RespostaOperacao(
        sucesso=ok,
        mensagem="Evento reprocessado." if ok else "O reprocessamento falhou; consulte o erro.",
    )


@router.get("/alerts", summary="Alertas disparados")
async def alertas(ctx: CtxDep, db: DbDep, limite: int = Query(50, le=200)) -> list[dict[str, Any]]:
    resultado = await db.execute(
        select(Alert)
        .where(Alert.tenant_id == ctx.tenant_id)
        .order_by(Alert.created_at.desc())
        .limit(limite)
    )
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "severity": a.severity,
            "title": a.title,
            "message": a.message,
            "acknowledged_at": a.acknowledged_at,
            "created_at": a.created_at,
        }
        for a in resultado.scalars()
    ]


@router.get("/alert-rules", summary="Regras de alerta configuradas")
async def regras(ctx: AdminDep, db: DbDep) -> list[dict[str, Any]]:
    resultado = await db.execute(
        select(AlertRule).where(AlertRule.tenant_id == ctx.tenant_id).order_by(AlertRule.id)
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "kind": r.kind,
            "threshold": str(r.threshold),
            "channel": r.channel,
            "is_active": r.is_active,
            "notify_email": r.notify_email,
            "notify_webhook": r.notify_webhook,
        }
        for r in resultado.scalars()
    ]


@router.post("/alert-rules", response_model=RespostaOperacao, summary="Cria regra de alerta")
async def criar_regra(dados: RegraAlertaIn, ctx: AdminDep, db: DbDep) -> RespostaOperacao:
    regra = AlertRule(tenant_id=ctx.tenant_id, **dados.model_dump())
    db.add(regra)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.CONFIG_ALTERADA,
        entity_type="alert_rule",
        after=dados.model_dump(mode="json"),
    )
    await db.commit()
    return RespostaOperacao(mensagem=f"Regra '{dados.name}' criada.", dados={"id": regra.id})


@router.delete("/alert-rules/{regra_id}", response_model=RespostaOperacao, summary="Remove regra")
async def remover_regra(regra_id: int, ctx: AdminDep, db: DbDep) -> RespostaOperacao:
    regra = await db.get(AlertRule, regra_id)
    if regra is None or regra.tenant_id != ctx.tenant_id:
        raise NaoEncontrado("Regra não encontrada.")
    await db.delete(regra)
    await db.commit()
    return RespostaOperacao(mensagem="Regra removida.")


def _aware(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
