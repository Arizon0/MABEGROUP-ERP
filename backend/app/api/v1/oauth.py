"""Fluxo de autorização das contas de marketplace."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.deps import AdminDep, DbDep
from app.db.session import SessionLocal
from app.models.enums import Canal
from app.schemas.common import Base
from app.services import accounts, audit

router = APIRouter(prefix="/oauth", tags=["Conexão de contas"])

CANAIS_VALIDOS = {Canal.MERCADO_LIVRE, Canal.MERCADO_PAGO, Canal.SHOPEE}


class UrlAutorizacao(Base):
    authorization_url: str
    channel: str


@router.get(
    "/{canal}/authorize",
    response_model=UrlAutorizacao,
    summary="Gera a URL de autorização do marketplace",
)
async def autorizar(canal: str, ctx: AdminDep, db: DbDep) -> UrlAutorizacao:
    """Inicia a conexão de uma conta.

    Devolve a URL em JSON (em vez de redirecionar) porque quem chama é o
    frontend por XHR: o redirecionamento precisa acontecer no navegador, não na
    requisição da API.
    """
    from app.core.errors import ErroDominio

    if canal not in CANAIS_VALIDOS:
        raise ErroDominio(f"Canal inválido: {canal!r}.")

    url = await accounts.iniciar_conexao(db, ctx.tenant_id, canal)
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="account.authorize_started",
        entity_type="channel",
        entity_id=canal,
        ip=ctx.ip,
    )
    await db.commit()
    return UrlAutorizacao(authorization_url=url, channel=canal)


@router.get(
    "/{canal}/callback",
    summary="Callback do marketplace após a autorização",
    include_in_schema=False,
)
async def callback(
    canal: str,
    code: str = Query(""),
    state: str = Query(""),
    shop_id: str = Query(""),
    error: str = Query(""),
) -> RedirectResponse:
    """Recebe o retorno do marketplace e finaliza a conexão.

    Usa sessão própria em vez da dependência ``get_db``: o navegador do vendedor
    chega aqui **sem** o JWT do painel (a volta vem do domínio do marketplace),
    então este endpoint não pode exigir autenticação. A segurança do fluxo é
    garantida pelo ``state``, que é de uso único e tem TTL de 10 minutos.
    """
    destino = _base_frontend()

    if error:
        return RedirectResponse(f"{destino}/configuracoes?erro={error}&canal={canal}")
    if not code or not state:
        return RedirectResponse(f"{destino}/configuracoes?erro=parametros_ausentes&canal={canal}")

    async with SessionLocal() as db:
        try:
            conta = await accounts.concluir_conexao(db, canal, code, state, shop_id=shop_id)
        except Exception as exc:
            import structlog

            structlog.get_logger(__name__).warning(
                "callback_oauth_falhou", canal=canal, erro=str(exc)
            )
            return RedirectResponse(
                f"{destino}/configuracoes?erro=falha_conexao&canal={canal}"
            )

        await audit.registrar(
            db,
            tenant_id=conta.tenant_id,
            action=audit.Acao.CONTA_CONECTADA,
            entity_type="channel_account",
            entity_id=conta.id,
            after={"channel": canal, "nickname": conta.nickname},
        )
        await db.commit()
        await _agendar_backfill(conta.id)

    return RedirectResponse(f"{destino}/configuracoes?conectado={canal}")


async def _agendar_backfill(conta_id: int) -> None:
    """Dispara a carga histórica em segundo plano.

    Com Redis, vai para a fila. Sem Redis (desenvolvimento), roda inline — o
    onboarding precisa funcionar independentemente da infraestrutura disponível.
    """
    import structlog

    log = structlog.get_logger(__name__)
    if settings.REDIS_URL:
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            await pool.enqueue_job("backfill_conta", conta_id, settings.BACKFILL_DAYS)
            await pool.aclose()
            return
        except Exception as exc:
            log.warning("enfileirar_backfill_falhou", erro=str(exc))

    from app.workers.tasks import backfill_conta

    try:
        await backfill_conta({}, conta_id, settings.BACKFILL_DAYS)
    except Exception as exc:
        log.warning("backfill_inline_falhou", conta=conta_id, erro=str(exc))


def _base_frontend() -> str:
    origens = settings.cors_origins_list
    for origem in origens:
        if origem.startswith("http") and origem != "*":
            return origem.rstrip("/")
    return "http://localhost:5173"
