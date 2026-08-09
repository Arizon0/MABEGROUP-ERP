"""Gestão das contas de marketplace conectadas (aba Configurações)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.connectors import canais_disponiveis
from app.core.config import settings
from app.core.deps import AdminDep, CtxDep, DbDep
from app.schemas.common import RespostaOperacao
from app.services import accounts, audit, sync, tokens

router = APIRouter(prefix="/accounts", tags=["Contas conectadas"])


@router.get("", summary="Lista as contas conectadas")
async def listar(ctx: CtxDep, db: DbDep) -> list[dict[str, Any]]:
    contas = await accounts.listar(db, ctx.tenant_id)
    return [await accounts.status_detalhado(db, conta) for conta in contas]


@router.get("/channels", summary="Canais suportados e seu estado de configuração")
async def canais() -> dict[str, Any]:
    """Informa quais canais estão prontos para conectar.

    Um canal sem credencial de aplicação configurada não deve exibir botão de
    conectar que só levaria o usuário a um erro do marketplace.
    """
    configurados = {
        "mercadolivre": bool(settings.ML_CLIENT_ID and settings.ML_CLIENT_SECRET),
        "mercadopago": bool(settings.MP_CLIENT_ID and settings.MP_CLIENT_SECRET),
        "shopee": bool(settings.SHOPEE_PARTNER_ID and settings.SHOPEE_PARTNER_KEY),
    }
    return {
        "modo_simulado": settings.USE_MOCK_CONNECTORS,
        "canais": [
            {
                "channel": canal,
                "rotulo": {
                    "mercadolivre": "Mercado Livre",
                    "mercadopago": "Mercado Pago",
                    "shopee": "Shopee",
                }[canal],
                # Em modo simulado tudo é conectável, para o produto poder ser
                # demonstrado antes de qualquer homologação.
                "configurado": settings.USE_MOCK_CONNECTORS or configurados[canal],
            }
            for canal in canais_disponiveis()
        ],
    }


@router.get("/{conta_id}", summary="Detalhe de uma conta")
async def detalhe(conta_id: int, ctx: CtxDep, db: DbDep) -> dict[str, Any]:
    conta = await accounts.obter(db, ctx.tenant_id, conta_id)
    return await accounts.status_detalhado(db, conta)


@router.post("/{conta_id}/sync", response_model=RespostaOperacao, summary="Sincroniza sob demanda")
async def sincronizar(
    conta_id: int, ctx: AdminDep, db: DbDep, completo: bool = Query(False)
) -> RespostaOperacao:
    conta = await accounts.obter(db, ctx.tenant_id, conta_id)
    resultados = (
        await sync.sincronizar_conta(db, conta)
        if completo
        else [await sync.sincronizar_pedidos(db, conta)]
    )
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.SYNC_MANUAL,
        entity_type="channel_account",
        entity_id=conta_id,
        ip=ctx.ip,
    )
    await db.commit()
    return RespostaOperacao(
        mensagem="Sincronização concluída.",
        dados={"resultados": [r.como_dict() for r in resultados]},
    )


@router.post(
    "/{conta_id}/refresh-token",
    response_model=RespostaOperacao,
    summary="Força a renovação do token",
)
async def renovar_token(conta_id: int, ctx: AdminDep, db: DbDep) -> RespostaOperacao:
    conta = await accounts.obter(db, ctx.tenant_id, conta_id)
    await tokens.obter_access_token(db, conta)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.TOKEN_RENOVADO,
        entity_type="channel_account",
        entity_id=conta_id,
    )
    await db.commit()
    return RespostaOperacao(mensagem="Token renovado.")


@router.delete(
    "/{conta_id}",
    response_model=RespostaOperacao,
    summary="Revoga o acesso da conta",
    description=(
        "Apaga as credenciais (única exclusão física do sistema). Pedidos, "
        "pagamentos e histórico permanecem: são registro contábil do vendedor, "
        "não do marketplace."
    ),
)
async def revogar(conta_id: int, ctx: AdminDep, db: DbDep) -> RespostaOperacao:
    conta = await accounts.obter(db, ctx.tenant_id, conta_id)
    await tokens.revogar(db, conta)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.CONTA_REVOGADA,
        entity_type="channel_account",
        entity_id=conta_id,
        before={"channel": conta.channel, "nickname": conta.nickname},
        ip=ctx.ip,
    )
    await db.commit()
    return RespostaOperacao(
        mensagem=f"Acesso revogado. O histórico de {conta.nickname or conta.channel} foi preservado."
    )
