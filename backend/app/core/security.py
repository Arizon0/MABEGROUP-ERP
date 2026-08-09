"""Autenticação dos usuários do SaaS: senhas, JWT e refresh tokens.

Camada totalmente separada das credenciais de marketplace (``core/crypto.py``).
Aqui trata-se de quem entra no painel; lá, de qual token fala com o Mercado Livre.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

ALGORITMO = settings.JWT_ALGORITHM


# --- Senhas ------------------------------------------------------------------

def hash_senha(senha: str) -> str:
    """Gera o hash bcrypt (cost 12) de uma senha."""
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    """Compara senha e hash. Retorna ``False`` em hash malformado, nunca levanta."""
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_armazenado.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- JWT de acesso -----------------------------------------------------------

def criar_access_token(
    *, user_id: int, tenant_id: int, role: str, expira_em_minutos: int | None = None
) -> str:
    """Emite o JWT de acesso.

    O ``tenant_id`` viaja assinado dentro do token: é o que torna o isolamento
    entre clientes impossível de falsificar sem a chave do servidor.
    """
    agora = datetime.now(UTC)
    minutos = expira_em_minutos or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": str(user_id),
        "tid": tenant_id,
        "role": role,
        "iat": int(agora.timestamp()),
        "exp": int((agora + timedelta(minutes=minutos)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITMO)


def decodificar_token(token: str) -> dict[str, Any]:
    """Valida assinatura e expiração. Levanta ``jwt.PyJWTError`` se inválido."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITMO])


# --- Refresh tokens ----------------------------------------------------------

def gerar_refresh_token() -> tuple[str, str]:
    """Cria um refresh token opaco e o hash que vai para o banco.

    Devolve ``(token_claro, hash)``. O claro só existe nesta resposta; o banco
    guarda apenas o hash, de forma que um vazamento do banco não permite assumir
    a sessão de ninguém.
    """
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def gerar_state_oauth() -> str:
    """Valor anti-CSRF do fluxo OAuth."""
    return secrets.token_urlsafe(32)


def gerar_code_verifier() -> str:
    """``code_verifier`` do PKCE (RFC 7636): 43 a 128 caracteres."""
    return secrets.token_urlsafe(64)[:128]


def code_challenge_de(verifier: str) -> str:
    """``code_challenge`` = BASE64URL(SHA256(verifier)), sem preenchimento."""
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
