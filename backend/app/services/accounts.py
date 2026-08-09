"""Conexão de contas de marketplace: orquestração do fluxo de autorização."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import obter_conector
from app.core.crypto import cifrar, decifrar
from app.core.errors import Conflito, NaoEncontrado, Proibido
from app.core.security import gerar_code_verifier, gerar_state_oauth
from app.models.channel import ChannelAccount, OAuthState
from app.models.enums import Canal, StatusConta
from app.services import tokens

log = structlog.get_logger(__name__)

#: TTL curto: o state é anti-CSRF, não sessão. Dez minutos cobrem com folga o
#: tempo de o vendedor autorizar no domínio do marketplace.
TTL_STATE = timedelta(minutes=10)

#: Canais que usam PKCE. A Shopee assina cada requisição e não tem fluxo PKCE.
USA_PKCE = {Canal.MERCADO_LIVRE}


async def iniciar_conexao(
    db: AsyncSession, tenant_id: int, canal: str, redirect_after: str = ""
) -> str:
    """Cria o ``state`` e devolve a URL de autorização do marketplace."""
    state = gerar_state_oauth()
    verifier = gerar_code_verifier() if canal in USA_PKCE else None

    db.add(
        OAuthState(
            state=state,
            tenant_id=tenant_id,
            channel=canal,
            # O verifier é cifrado porque, junto de um ``code`` interceptado,
            # bastaria para trocar por um token válido.
            code_verifier_enc=cifrar(verifier),
            redirect_after=redirect_after,
            expires_at=datetime.now(UTC) + TTL_STATE,
        )
    )
    await db.commit()

    conector = obter_conector(canal)
    url = await conector.build_authorization_url(state, verifier)
    log.info("conexao_iniciada", canal=canal, tenant=tenant_id)
    return url


async def concluir_conexao(
    db: AsyncSession, canal: str, code: str, state: str, **extra: Any
) -> ChannelAccount:
    """Consome o ``state``, troca o ``code`` por tokens e registra a conta."""
    registro = await db.scalar(select(OAuthState).where(OAuthState.state == state))
    if registro is None:
        raise Proibido("Parâmetro de segurança (state) inválido ou desconhecido.")
    if registro.consumed_at is not None:
        raise Conflito("Esta autorização já foi utilizada.")
    expira = registro.expires_at
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=UTC)
    if expira < datetime.now(UTC):
        raise Proibido("A autorização expirou. Inicie a conexão novamente.")
    if registro.channel != canal:
        raise Proibido("O canal da autorização não corresponde ao esperado.")

    registro.consumed_at = datetime.now(UTC)
    verifier = decifrar(registro.code_verifier_enc)

    conector = obter_conector(canal)
    pacote = await conector.exchange_code(code, verifier, **extra)
    info = await conector.fetch_account_info(
        pacote.access_token, shop_id=pacote.external_account_id
    )

    externo = info.external_account_id or pacote.external_account_id
    conta = await db.scalar(
        select(ChannelAccount).where(
            ChannelAccount.tenant_id == registro.tenant_id,
            ChannelAccount.channel == canal,
            ChannelAccount.external_account_id == externo,
        )
    )
    if conta is None:
        conta = ChannelAccount(
            tenant_id=registro.tenant_id, channel=canal, external_account_id=externo
        )
        db.add(conta)

    conta.nickname = info.nickname
    conta.site_id = info.site_id
    conta.status = StatusConta.CONECTADA
    conta.scopes = pacote.scopes
    conta.connected_at = datetime.now(UTC)
    conta.last_error = ""
    conta.metadata_json = info.metadata
    await db.flush()

    await tokens.salvar_tokens(db, conta.id, pacote)
    await db.commit()

    log.info("conta_conectada", canal=canal, conta=conta.id, tenant=registro.tenant_id)
    return conta


async def listar(db: AsyncSession, tenant_id: int) -> list[ChannelAccount]:
    resultado = await db.execute(
        select(ChannelAccount)
        .where(ChannelAccount.tenant_id == tenant_id)
        .order_by(ChannelAccount.channel, ChannelAccount.id)
    )
    return list(resultado.scalars())


async def obter(db: AsyncSession, tenant_id: int, conta_id: int) -> ChannelAccount:
    """Busca uma conta **sempre** dentro do escopo do tenant.

    Devolve 404 (e não 403) quando a conta é de outro tenant: confirmar a
    existência do recurso já seria vazamento de informação.
    """
    conta = await db.scalar(
        select(ChannelAccount).where(
            ChannelAccount.id == conta_id, ChannelAccount.tenant_id == tenant_id
        )
    )
    if conta is None:
        raise NaoEncontrado("Conta não encontrada.")
    return conta


async def status_detalhado(db: AsyncSession, conta: ChannelAccount) -> dict[str, Any]:
    """Situação da conta para a tela de Configurações."""
    from app.models.channel import SyncCursor

    credencial = await tokens.credencial_atual(db, conta.id)
    cursores = list(
        (
            await db.execute(
                select(SyncCursor).where(SyncCursor.channel_account_id == conta.id)
            )
        ).scalars()
    )

    return {
        "id": conta.id,
        "channel": conta.channel,
        "nickname": conta.nickname,
        "external_account_id": conta.external_account_id,
        "status": conta.status,
        "connected_at": conta.connected_at,
        "last_sync_at": conta.last_sync_at,
        "last_error": conta.last_error,
        "token_expires_at": credencial.access_expires_at if credencial else None,
        "refresh_expires_at": credencial.refresh_expires_at if credencial else None,
        "has_credential": credencial is not None,
        "cursors": [
            {
                "resource": c.resource,
                "last_synced_at": c.last_synced_at,
                "status": c.status,
                "failures": c.consecutive_failures,
                "progress_pct": c.progress_pct,
                "last_error": c.last_error,
            }
            for c in cursores
        ],
    }


async def limpar_states_expirados(db: AsyncSession) -> int:
    """Remove states vencidos (rotina de limpeza diária)."""
    from sqlalchemy import delete

    resultado = await db.execute(
        delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC) - timedelta(days=1))
    )
    await db.commit()
    return resultado.rowcount or 0
