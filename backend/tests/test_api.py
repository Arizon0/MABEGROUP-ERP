"""Testes de contrato da API: status, formato e comportamento de erro."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ENDPOINTS_DE_LEITURA = [
    "/api/v1/auth/me",
    "/api/v1/accounts",
    "/api/v1/accounts/channels",
    "/api/v1/dashboard/overview",
    "/api/v1/dashboard/timeseries",
    "/api/v1/dashboard/channels",
    "/api/v1/dashboard/products",
    "/api/v1/dashboard/geo",
    "/api/v1/orders",
    "/api/v1/live/pulse",
    "/api/v1/live/feed",
    "/api/v1/finance/waterfall",
    "/api/v1/finance/fees",
    "/api/v1/finance/reconciliation",
    "/api/v1/finance/divergences",
    "/api/v1/finance/cashflow",
    "/api/v1/finance/settlements",
    "/api/v1/catalog/listings",
    "/api/v1/catalog/stock-health",
    "/api/v1/catalog/sku-pendencies",
    "/api/v1/catalog/products",
    "/api/v1/catalog/sku-links",
    "/api/v1/logistics/overview",
    "/api/v1/logistics/delayed",
    "/api/v1/support/overview",
    "/api/v1/support/questions",
    "/api/v1/support/reputation-history",
    "/api/v1/marketing/campaigns",
    "/api/v1/settings/audit",
    "/api/v1/settings/integration-monitor",
    "/api/v1/settings/webhooks",
    "/api/v1/settings/alerts",
    "/api/v1/settings/alert-rules",
]


@pytest.mark.parametrize("caminho", ENDPOINTS_DE_LEITURA)
async def test_endpoints_respondem_com_base_vazia(cliente, caminho):
    """Base vazia é o estado do primeiro minuto de uso — não pode dar erro."""
    resposta = await cliente.get(caminho)
    assert resposta.status_code == 200, f"{caminho}: {resposta.text[:300]}"


async def test_health_reporta_o_estado_das_dependencias(cliente):
    corpo = (await cliente.get("/health")).json()
    assert corpo["status"] in ("ok", "degradado")
    assert corpo["banco"] == "ok"
    # A hora do servidor entra na resposta porque a Shopee rejeita requisições
    # com timestamp fora de ±5 min, e o sintoma disso é um erro genérico.
    assert "hora_servidor" in corpo


async def test_openapi_documenta_a_api(cliente):
    esquema = (await cliente.get("/openapi.json")).json()
    assert esquema["info"]["title"]
    assert len(esquema["paths"]) > 25


async def test_erro_segue_formato_padronizado(cliente):
    resposta = await cliente.get("/api/v1/orders/99999999")
    assert resposta.status_code == 404

    corpo = resposta.json()
    assert corpo["erro"]["codigo"] == "nao_encontrado"
    assert corpo["erro"]["mensagem"]


async def test_entrada_invalida_devolve_422_com_os_campos(cliente):
    resposta = await cliente.post("/api/v1/auth/login", json={"email": "nao-e-email"})
    assert resposta.status_code == 422
    assert resposta.json()["erro"]["codigo"] == "erro_validacao"


async def test_login_com_senha_errada_nao_revela_se_o_email_existe(cliente, usuario):
    inexistente = await cliente.post(
        "/api/v1/auth/login",
        json={"email": "ninguem@exemplo.com.br", "senha": "qualquer-coisa"},
    )
    senha_errada = await cliente.post(
        "/api/v1/auth/login", json={"email": usuario.email, "senha": "errada"}
    )

    assert inexistente.status_code == senha_errada.status_code == 401
    assert inexistente.json()["erro"]["mensagem"] == senha_errada.json()["erro"]["mensagem"]


async def test_refresh_rotaciona_e_invalida_o_token_anterior(cliente, usuario):
    """Um refresh token reutilizável que vaze dá acesso permanente à conta."""
    login = await cliente.post(
        "/api/v1/auth/login",
        json={"email": usuario.email, "senha": "senha-de-teste-123"},
    )
    antigo = login.json()["refresh_token"]

    primeira = await cliente.post("/api/v1/auth/refresh", json={"refresh_token": antigo})
    assert primeira.status_code == 200
    assert primeira.json()["refresh_token"] != antigo

    reuso = await cliente.post("/api/v1/auth/refresh", json={"refresh_token": antigo})
    assert reuso.status_code == 401


async def test_cabecalhos_de_seguranca_presentes(cliente):
    resposta = await cliente.get("/health")
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["X-Request-ID"]


async def test_mapear_sku_recalcula_o_custo_dos_pedidos_ja_importados(cliente, conta, db):
    """O de-para retroalimenta o histórico: a margem passa a existir para trás."""
    from app.services import sync as servico_sync

    await servico_sync.sincronizar_pedidos(db, conta)

    pendencias = (await cliente.get("/api/v1/catalog/sku-pendencies")).json()
    assert pendencias, "a simulação deve produzir SKUs sem mapeamento"

    produto = await cliente.post(
        "/api/v1/catalog/products",
        json={"sku": "PROD-TESTE", "name": "Produto de teste", "unit_cost": "10.00"},
    )
    assert produto.status_code == 201

    vinculo = await cliente.post(
        "/api/v1/catalog/sku-links",
        json={
            "channel": pendencias[0]["channel"],
            "sku_channel": pendencias[0]["sku_channel"],
            "product_id": produto.json()["id"],
        },
    )
    assert vinculo.status_code == 200
    assert vinculo.json()["dados"]["itens_atualizados"] > 0
