"""Erros de domínio e tratamento consistente na borda HTTP.

Regra: os serviços levantam erros de *domínio* (que não conhecem HTTP); a borda
os traduz para status e corpo padronizados. Isso mantém a lógica de negócio
testável sem cliente HTTP e garante que toda resposta de erro tenha o mesmo
formato.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import structlog

log = structlog.get_logger(__name__)


class ErroDominio(Exception):
    """Base de todos os erros de negócio."""

    status_code = status.HTTP_400_BAD_REQUEST
    codigo = "erro_dominio"

    def __init__(self, mensagem: str, detalhes: dict[str, Any] | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.detalhes = detalhes or {}


class NaoEncontrado(ErroDominio):
    status_code = status.HTTP_404_NOT_FOUND
    codigo = "nao_encontrado"


class NaoAutorizado(ErroDominio):
    status_code = status.HTTP_401_UNAUTHORIZED
    codigo = "nao_autorizado"


class Proibido(ErroDominio):
    status_code = status.HTTP_403_FORBIDDEN
    codigo = "proibido"


class Conflito(ErroDominio):
    status_code = status.HTTP_409_CONFLICT
    codigo = "conflito"


class ErroValidacao(ErroDominio):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    codigo = "erro_validacao"


class ErroIntegracao(ErroDominio):
    """Falha ao falar com uma API de marketplace."""

    status_code = status.HTTP_502_BAD_GATEWAY
    codigo = "erro_integracao"

    def __init__(
        self,
        mensagem: str,
        *,
        canal: str = "",
        status_externo: int | None = None,
        detalhes: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(mensagem, detalhes)
        self.canal = canal
        self.status_externo = status_externo


class LimiteDeTaxa(ErroIntegracao):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    codigo = "limite_de_taxa"

    def __init__(self, mensagem: str, *, canal: str = "", retry_after: float = 60.0) -> None:
        super().__init__(mensagem, canal=canal)
        self.retry_after = retry_after


class CredencialInvalida(ErroIntegracao):
    """Token expirado, revogado ou insuficiente para o recurso pedido."""

    status_code = status.HTTP_401_UNAUTHORIZED
    codigo = "credencial_invalida"


def _corpo(codigo: str, mensagem: str, detalhes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"erro": {"codigo": codigo, "mensagem": mensagem, "detalhes": detalhes or {}}}


def registrar_tratadores(app: FastAPI) -> None:
    """Instala os tratadores de exceção na aplicação."""

    @app.exception_handler(ErroDominio)
    async def _dominio(_: Request, exc: ErroDominio) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_corpo(exc.codigo, exc.mensagem, exc.detalhes),
        )

    @app.exception_handler(RequestValidationError)
    async def _validacao(_: Request, exc: RequestValidationError) -> JSONResponse:
        # ``exc.errors()`` devolve o ``ctx`` cru do Pydantic, que em campos
        # monetários carrega os limites como ``Decimal`` — tipo que o
        # ``json.dumps`` do JSONResponse não serializa. Sem o encoder, toda
        # alíquota ou valor fora de faixa viraria 500 em vez de 422.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder(
                _corpo("erro_validacao", "Dados de entrada inválidos.", {"campos": exc.errors()})
            ),
        )

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, exc: Exception) -> JSONResponse:
        # O detalhe fica no log correlacionado; o cliente recebe mensagem genérica
        # para não vazar estrutura interna em resposta de erro.
        log.exception("erro_nao_tratado", caminho=request.url.path, erro=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_corpo("erro_interno", "Erro interno. A equipe foi notificada."),
        )
