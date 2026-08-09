"""Log estruturado com redação automática de segredos.

Detalhe que evita o incidente mais comum de vazamento: quando o httpx levanta
erro, a URL completa (com ``access_token`` na query string, no caso da Shopee)
entra na mensagem de exceção e vai parar no Sentry. O processador
:func:`redigir_segredos` remove esses valores antes de qualquer saída.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import settings

# Cobre os formatos que aparecem nos três marketplaces.
_PADROES = [
    re.compile(r"(access_token=)[^&\s\"']+", re.I),
    re.compile(r"(refresh_token=)[^&\s\"']+", re.I),
    re.compile(r"(partner_key=)[^&\s\"']+", re.I),
    re.compile(r"(client_secret=)[^&\s\"']+", re.I),
    re.compile(r"(sign=)[0-9a-f]{32,}", re.I),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I),
    re.compile(r"(APP_USR-)[A-Za-z0-9\-]+", re.I),  # tokens Mercado Pago
    re.compile(r"(TG-)[A-Za-z0-9\-]+", re.I),       # authorization codes do ML
]

_CHAVES_SENSIVEIS = {
    "access_token", "refresh_token", "client_secret", "partner_key",
    "password", "senha", "secret_key", "authorization", "sign",
    "master_encryption_key", "x-signature",
}


def _mascarar(valor: Any) -> Any:
    if isinstance(valor, str):
        for padrao in _PADROES:
            valor = padrao.sub(r"\1***", valor)
        return valor
    if isinstance(valor, dict):
        return {
            k: ("***" if k.lower() in _CHAVES_SENSIVEIS else _mascarar(v))
            for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return type(valor)(_mascarar(v) for v in valor)
    return valor


def redigir_segredos(_logger: Any, _metodo: str, evento: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k.lower() in _CHAVES_SENSIVEIS else _mascarar(v)) for k, v in evento.items()}


def configurar_logging() -> None:
    """Configura structlog: JSON em produção, colorido e legível em desenvolvimento."""
    nivel = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=nivel)

    processadores: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redigir_segredos,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processadores.append(
        structlog.processors.JSONRenderer()
        if settings.ENVIRONMENT in ("production", "staging")
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processadores,
        wrapper_class=structlog.make_filtering_bound_logger(nivel),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
