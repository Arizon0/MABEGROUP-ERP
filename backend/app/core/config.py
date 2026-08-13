"""Configuração da aplicação, lida de variáveis de ambiente.

Toda a configuração passa por aqui. Nenhum módulo lê ``os.environ`` direto —
isso garante um único ponto de validação e permite sobrescrever tudo em testes.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Aplicação -----------------------------------------------------------
    APP_NAME: str = "Marketplace Hub"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["local", "test", "staging", "production"] = "local"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # --- Banco ---------------------------------------------------------------
    # Em produção: postgresql+asyncpg://user:senha@host:5432/db
    DATABASE_URL: str = "sqlite+aiosqlite:///./marketplace_hub.db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    #: Segundos após os quais uma conexão do pool é descartada em vez de
    #: reutilizada. Curto de propósito: ver o comentário em ``db/session.py``.
    DB_POOL_RECYCLE: int = 300
    DB_ECHO: bool = False

    # --- Redis (fila, cache e pub/sub do painel ao vivo) ----------------------
    # Vazio = modo degradado com barramento em memória (só para desenvolvimento).
    REDIS_URL: str = ""

    # --- Segurança -----------------------------------------------------------
    SECRET_KEY: str = "dev-inseguro-troque-em-producao-por-favor-000000"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    # Chave mestra do cofre de tokens (Fernet: 32 bytes em base64 urlsafe).
    # Vazia = derivada da SECRET_KEY, aceitável só fora de produção.
    MASTER_ENCRYPTION_KEY: str = ""
    # Sal usado na pseudonimização de identificadores de comprador (LGPD).
    BUYER_HASH_PEPPER: str = "dev-pepper-troque-em-producao"

    CORS_ORIGINS: str = "*"

    # --- Mercado Livre -------------------------------------------------------
    ML_CLIENT_ID: str = ""
    ML_CLIENT_SECRET: str = ""
    ML_REDIRECT_URI: str = "http://localhost:8000/api/v1/oauth/mercadolivre/callback"
    ML_AUTH_BASE: str = "https://auth.mercadolivre.com.br"
    ML_API_BASE: str = "https://api.mercadolibre.com"

    # --- Mercado Pago --------------------------------------------------------
    MP_CLIENT_ID: str = ""
    MP_CLIENT_SECRET: str = ""
    MP_REDIRECT_URI: str = "http://localhost:8000/api/v1/oauth/mercadopago/callback"
    MP_API_BASE: str = "https://api.mercadopago.com"
    MP_WEBHOOK_SECRET: str = ""

    # --- Shopee --------------------------------------------------------------
    SHOPEE_PARTNER_ID: str = ""
    SHOPEE_PARTNER_KEY: str = ""
    SHOPEE_REDIRECT_URI: str = "http://localhost:8000/api/v1/oauth/shopee/callback"
    SHOPEE_API_BASE: str = "https://partner.shopeemobile.com"

    # --- Integração ----------------------------------------------------------
    # Com 1, nenhuma chamada sai para a internet: os conectores mock geram dados
    # realistas. É o que permite rodar o projeto inteiro sem credencial nenhuma.
    USE_MOCK_CONNECTORS: bool = True
    HTTP_TIMEOUT_SECONDS: float = 20.0
    HTTP_MAX_RETRIES: int = 4
    RATE_LIMIT_PER_MINUTE: int = 300
    WEBHOOK_MAX_ATTEMPTS: int = 6
    SYNC_OVERLAP_MINUTES: int = 5
    BACKFILL_DAYS: int = 90
    #: Pedidos por dia que o conector simulado gera, por canal do Mercado Livre
    #: (a Shopee usa a proporção real, ~1/3 disso). O padrão reproduz o volume
    #: da operação real para a demonstração; a suíte de testes baixa este valor,
    #: porque provar a regra não exige mil pedidos — exige os casos certos, e
    #: gerar volume só faz cada teste pagar segundos que não compram nada.
    MOCK_ORDERS_PER_DAY: int = 11

    # --- Conciliação ---------------------------------------------------------
    RECONCILIATION_TOLERANCE: str = "0.01"

    # --- Observabilidade -----------------------------------------------------
    SENTRY_DSN: str = ""
    METRICS_ENABLED: bool = True

    # --- Seed inicial --------------------------------------------------------
    SEED_ON_STARTUP: bool = True
    ADMIN_EMAIL: str = "admin@marketplacehub.com.br"
    ADMIN_PASSWORD: str = "admin123"
    ADMIN_TENANT: str = "Demonstração"

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _limpar_origens(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def fernet_key(self) -> bytes:
        """Chave do cofre de tokens, em formato aceito pelo Fernet.

        Em produção exige ``MASTER_ENCRYPTION_KEY`` explícita. Fora dela, deriva
        da ``SECRET_KEY`` para que o projeto rode sem configuração adicional.
        """
        if self.MASTER_ENCRYPTION_KEY:
            return self.MASTER_ENCRYPTION_KEY.encode()
        if self.is_production:
            raise RuntimeError(
                "MASTER_ENCRYPTION_KEY é obrigatória em produção: sem ela os "
                "tokens de marketplace seriam cifrados com uma chave derivada "
                "de um segredo compartilhado."
            )
        import hashlib

        return base64.urlsafe_b64encode(hashlib.sha256(self.SECRET_KEY.encode()).digest())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
