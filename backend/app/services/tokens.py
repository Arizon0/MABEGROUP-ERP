"""Ciclo de vida das credenciais de marketplace.

Concentra o trecho mais delicado de toda a integração: a renovação do token.

O refresh token do Mercado Livre é de **uso único** — cada renovação invalida a
anterior. Se dois workers detectarem o token vencido ao mesmo tempo e ambos
chamarem ``/oauth/token``, o segundo usa um refresh já consumido, o Mercado Livre
desconecta a conta e o vendedor precisa reautorizar manualmente. Sob carga, isso
acontece em minutos. A serialização implementada aqui não é otimização: é o que
mantém a conta conectada.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import obter_conector
from app.connectors.base import TokenBundle
from app.core.config import settings
from app.core.crypto import CURRENT_KEY_VERSION, cifrar, decifrar
from app.core.errors import CredencialInvalida, NaoEncontrado
from app.models.channel import ChannelAccount, ChannelCredential
from app.models.enums import StatusConta

log = structlog.get_logger(__name__)

#: Renova quando resta menos que isto de vida. Renovar proativamente evita que
#: uma requisição de usuário pague o custo do refresh — e evita a janela em que
#: várias chamadas simultâneas encontram o token já vencido.
MARGEM_RENOVACAO = timedelta(minutes=30)

_locks_locais: dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def _lock_de_renovacao(account_id: int):
    """Exclusão mútua na renovação de uma conta.

    Com Redis, o lock é distribuído e cobre todas as réplicas. Sem Redis, um
    ``asyncio.Lock`` por conta cobre o processo atual — suficiente em
    desenvolvimento, onde só existe um processo.
    """
    if settings.REDIS_URL:
        import redis.asyncio as redis

        cliente = redis.from_url(settings.REDIS_URL, decode_responses=True)
        chave = f"lock:refresh:{account_id}"
        try:
            adquirido = False
            for _ in range(60):  # até ~30 s esperando quem está renovando
                if await cliente.set(chave, "1", nx=True, ex=30):
                    adquirido = True
                    break
                await asyncio.sleep(0.5)
            yield adquirido
        finally:
            if adquirido:
                await cliente.delete(chave)
            await cliente.aclose()
    else:
        lock = _locks_locais.setdefault(account_id, asyncio.Lock())
        async with lock:
            yield True


async def credencial_atual(db: AsyncSession, account_id: int) -> ChannelCredential | None:
    resultado = await db.execute(
        select(ChannelCredential)
        .where(
            ChannelCredential.channel_account_id == account_id,
            ChannelCredential.is_current.is_(True),
        )
        .order_by(ChannelCredential.id.desc())
        .limit(1)
    )
    return resultado.scalar_one_or_none()


async def salvar_tokens(
    db: AsyncSession, account_id: int, tokens: TokenBundle
) -> ChannelCredential:
    """Grava novas credenciais e aposenta as anteriores.

    O histórico é preservado (``is_current=False``) para auditoria — permite
    responder qual credencial estava ativa quando uma sincronização falhou, sem
    guardar nada em texto claro.
    """
    await db.execute(
        update(ChannelCredential)
        .where(
            ChannelCredential.channel_account_id == account_id,
            ChannelCredential.is_current.is_(True),
        )
        .values(is_current=False, rotated_at=datetime.now(UTC))
    )

    credencial = ChannelCredential(
        channel_account_id=account_id,
        access_token_enc=cifrar(tokens.access_token),
        refresh_token_enc=cifrar(tokens.refresh_token),
        access_expires_at=tokens.expires_at,
        refresh_expires_at=tokens.refresh_expires_at,
        key_version=CURRENT_KEY_VERSION,
        is_current=True,
    )
    db.add(credencial)
    await db.flush()
    return credencial


async def obter_access_token(db: AsyncSession, account: ChannelAccount) -> str:
    """Devolve um access token válido, renovando se necessário.

    Fluxo:

    1. Se falta mais que :data:`MARGEM_RENOVACAO` de vida, devolve o atual.
    2. Senão, adquire o lock e **relê a credencial do banco** — se outro worker
       já renovou enquanto esperávamos, usamos o token dele e não gastamos o
       refresh (que é de uso único).
    3. Só então renova, gravando na mesma transação.
    """
    credencial = await credencial_atual(db, account.id)
    if credencial is None:
        raise NaoEncontrado(
            f"A conta {account.nickname or account.id} não possui credencial ativa. "
            f"Reconecte-a em Configurações."
        )

    if not _precisa_renovar(credencial):
        token = decifrar(credencial.access_token_enc)
        if token:
            return token

    refresh = decifrar(credencial.refresh_token_enc)
    if not refresh:
        await _marcar_conta(db, account, StatusConta.EXPIRADA, "Sem refresh token disponível.")
        raise CredencialInvalida(
            f"A conexão com {account.channel} expirou e não há refresh token. "
            f"É necessário reautorizar a conta.",
            canal=account.channel,
        )

    async with _lock_de_renovacao(account.id) as tenho_lock:
        # Releitura obrigatória: outro processo pode ter renovado enquanto
        # esperávamos o lock. Sem isso, gastaríamos um refresh token já
        # substituído e derrubaríamos a conta.
        await db.refresh(account)
        atual = await credencial_atual(db, account.id)
        if atual and atual.id != credencial.id and not _precisa_renovar(atual):
            token = decifrar(atual.access_token_enc)
            if token:
                return token

        if not tenho_lock:
            raise CredencialInvalida(
                "Não foi possível coordenar a renovação do token. Tente novamente.",
                canal=account.channel,
            )

        conector = obter_conector(account.channel)
        try:
            novos = await conector.refresh(refresh, shop_id=account.external_account_id)
        except Exception as exc:
            await _marcar_conta(db, account, StatusConta.ERRO, str(exc)[:400])
            log.error(
                "falha_refresh_token",
                canal=account.channel,
                conta=account.id,
                erro=str(exc),
            )
            raise CredencialInvalida(
                f"Falha ao renovar o token de {account.channel}. "
                f"Se persistir, reconecte a conta.",
                canal=account.channel,
            ) from exc

        await salvar_tokens(db, account.id, novos)
        account.status = StatusConta.CONECTADA
        account.last_error = ""
        await db.commit()
        log.info("token_renovado", canal=account.channel, conta=account.id)
        return novos.access_token


def _precisa_renovar(credencial: ChannelCredential) -> bool:
    if credencial.access_expires_at is None:
        return False  # token de longa duração (ex.: credencial própria do MP)
    expira = credencial.access_expires_at
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=UTC)
    return expira - datetime.now(UTC) <= MARGEM_RENOVACAO


async def _marcar_conta(
    db: AsyncSession, account: ChannelAccount, status: str, erro: str
) -> None:
    account.status = status
    account.last_error = erro
    await db.commit()


async def contas_para_renovar(db: AsyncSession) -> list[ChannelAccount]:
    """Contas cujo token vence em breve — usado pelo cron horário."""
    limite = datetime.now(UTC) + MARGEM_RENOVACAO
    resultado = await db.execute(
        select(ChannelAccount)
        .join(ChannelCredential, ChannelCredential.channel_account_id == ChannelAccount.id)
        .where(
            ChannelAccount.status == StatusConta.CONECTADA,
            ChannelCredential.is_current.is_(True),
            ChannelCredential.access_expires_at.is_not(None),
            ChannelCredential.access_expires_at <= limite,
        )
    )
    return list(resultado.scalars().unique())


async def revogar(db: AsyncSession, account: ChannelAccount) -> None:
    """Revoga o acesso: apaga as credenciais e preserva o histórico de dados.

    Único lugar do sistema com exclusão física — credencial revogada não deve
    sobreviver em lugar nenhum. Os pedidos e o financeiro permanecem: são
    registro contábil do vendedor, não do marketplace.
    """
    await db.execute(
        ChannelCredential.__table__.delete().where(
            ChannelCredential.channel_account_id == account.id
        )
    )
    account.status = StatusConta.REVOGADA
    await db.commit()
    log.info("conta_revogada", canal=account.channel, conta=account.id)
