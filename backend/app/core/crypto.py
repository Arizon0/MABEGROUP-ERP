"""Cofre de credenciais de marketplace.

Tokens de marketplace são o ativo mais sensível do sistema: um ``access_token``
do Mercado Livre com escopo de escrita permite alterar preços e encerrar anúncios
em nome do vendedor. Por isso nunca ficam em texto claro no banco.

Cifra usada: Fernet (AES-128-CBC + HMAC-SHA256), que é autenticado — adulterar o
ciphertext é detectado na decifragem, não silenciosamente aceito.

A chave mestra vive fora do banco (variável de ambiente / KMS). Um dump do banco,
sozinho, não revela nenhum token.
"""
from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# Versão da chave gravada junto de cada credencial. Permite rotacionar a chave
# mestra sem downtime: a nova cifra as escritas, as antigas seguem decifrando as
# leituras até a re-cifragem em lote terminar.
CURRENT_KEY_VERSION = 1


class CofreError(RuntimeError):
    """Falha ao cifrar ou decifrar uma credencial."""


def _fernet() -> Fernet:
    return Fernet(settings.fernet_key())


def cifrar(valor: str | None) -> bytes | None:
    """Cifra um segredo. ``None`` passa direto (campo opcional continua nulo)."""
    if valor is None:
        return None
    return _fernet().encrypt(valor.encode("utf-8"))


def decifrar(blob: bytes | None) -> str | None:
    """Decifra um segredo cifrado por :func:`cifrar`."""
    if blob is None:
        return None
    try:
        return _fernet().decrypt(blob).decode("utf-8")
    except InvalidToken as exc:  # chave errada, versão errada ou dado adulterado
        raise CofreError(
            "Não foi possível decifrar a credencial. Isso indica chave mestra "
            "incorreta/rotacionada ou registro adulterado."
        ) from exc


def hash_comprador(identificador: str | int | None) -> str | None:
    """Pseudonimiza o identificador do comprador (LGPD).

    Guardamos o hash com *pepper* em vez do ID original: permite medir recompra
    e comportamento sem armazenar a identidade de ninguém. Como usa HMAC com um
    segredo do servidor, não é vulnerável a ataque de dicionário sobre IDs.
    """
    if identificador is None or identificador == "":
        return None
    return hmac.new(
        settings.BUYER_HASH_PEPPER.encode(), str(identificador).encode(), hashlib.sha256
    ).hexdigest()


def chave_idempotencia(*partes: str) -> str:
    """Chave determinística usada para deduplicar webhooks e sincronizações."""
    return hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()
