"""As migrations precisam rodar sobre um banco que já tem dados.

Um `alembic upgrade` que só funciona em banco vazio passa despercebido em todo
teste e falha exatamente uma vez: no dia da atualização, com dados reais dentro.
Adicionar coluna NOT NULL sem `server_default` é o jeito mais fácil de causar
isso, e o autogenerate do Alembic escreve assim por padrão.
"""
from __future__ import annotations

import subprocess
import sqlite3
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PRIMEIRA = "5b72ed966a94"


def _alembic(comando: str, banco: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [".venv/bin/alembic", *comando.split()],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        timeout=300,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(RAIZ),
            "DATABASE_URL": f"sqlite+aiosqlite:///{banco}",
            "ENVIRONMENT": "local",
        },
    )


@pytest.mark.skipif(
    not (RAIZ / ".venv/bin/alembic").exists(), reason="alembic fora do venv local"
)
def test_upgrade_completo_sobre_banco_com_dados(tmp_path):
    """Sobe até a primeira migration, grava dados e aplica o resto por cima."""
    banco = tmp_path / "upgrade.db"

    inicial = _alembic(f"upgrade {PRIMEIRA}", banco)
    assert inicial.returncode == 0, inicial.stderr

    con = sqlite3.connect(banco)
    con.execute(
        "INSERT INTO tenants (id,name,slug,plan,timezone,status,settings_json,"
        "created_at,updated_at) VALUES (1,'Demo','demo','free','UTC','active','{}',"
        "datetime('now'),datetime('now'))"
    )
    con.execute(
        "INSERT INTO products (id,tenant_id,sku,name,brand,category,unit_cost,ncm,"
        "ean,weight_grams,is_active,notes,created_at,updated_at) VALUES "
        "(1,1,'5338','Retentor','Sabo','',14.20,'','',0,1,'',datetime('now'),"
        "datetime('now'))"
    )
    con.commit()
    con.close()

    resto = _alembic("upgrade head", banco)
    assert resto.returncode == 0, (
        "upgrade falhou sobre banco com dados — provável coluna NOT NULL sem "
        f"server_default:\n{resto.stderr}"
    )

    con = sqlite3.connect(banco)
    try:
        # O dado antigo sobrevive e as colunas novas nascem preenchidas.
        assert con.execute(
            "SELECT sku, unit_cost, packaging_cost, freight_in_cost FROM products"
        ).fetchall() == [("5338", 14.2, 0, 0)]

        tabelas = {
            linha[0]
            for linha in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"tax_rules", "tax_brackets", "operating_expenses"} <= tabelas
    finally:
        con.close()
