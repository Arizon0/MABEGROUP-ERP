"""Simples Nacional progressivo, RBT12 e custo de aquisição do estoque.

Os dois assuntos que um contador confere primeiro. A alíquota do Simples não é
um número fixo — é função do faturamento dos últimos 12 meses — e o custo do
estoque não é o preço do fornecedor, é o preço posto no galpão.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.catalog import Product
from app.models.costs import BaseImposto, RegimeTributario, TaxBracket, TaxRule
from app.models.enums import StatusPedido
from app.models.order import Order
from app.seed import ANEXO_I_COMERCIO
from app.services import taxes

ZERO = Decimal("0")


def _regra_progressiva() -> TaxRule:
    regra = TaxRule(
        id=1,
        tenant_id=1,
        name="Simples — Anexo I",
        regime=RegimeTributario.SIMPLES_PROGRESSIVO,
        base=BaseImposto.RECEITA_BRUTA,
        valid_from=date(2020, 1, 1),
    )
    regra.brackets = [
        TaxBracket(
            tenant_id=1, rbt12_ate=teto, aliquota_nominal_pct=nominal, parcela_deduzir=pd
        )
        for teto, nominal, pd in ANEXO_I_COMERCIO
    ]
    return regra


class TestAliquotaEfetiva:
    """(RBT12 × nominal − parcela a deduzir) ÷ RBT12 — art. 18 da LC 123/2006."""

    def test_primeira_faixa_nao_tem_deducao(self):
        """Na faixa 1 a parcela a deduzir é zero, então efetiva = nominal."""
        regra = _regra_progressiva()
        assert taxes.aliquota_efetiva_simples(regra, Decimal("120000")) == Decimal("4.0000")

    def test_segunda_faixa_fica_abaixo_da_nominal(self):
        """A parcela a deduzir existe justamente para isso.

        Com RBT12 de 300 mil na faixa de 7,3%: (300000×0,073 − 5940)/300000 =
        5,32%. Cobrar os 7,3% cheios seria quase 40% de imposto a mais.
        """
        regra = _regra_progressiva()
        assert taxes.aliquota_efetiva_simples(regra, Decimal("300000")) == Decimal("5.3200")

    def test_nao_ha_salto_de_degrau_ao_cruzar_a_faixa(self):
        """R$ 1 a mais de faturamento não pode custar milhares em tributo.

        É o efeito que a parcela a deduzir elimina, e a razão de a tabela não
        poder ser aplicada como alíquota fixa por faixa.
        """
        regra = _regra_progressiva()
        antes = taxes.aliquota_efetiva_simples(regra, Decimal("360000"))
        depois = taxes.aliquota_efetiva_simples(regra, Decimal("360001"))
        assert abs(depois - antes) < Decimal("0.01")

    def test_alicota_cresce_junto_com_o_faturamento(self):
        regra = _regra_progressiva()
        valores = [
            taxes.aliquota_efetiva_simples(regra, Decimal(v))
            for v in ("100000", "300000", "600000", "1500000", "3000000")
        ]
        assert valores == sorted(valores)

    def test_acima_do_teto_usa_a_ultima_faixa_e_sinaliza(self):
        """Estourar o limite do Simples não pode zerar o imposto — nem passar batido.

        Acima do teto a empresa está desenquadrada e precisa mudar de regime.
        O cálculo segue pela última faixa, porque zerar seria pior, mas o
        painel precisa dizer que o número deixou de valer.
        """
        regra = _regra_progressiva()
        rbt12 = Decimal("9000000")
        # (9.000.000 × 0,19 − 378.000) ÷ 9.000.000 = 14,8%
        assert taxes.aliquota_efetiva_simples(regra, rbt12) == Decimal("14.8000")
        assert taxes.excedeu_o_teto(regra, rbt12) is True
        assert taxes.excedeu_o_teto(regra, Decimal("4000000")) is False

    def test_sem_faixas_cai_na_aliquota_fixa(self):
        """Regra de Lucro Presumido continua funcionando pelo mesmo caminho."""
        regra = TaxRule(
            id=2, tenant_id=1, name="Presumido", rate_pct=Decimal("11.33"),
            valid_from=date(2020, 1, 1),
        )
        regra.brackets = []
        assert taxes.aliquota_efetiva_simples(regra, Decimal("500000")) == Decimal("11.33")


class TestProporcionalizacao:
    """Início de atividade: média dos meses em operação × 12."""

    def test_seis_meses_projetam_o_dobro(self):
        assert taxes.rbt12_proporcionalizada(Decimal("300000"), 6) == Decimal("600000.00")

    def test_doze_meses_ou_mais_usam_o_valor_cheio(self):
        assert taxes.rbt12_proporcionalizada(Decimal("300000"), 12) == Decimal("300000")

    def test_empresa_nova_nao_fica_presa_na_faixa_mais_baixa(self):
        """Sem proporcionalizar, um negócio novo recolheria abaixo do devido."""
        regra = _regra_progressiva()
        bruto, meses = Decimal("300000"), 6
        crua = taxes.aliquota_efetiva_simples(regra, bruto)
        proporcional = taxes.aliquota_efetiva_simples(
            regra, taxes.rbt12_proporcionalizada(bruto, meses)
        )
        assert proporcional > crua

    def test_sem_historico_devolve_zero(self):
        assert taxes.rbt12_proporcionalizada(Decimal("1000"), 0) == ZERO


class TestRBT12:
    async def test_exclui_o_mes_corrente(self, db, conta):
        """A RBT12 olha os 12 meses anteriores.

        Incluir o mês em curso faria a alíquota mudar a cada venda, e ninguém
        conseguiria conferir a apuração.
        """
        agora = datetime.now(UTC)
        db.add(
            Order(
                tenant_id=conta.tenant_id, channel_account_id=conta.id,
                channel=conta.channel, external_id="MES-CORRENTE",
                status=StatusPedido.ENTREGUE, date_created=agora,
                gross_amount=Decimal("50000"), net_amount=Decimal("40000"),
            )
        )
        await db.commit()

        total, _ = await taxes.calcular_rbt12(
            db, conta.tenant_id, referencia=agora.date()
        )
        assert total == ZERO

    async def test_cancelado_nao_entra_na_base(self, db, conta):
        """Venda desfeita não é receita bruta."""
        mes_passado = datetime.now(UTC) - timedelta(days=40)
        for externo, status in (("OK", StatusPedido.ENTREGUE), ("X", StatusPedido.CANCELADO)):
            db.add(
                Order(
                    tenant_id=conta.tenant_id, channel_account_id=conta.id,
                    channel=conta.channel, external_id=f"RBT-{externo}",
                    status=status, date_created=mes_passado,
                    gross_amount=Decimal("10000"), net_amount=Decimal("8000"),
                )
            )
        await db.commit()

        total, _ = await taxes.calcular_rbt12(
            db, conta.tenant_id, referencia=datetime.now(UTC).date()
        )
        assert total == Decimal("10000.0000")


class TestCustoDeAquisicao:
    """O custo do estoque é o preço posto no galpão, não o do fornecedor."""

    def test_frete_de_compra_integra_o_custo(self):
        produto = Product(
            tenant_id=1, sku="5338", unit_cost=Decimal("14.20"),
            freight_in_cost=Decimal("1.80"), other_acquisition_cost=Decimal("0.50"),
            packaging_cost=Decimal("0.90"),
        )
        assert produto.custo_aquisicao == Decimal("16.50")
        assert produto.custo_total_unitario == Decimal("17.40")

    def test_ignorar_o_frete_infla_a_margem(self):
        """A diferença é exatamente o que apareceria como lucro que não existe."""
        produto = Product(
            tenant_id=1, sku="9104", unit_cost=Decimal("104.60"),
            freight_in_cost=Decimal("8.40"), packaging_cost=Decimal("1.20"),
        )
        preco = Decimal("189.90")
        margem_correta = preco - produto.custo_total_unitario
        margem_sem_frete = preco - produto.unit_cost - produto.packaging_cost
        assert margem_sem_frete - margem_correta == Decimal("8.40")

    def test_produto_sem_custos_extras_nao_muda_de_comportamento(self):
        produto = Product(tenant_id=1, sku="X", unit_cost=Decimal("10.00"))
        assert produto.custo_total_unitario == Decimal("10.00")


class TestRateioDeFrete:
    async def test_rateia_por_quantidade_e_grava_no_produto(self, cliente, db):
        await cliente.post(
            "/api/v1/catalog/products",
            json={"sku": "RT-1", "name": "Retentor", "unit_cost": "10.00"},
        )
        await cliente.post(
            "/api/v1/catalog/products",
            json={"sku": "RT-2", "name": "Anel", "unit_cost": "20.00"},
        )

        resposta = await cliente.post(
            "/api/v1/catalog/products/freight-in",
            json={
                "frete_total": "300.00",
                "criterio": "quantidade",
                "itens": [
                    {"sku": "RT-1", "quantidade": "100"},
                    {"sku": "RT-2", "quantidade": "50"},
                ],
            },
        )
        assert resposta.status_code == 200
        itens = {i["sku"]: i for i in resposta.json()["dados"]["itens"]}
        # 300 / 150 unidades = 2,00 por unidade, para os dois SKUs.
        assert itens["RT-1"]["frete_por_unidade"] == "2.0000"
        assert itens["RT-2"]["frete_por_unidade"] == "2.0000"

        produto = await db.scalar(select(Product).where(Product.sku == "RT-1"))
        await db.refresh(produto)
        assert produto.freight_in_cost == Decimal("2.0000")

    async def test_rateio_por_valor_pesa_o_item_mais_caro(self, cliente):
        await cliente.post(
            "/api/v1/catalog/products",
            json={"sku": "VL-1", "name": "Barato", "unit_cost": "5.00"},
        )
        await cliente.post(
            "/api/v1/catalog/products",
            json={"sku": "VL-2", "name": "Caro", "unit_cost": "95.00"},
        )

        resposta = await cliente.post(
            "/api/v1/catalog/products/freight-in",
            json={
                "frete_total": "100.00",
                "criterio": "valor",
                "itens": [
                    {"sku": "VL-1", "quantidade": "10", "valor_total": "50.00"},
                    {"sku": "VL-2", "quantidade": "10", "valor_total": "950.00"},
                ],
            },
        )
        itens = {i["sku"]: i for i in resposta.json()["dados"]["itens"]}
        assert Decimal(itens["VL-2"]["frete_rateado"]) > Decimal(itens["VL-1"]["frete_rateado"])
        soma = sum(Decimal(i["frete_rateado"]) for i in itens.values())
        assert soma == Decimal("100.00")

    async def test_simulacao_nao_grava(self, cliente, db):
        await cliente.post(
            "/api/v1/catalog/products",
            json={"sku": "SIM-1", "name": "Teste", "unit_cost": "10.00"},
        )
        await cliente.post(
            "/api/v1/catalog/products/freight-in",
            json={
                "frete_total": "500.00",
                "aplicar": False,
                "itens": [{"sku": "SIM-1", "quantidade": "10"}],
            },
        )
        produto = await db.scalar(select(Product).where(Product.sku == "SIM-1"))
        assert produto.freight_in_cost == ZERO

    async def test_rateio_por_valor_sem_valores_e_recusado(self, cliente):
        """Dividir por zero silenciosamente daria frete zero e CMV errado."""
        resposta = await cliente.post(
            "/api/v1/catalog/products/freight-in",
            json={
                "frete_total": "100.00",
                "criterio": "valor",
                "itens": [{"sku": "QQ", "quantidade": "1", "valor_total": "0"}],
            },
        )
        assert resposta.status_code == 409


class TestContasAReceber:
    async def test_soma_pendente_e_separa_liberado(self, cliente):
        resposta = await cliente.get("/api/v1/finance/receivables")
        assert resposta.status_code == 200
        corpo = resposta.json()
        assert "total_a_receber" in corpo["resumo"]
        assert set(corpo["por_faixa"]) == {
            "vencido", "ate_7_dias", "ate_30_dias", "acima_de_30", "sem_previsao",
        }
