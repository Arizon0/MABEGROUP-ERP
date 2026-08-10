"""Edição e exclusão: o que o operador consegue corrigir sozinho.

Um painel só de leitura obriga o usuário a mexer no banco quando erra um custo
ou mapeia um SKU errado. Estes testes garantem que o caminho existe — e que ele
não destrói o histórico no processo.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.catalog import Product, SkuLink, SkuPendency
from app.models.enums import PapelUsuario
from app.models.order import OrderItem

pytestmark = pytest.mark.asyncio


async def _criar_produto(cliente, sku="TESTE-1", custo="10.00", embalagem="1.50"):
    resposta = await cliente.post(
        "/api/v1/catalog/products",
        json={"sku": sku, "name": f"Produto {sku}", "unit_cost": custo,
              "packaging_cost": embalagem},
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


class TestProdutos:
    async def test_cadastra_com_custo_e_embalagem(self, cliente):
        produto = await _criar_produto(cliente)
        assert produto["unit_cost"] == "10.00"
        assert produto["packaging_cost"] == "1.50"

    async def test_edita_custo(self, cliente):
        produto = await _criar_produto(cliente)
        resposta = await cliente.patch(
            f"/api/v1/catalog/products/{produto['id']}",
            json={"sku": produto["sku"], "name": "Renomeado", "unit_cost": "25.00"},
        )
        assert resposta.status_code == 200
        assert resposta.json()["unit_cost"] == "25.00"

    async def test_exclui_produto_sem_vendas(self, cliente, db):
        produto = await _criar_produto(cliente, sku="SEM-VENDA")
        resposta = await cliente.delete(f"/api/v1/catalog/products/{produto['id']}")

        assert resposta.status_code == 200
        assert resposta.json()["dados"]["excluido"] is True
        assert await db.get(Product, produto["id"]) is None

    async def test_produto_com_venda_e_desativado_e_nao_excluido(self, cliente, db, conta):
        """Excluir apagaria o custo congelado e reescreveria a margem histórica."""
        from app.services import sync

        produto = await _criar_produto(cliente, sku="COM-VENDA")
        await sync.sincronizar_pedidos(db, conta)

        pendencias = (await cliente.get("/api/v1/catalog/sku-pendencies")).json()
        await cliente.post(
            "/api/v1/catalog/sku-links",
            json={
                "channel": pendencias[0]["channel"],
                "sku_channel": pendencias[0]["sku_channel"],
                "product_id": produto["id"],
            },
        )

        resposta = await cliente.delete(f"/api/v1/catalog/products/{produto['id']}")
        assert resposta.status_code == 200
        assert resposta.json()["dados"]["desativado"] is True

        registro = await db.get(Product, produto["id"])
        assert registro is not None and registro.is_active is False

    async def test_atualiza_custos_em_lote(self, cliente):
        await _criar_produto(cliente, sku="LOTE-A", custo="1.00")
        await _criar_produto(cliente, sku="LOTE-B", custo="2.00")

        resposta = await cliente.post(
            "/api/v1/catalog/products/bulk-cost",
            json=[
                {"sku": "LOTE-A", "unit_cost": "11.00", "packaging_cost": "2.00"},
                {"sku": "LOTE-B", "unit_cost": "22.00"},
                {"sku": "NAO-EXISTE", "unit_cost": "5.00"},
            ],
        )
        dados = resposta.json()["dados"]
        assert dados["atualizados"] == 2
        assert dados["nao_encontrados"] == ["NAO-EXISTE"]

    async def test_sku_duplicado_e_recusado(self, cliente):
        await _criar_produto(cliente, sku="DUP")
        resposta = await cliente.post(
            "/api/v1/catalog/products", json={"sku": "DUP", "name": "Outro"}
        )
        assert resposta.status_code == 409


class TestDeParaDeSku:
    async def test_desfaz_vinculo_e_devolve_para_pendencias(self, cliente, db, conta):
        from app.services import sync

        produto = await _criar_produto(cliente, sku="MAP-1")
        await sync.sincronizar_pedidos(db, conta)

        pendencias = (await cliente.get("/api/v1/catalog/sku-pendencies")).json()
        alvo = pendencias[0]
        await cliente.post(
            "/api/v1/catalog/sku-links",
            json={"channel": alvo["channel"], "sku_channel": alvo["sku_channel"],
                  "product_id": produto["id"]},
        )

        vinculos = (await cliente.get("/api/v1/catalog/sku-links")).json()
        vinculo = next(v for v in vinculos if v["sku_channel"] == alvo["sku_channel"])

        # O custo já gravado nas vendas precisa sobreviver ao desfazer.
        custo_antes = await db.scalar(
            select(func.coalesce(func.sum(OrderItem.cogs), 0)).where(
                OrderItem.sku_channel == alvo["sku_channel"]
            )
        )

        resposta = await cliente.delete(f"/api/v1/catalog/sku-links/{vinculo['id']}")
        assert resposta.status_code == 200

        assert await db.get(SkuLink, vinculo["id"]) is None

        pendencia = await db.scalar(
            select(SkuPendency).where(SkuPendency.sku_channel == alvo["sku_channel"])
        )
        assert pendencia is not None and pendencia.resolved is False

        custo_depois = await db.scalar(
            select(func.coalesce(func.sum(OrderItem.cogs), 0)).where(
                OrderItem.sku_channel == alvo["sku_channel"]
            )
        )
        assert Decimal(str(custo_depois)) == Decimal(str(custo_antes))


class TestUsuarios:
    async def test_cria_edita_e_desativa(self, cliente):
        criar = await cliente.post(
            "/api/v1/auth/usuarios",
            json={"email": "novo@exemplo.com.br", "senha": "senha-longa-123",
                  "nome": "Novo", "papel": PapelUsuario.ANALISTA},
        )
        assert criar.status_code == 201
        usuario_id = criar.json()["id"]

        editar = await cliente.patch(
            f"/api/v1/auth/usuarios/{usuario_id}",
            json={"nome": "Renomeado", "papel": PapelUsuario.LEITOR},
        )
        assert editar.status_code == 200
        assert editar.json()["role"] == PapelUsuario.LEITOR

        desativar = await cliente.patch(
            f"/api/v1/auth/usuarios/{usuario_id}", json={"is_active": False}
        )
        assert desativar.json()["is_active"] is False

    async def test_nao_permite_ficar_sem_proprietario(self, cliente, usuario):
        """Desativar o último proprietário deixaria a organização sem administração."""
        resposta = await cliente.patch(
            f"/api/v1/auth/usuarios/{usuario.id}", json={"is_active": False}
        )
        assert resposta.status_code == 409
        assert "única conta de proprietário" in resposta.json()["erro"]["mensagem"]

    async def test_senha_curta_e_recusada(self, cliente):
        resposta = await cliente.post(
            "/api/v1/auth/usuarios", json={"email": "x@y.com.br", "senha": "curta"}
        )
        assert resposta.status_code == 422


class TestCustosEImpostos:
    async def test_ciclo_completo_da_regra_tributaria(self, cliente):
        criar = await cliente.post(
            "/api/v1/costs/tax-rules",
            json={"name": "Simples Anexo I", "rate_pct": "8.00",
                  "valid_from": "2026-01-01"},
        )
        assert criar.status_code == 201
        regra_id = criar.json()["id"]

        editar = await cliente.patch(
            f"/api/v1/costs/tax-rules/{regra_id}",
            json={"name": "Simples Anexo I", "rate_pct": "9.50",
                  "valid_from": "2026-01-01"},
        )
        assert editar.json()["rate_pct"] == "9.50"

        assert (await cliente.delete(f"/api/v1/costs/tax-rules/{regra_id}")).status_code == 200
        assert (await cliente.get("/api/v1/costs/tax-rules")).json() == []

    async def test_vigencia_invertida_e_recusada(self, cliente):
        resposta = await cliente.post(
            "/api/v1/costs/tax-rules",
            json={"name": "Errada", "rate_pct": "5.00",
                  "valid_from": "2026-06-01", "valid_to": "2026-01-01"},
        )
        assert resposta.status_code == 409

    async def test_aliquota_acima_de_cem_por_cento_e_recusada(self, cliente):
        resposta = await cliente.post(
            "/api/v1/costs/tax-rules",
            json={"name": "Absurda", "rate_pct": "150.00", "valid_from": "2026-01-01"},
        )
        assert resposta.status_code == 422

    async def test_ciclo_completo_da_despesa(self, cliente):
        criar = await cliente.post(
            "/api/v1/costs/expenses",
            json={"description": "Aluguel", "category": "rent", "amount": "2500.00",
                  "competence_month": "2026-08-15"},
        )
        assert criar.status_code == 201
        # A competência é sempre normalizada para o primeiro dia do mês.
        assert criar.json()["competence_month"] == "2026-08-01"

        despesa_id = criar.json()["id"]
        editar = await cliente.patch(
            f"/api/v1/costs/expenses/{despesa_id}",
            json={"description": "Aluguel reajustado", "category": "rent",
                  "amount": "2800.00", "competence_month": "2026-08-01"},
        )
        assert editar.json()["amount"] == "2800.00"

        assert (await cliente.delete(f"/api/v1/costs/expenses/{despesa_id}")).status_code == 200

    async def test_replica_recorrentes_sem_duplicar(self, cliente):
        await cliente.post(
            "/api/v1/costs/expenses",
            json={"description": "Aluguel", "category": "rent", "amount": "2500.00",
                  "competence_month": "2026-07-01", "is_recurring": True},
        )
        await cliente.post(
            "/api/v1/costs/expenses",
            json={"description": "Bônus pontual", "category": "payroll",
                  "amount": "900.00", "competence_month": "2026-07-01"},
        )

        primeira = await cliente.post(
            "/api/v1/costs/expenses/replicate",
            params={"origem": "2026-07-01", "destino": "2026-08-01"},
        )
        assert primeira.json()["dados"]["criadas"] == 1  # só a recorrente

        # Rodar de novo não pode duplicar o aluguel do mês de destino.
        segunda = await cliente.post(
            "/api/v1/costs/expenses/replicate",
            params={"origem": "2026-07-01", "destino": "2026-08-01"},
        )
        assert segunda.json()["dados"]["criadas"] == 0

    async def test_fechamento_de_mes_e_unico(self, cliente):
        primeiro = await cliente.post("/api/v1/costs/close-month", params={"mes": "2026-07-01"})
        assert primeiro.status_code == 200

        repetido = await cliente.post("/api/v1/costs/close-month", params={"mes": "2026-07-15"})
        assert repetido.status_code == 409  # mesmo mês, dia diferente

    async def test_leitor_nao_altera_custos(self, engine, db, tenant):
        """Cor de botão escondido é UX; o bloqueio precisa ser no servidor."""
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.core.security import criar_access_token, hash_senha
        from app.db.session import get_db
        from app.main import app
        from app.models.tenant import User

        leitor = User(
            tenant_id=tenant.id, email="leitor2@exemplo.com.br",
            password_hash=hash_senha("senha-de-teste-123"), role=PapelUsuario.LEITOR,
        )
        db.add(leitor)
        await db.commit()

        fabrica = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        async def sobrepor():
            async with fabrica() as s:
                yield s

        app.dependency_overrides[get_db] = sobrepor
        token = criar_access_token(
            user_id=leitor.id, tenant_id=tenant.id, role=PapelUsuario.LEITOR
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://teste"
            ) as http:
                http.headers["Authorization"] = f"Bearer {token}"
                assert (await http.get("/api/v1/costs/dre")).status_code == 200
                criar = await http.post(
                    "/api/v1/costs/expenses",
                    json={"description": "X", "amount": "1", "competence_month": "2026-08-01"},
                )
                assert criar.status_code == 403
        finally:
            app.dependency_overrides.clear()


class TestMarketing:
    async def test_lanca_custo_de_midia_manualmente(self, cliente, db, conta):
        """A Ads API exige whitelist; sem lançamento manual a rentabilidade
        da campanha ficaria estruturalmente incompleta."""
        from datetime import UTC, datetime

        from app.models.marketing import Campaign

        campanha = Campaign(
            tenant_id=conta.tenant_id, channel_account_id=conta.id, channel=conta.channel,
            external_id="CAMP-1", name="Dia das Mães", type="discount",
            start_at=datetime.now(UTC),
        )
        db.add(campanha)
        await db.commit()

        resposta = await cliente.patch(
            f"/api/v1/marketing/campaigns/{campanha.id}",
            json={"manual_media_cost": "1500.00"},
        )
        assert resposta.status_code == 200
        assert resposta.json()["manual_media_cost"] == "1500.00"

        listagem = (await cliente.get("/api/v1/marketing/campaigns")).json()
        assert listagem[0]["custo_midia"] == "1500.00"
