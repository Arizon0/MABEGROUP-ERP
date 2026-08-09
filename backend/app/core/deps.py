"""Dependências do FastAPI: autenticação, escopo de tenant e RBAC.

O escopo de tenant é injetado aqui, uma única vez, e todo endpoint de negócio o
recebe. É a segunda das três camadas de isolamento descritas em
``docs/08-seguranca.md`` — e a única que o código da aplicação controla
diretamente.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NaoAutorizado, Proibido
from app.core.security import decodificar_token
from app.db.session import get_db
from app.models.enums import PapelUsuario
from app.models.tenant import User
from app.services.auth import pode


@dataclass(slots=True)
class Contexto:
    """Identidade autenticada da requisição."""

    user_id: int
    tenant_id: int
    role: str
    ip: str = ""
    user_agent: str = ""


async def obter_contexto(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    # O EventSource do navegador não permite cabeçalhos personalizados, então o
    # endpoint SSE aceita o token por query string. É o único caso, e o token de
    # acesso é curto (30 min) justamente para limitar a exposição em logs de proxy.
    token: Annotated[str | None, Query()] = None,
) -> Contexto:
    bruto = ""
    if authorization and authorization.lower().startswith("bearer "):
        bruto = authorization[7:].strip()
    elif token:
        bruto = token.strip()

    if not bruto:
        raise NaoAutorizado("Autenticação necessária.")

    try:
        dados = decodificar_token(bruto)
    except jwt.ExpiredSignatureError as exc:
        raise NaoAutorizado("Sessão expirada. Faça login novamente.") from exc
    except jwt.PyJWTError as exc:
        raise NaoAutorizado("Token inválido.") from exc

    if dados.get("typ") != "access":
        raise NaoAutorizado("Tipo de token inválido para esta operação.")

    return Contexto(
        user_id=int(dados["sub"]),
        tenant_id=int(dados["tid"]),
        role=str(dados.get("role") or PapelUsuario.LEITOR),
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", "")[:400],
    )


async def usuario_atual(
    ctx: Annotated[Contexto, Depends(obter_contexto)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    usuario = await db.get(User, ctx.user_id)
    if usuario is None or not usuario.is_active:
        raise NaoAutorizado("Usuário indisponível.")
    return usuario


def exige_papel(papel: str):
    """Fábrica de dependência que impõe um papel mínimo.

    A verificação acontece **no servidor**. Esconder um botão na interface é
    conveniência de UX, nunca controle de acesso.
    """

    async def _verificar(ctx: Annotated[Contexto, Depends(obter_contexto)]) -> Contexto:
        if not pode(ctx.role, papel):
            raise Proibido(
                f"Esta ação exige o perfil '{papel}' ou superior. "
                f"Seu perfil atual é '{ctx.role}'."
            )
        return ctx

    return _verificar


#: Atalhos usados nas assinaturas dos endpoints.
CtxDep = Annotated[Contexto, Depends(obter_contexto)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminDep = Annotated[Contexto, Depends(exige_papel(PapelUsuario.ADMIN))]
AnalistaDep = Annotated[Contexto, Depends(exige_papel(PapelUsuario.ANALISTA))]
