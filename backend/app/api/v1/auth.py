"""Autenticação dos usuários do painel."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.core.deps import AdminDep, CtxDep, DbDep, usuario_atual
from app.models.enums import PapelUsuario
from app.models.tenant import Tenant, User
from app.schemas.common import Base, RespostaOperacao
from app.services import audit, auth

router = APIRouter(prefix="/auth", tags=["Autenticação"])


class LoginIn(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=1, max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str


class NovoUsuarioIn(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=10, max_length=200)
    nome: str = ""
    papel: str = PapelUsuario.LEITOR


class TokenOut(Base):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    user: dict | None = None


class UsuarioOut(Base):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool


@router.post("/login", response_model=TokenOut, summary="Autentica e emite tokens")
async def login(dados: LoginIn, request: Request, db: DbDep) -> TokenOut:
    resultado = await auth.autenticar(
        db,
        dados.email,
        dados.senha,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    usuario = resultado["user"]
    await audit.registrar(
        db,
        tenant_id=usuario["tenant"]["id"],
        user_id=usuario["id"],
        action=audit.Acao.LOGIN,
        ip=request.client.host if request.client else "",
    )
    await db.commit()
    return TokenOut(**resultado)


@router.post("/refresh", response_model=TokenOut, summary="Rotaciona o refresh token")
async def refresh(dados: RefreshIn, db: DbDep) -> TokenOut:
    return TokenOut(**await auth.renovar(db, dados.refresh_token))


@router.post("/logout", response_model=RespostaOperacao, summary="Encerra a sessão")
async def logout(dados: RefreshIn, db: DbDep) -> RespostaOperacao:
    await auth.encerrar_sessao(db, dados.refresh_token)
    return RespostaOperacao(mensagem="Sessão encerrada.")


@router.get("/me", summary="Dados do usuário autenticado")
async def eu(
    ctx: CtxDep, db: DbDep, usuario: Annotated[User, Depends(usuario_atual)]
) -> dict:
    tenant = await db.get(Tenant, ctx.tenant_id)
    return {
        "id": usuario.id,
        "email": usuario.email,
        "full_name": usuario.full_name,
        "role": usuario.role,
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "timezone": tenant.timezone,
        }
        if tenant
        else None,
    }


@router.post(
    "/usuarios",
    response_model=UsuarioOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cria um usuário na organização",
)
async def criar_usuario(dados: NovoUsuarioIn, ctx: AdminDep, db: DbDep) -> UsuarioOut:
    usuario = await auth.criar_usuario(
        db,
        tenant_id=ctx.tenant_id,
        email=dados.email,
        senha=dados.senha,
        nome=dados.nome,
        papel=dados.papel,
    )
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.USUARIO_CRIADO,
        entity_type="user",
        entity_id=usuario.id,
        after={"email": usuario.email, "role": usuario.role},
    )
    await db.commit()
    return UsuarioOut.model_validate(usuario)


@router.get("/usuarios", response_model=list[UsuarioOut], summary="Lista os usuários")
async def listar_usuarios(ctx: AdminDep, db: DbDep) -> list[UsuarioOut]:
    from sqlalchemy import select

    resultado = await db.execute(
        select(User).where(User.tenant_id == ctx.tenant_id).order_by(User.id)
    )
    return [UsuarioOut.model_validate(u) for u in resultado.scalars()]
