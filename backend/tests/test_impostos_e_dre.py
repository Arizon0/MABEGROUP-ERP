"""Impostos, DRE e lucro real.

Os cálculos aqui decidem preço e pró-labore. Um erro não aparece como tela
quebrada — aparece como um lucro que não existe.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal


from app.models.costs import BaseImposto, OperatingExpense, TaxRule
from app.models.enums import StatusPedido
from app.models.order import Order, OrderItem
from app.services import dre as servico_dre, taxes


async def _pedido(
    db, conta, *, bruto="1000.00", liquido="800.00", cmv="300.00",
    dias_atras=5, status=StatusPedido.ENTREGUE, frete_cobrado="0", devolucao="0",
) -> Order:
    pedido = Order(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=f"P{datetime.now(UTC).timestamp()}{dias_atras}{bruto}",
        status=status,
        date_created=datetime.now(UTC) - timedelta(days=dias_atras),
        gross_amount=Decimal(bruto),
        shipping_revenue=Decimal(frete_cobrado),
        net_amount=Decimal(liquido),
        cogs=Decimal(cmv),
        refund_amount=Decimal(devolucao),
    )
    db.add(pedido)
    await db.flush()
    db.add(
        OrderItem(
            tenant_id=conta.tenant_id,
            order_id=pedido.id,
            sku_channel="SKU-1",
            quantity=Decimal("1"),
            unit_price=Decimal(bruto),
            gross_amount=Decimal(bruto),
            unit_cost=Decimal(cmv),
            cogs=Decimal(cmv),
        )
    )
    await db.commit()
    return pedido


async def _regra(db, tenant_id, *, aliquota="8.00", base=BaseImposto.RECEITA_BRUTA,
                 desde=None, ate=None, canal="") -> TaxRule:
    regra = TaxRule(
        tenant_id=tenant_id,
        name=f"Simples {aliquota}%",
        kind="simples_nacional",
        rate_pct=Decimal(aliquota),
        base=base,
        channel=canal,
        valid_from=desde or (date.today() - timedelta(days=365)),
        valid_to=ate,
    )
    db.add(regra)
    await db.commit()
    return regra


class TestApuracaoDeImposto:
    async def test_aplica_aliquota_sobre_a_receita_bruta(self, db, conta):
        await _pedido(db, conta, bruto="1000.00")
        await _regra(db, conta.tenant_id, aliquota="8.00")

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.pedidos == 1
        assert resultado.imposto_total == Decimal("80.00")

    async def test_base_pode_incluir_o_frete_cobrado(self, db, conta):
        await _pedido(db, conta, bruto="1000.00", frete_cobrado="100.00")
        await _regra(db, conta.tenant_id, aliquota="10.00", base=BaseImposto.BRUTA_MAIS_FRETE)

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.imposto_total == Decimal("110.00")

    async def test_soma_varios_tributos_vigentes(self, db, conta):
        """Lucro Presumido tem PIS, COFINS, IRPJ e CSLL separados."""
        await _pedido(db, conta, bruto="1000.00")
        await _regra(db, conta.tenant_id, aliquota="1.65")   # PIS
        await _regra(db, conta.tenant_id, aliquota="7.60")   # COFINS

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.imposto_total == Decimal("92.50")

    async def test_usa_a_regra_vigente_na_data_da_venda(self, db, conta):
        """No Simples a alíquota muda de faixa.

        Apurar um pedido antigo com a alíquota de hoje reescreveria um mês já
        fechado pelo contador — por isso a vigência manda, não a data atual.
        """
        hoje = date.today()
        await _pedido(db, conta, bruto="1000.00", dias_atras=40)  # regra antiga
        await _pedido(db, conta, bruto="1000.00", dias_atras=2)   # regra nova

        await _regra(db, conta.tenant_id, aliquota="6.00",
                     desde=hoje - timedelta(days=90), ate=hoje - timedelta(days=30))
        await _regra(db, conta.tenant_id, aliquota="11.00",
                     desde=hoje - timedelta(days=29))

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=60), fim=datetime.now(UTC),
        )
        # 1000×6% + 1000×11% — e não 2000 pela mesma alíquota.
        assert resultado.imposto_total == Decimal("170.00")

    async def test_pedido_cancelado_nao_gera_tributo(self, db, conta):
        await _pedido(db, conta, bruto="1000.00", status=StatusPedido.CANCELADO)
        await _regra(db, conta.tenant_id, aliquota="8.00")

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.imposto_total == Decimal("0")

    async def test_devolucao_reduz_a_base_tributavel(self, db, conta):
        await _pedido(db, conta, bruto="1000.00", devolucao="200.00")
        await _regra(db, conta.tenant_id, aliquota="10.00")

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.imposto_total == Decimal("80.00")  # sobre 800, não 1000

    async def test_regra_por_canal_nao_afeta_os_outros(self, db, conta, conta_shopee):
        await _pedido(db, conta, bruto="1000.00")
        await _pedido(db, conta_shopee, bruto="1000.00")
        await _regra(db, conta.tenant_id, aliquota="9.00", canal="mercadolivre")

        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.imposto_total == Decimal("90.00")
        assert resultado.sem_regra == 1  # a Shopee ficou sem regra e isso é sinalizado

    async def test_sem_regra_cadastrada_o_imposto_e_zero_e_sinalizado(self, db, conta):
        await _pedido(db, conta, bruto="1000.00")
        resultado = await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert resultado.imposto_total == Decimal("0")
        assert resultado.sem_regra == 1


class TestDRE:
    async def test_cadeia_completa_ate_o_lucro_operacional(self, db, conta):
        # Bruto 1000, líquido do canal 800, CMV 300, imposto 8%, despesa 100.
        await _pedido(db, conta, bruto="1000.00", liquido="800.00", cmv="300.00")
        await _regra(db, conta.tenant_id, aliquota="8.00")
        await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        db.add(
            OperatingExpense(
                tenant_id=conta.tenant_id,
                description="Aluguel",
                category="rent",
                amount=Decimal("100.00"),
                competence_month=date.today().replace(day=1),
            )
        )
        await db.commit()

        resultado = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )

        assert resultado.receita_bruta == Decimal("1000.00")
        assert resultado.liquido_recebido == Decimal("800.00")
        assert resultado.imposto_sobre_vendas == Decimal("80.00")
        assert resultado.cmv == Decimal("300.00")
        # 800 − 80 − 300
        assert resultado.margem_contribuicao == Decimal("420.00")
        # 420 − 100
        assert resultado.lucro_operacional == Decimal("320.00")
        assert resultado.despesas == Decimal("100.00")

    async def test_imposto_do_vendedor_nao_reduz_o_liquido_recebido(self, db, conta):
        """O canal deposita o valor cheio; o tributo é recolhido depois.

        Subtrair o imposto do líquido contaria o tributo duas vezes: uma no
        líquido e outra na linha de imposto do DRE.
        """
        pedido = await _pedido(db, conta, bruto="1000.00", liquido="800.00")
        await _regra(db, conta.tenant_id, aliquota="8.00")
        await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        await db.refresh(pedido)

        assert pedido.net_amount == Decimal("800.00")      # intacto
        assert pedido.sales_tax_amount == Decimal("80.00")  # linha própria

    async def test_ordem_das_grandezas_e_sempre_coerente(self, db, conta):
        """Lucro ≤ margem ≤ líquido ≤ bruto. Quebrar isso é bug de cálculo."""
        await _pedido(db, conta, bruto="1000.00", liquido="800.00", cmv="300.00")
        await _regra(db, conta.tenant_id, aliquota="8.00")
        await taxes.apurar_periodo(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        db.add(
            OperatingExpense(
                tenant_id=conta.tenant_id, description="Software", category="software",
                amount=Decimal("50.00"), competence_month=date.today().replace(day=1),
            )
        )
        await db.commit()

        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert r.lucro_operacional <= r.margem_contribuicao
        assert r.margem_contribuicao <= r.liquido_recebido
        assert r.liquido_recebido <= r.receita_bruta

    async def test_prejuizo_aparece_como_negativo_e_nao_como_zero(self, db, conta):
        """Esconder prejuízo é pior que mostrá-lo."""
        await _pedido(db, conta, bruto="100.00", liquido="70.00", cmv="90.00")
        db.add(
            OperatingExpense(
                tenant_id=conta.tenant_id, description="Aluguel", category="rent",
                amount=Decimal("500.00"), competence_month=date.today().replace(day=1),
            )
        )
        await db.commit()

        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert r.margem_contribuicao == Decimal("-20.00")
        assert r.lucro_operacional == Decimal("-520.00")

    async def test_sinaliza_custo_faltando_em_vez_de_inflar_o_lucro(self, db, conta):
        await _pedido(db, conta, bruto="1000.00", liquido="800.00", cmv="0")

        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        saida = r.como_dict()
        assert saida["qualidade"]["confiavel"] is False
        assert "sem custo cadastrado" in saida["qualidade"]["aviso"]
        assert "sem regra tributária" in saida["qualidade"]["aviso"]

    async def test_ponto_de_equilibrio_indica_a_receita_necessaria(self, db, conta):
        # Margem de 40% sobre o bruto e R$ 400 de despesa → equilíbrio em R$ 1.000.
        await _pedido(db, conta, bruto="1000.00", liquido="700.00", cmv="300.00")
        db.add(
            OperatingExpense(
                tenant_id=conta.tenant_id, description="Fixas", category="rent",
                amount=Decimal("400.00"), competence_month=date.today().replace(day=1),
            )
        )
        await db.commit()

        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert r.ponto_de_equilibrio == Decimal("1000.00")

    async def test_sem_margem_positiva_nao_existe_ponto_de_equilibrio(self, db, conta):
        """Com margem negativa nenhum volume cobre as despesas."""
        await _pedido(db, conta, bruto="100.00", liquido="50.00", cmv="90.00")
        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert r.ponto_de_equilibrio == Decimal("0")

    async def test_cancelado_sai_da_receita_e_entra_como_deducao(self, db, conta):
        await _pedido(db, conta, bruto="1000.00", liquido="800.00", cmv="300.00")
        await _pedido(db, conta, bruto="500.00", status=StatusPedido.CANCELADO)

        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        assert r.receita_bruta == Decimal("1000.00")
        assert r.cancelamentos == Decimal("500.00")

    async def test_linhas_do_dre_estao_na_ordem_contabil(self, db, conta):
        await _pedido(db, conta, bruto="1000.00")
        r = await servico_dre.apurar(
            db, conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=30), fim=datetime.now(UTC),
        )
        rotulos = [linha.rotulo for linha in r.linhas()]

        assert rotulos[0].startswith("Receita bruta")
        assert rotulos[-1].startswith("(=) Lucro operacional")
        assert rotulos.index("(=) Margem de contribuição") < rotulos.index(
            "(=) Lucro operacional"
        )

    async def test_serie_mensal_cobre_o_numero_de_meses_pedido(self, db, conta):
        await _pedido(db, conta, bruto="1000.00")
        serie = await servico_dre.apurar_por_mes(db, conta.tenant_id, meses=3)
        assert len(serie) == 3
        assert all("lucro_operacional" in mes for mes in serie)


class TestAritmeticaDeMes:
    """Funções puras — sem banco, sem async."""

    def test_soma_de_mes_nao_quebra_no_dia_31(self):
        assert servico_dre.somar_meses(datetime(2026, 1, 31, tzinfo=UTC)  , 1).date() == date(2026, 2, 1)

    def test_atravessa_a_virada_de_ano_nos_dois_sentidos(self):
        assert servico_dre.somar_meses(datetime(2026, 1, 15, tzinfo=UTC), -1).date() == date(2025, 12, 1)
        assert servico_dre.somar_meses(datetime(2026, 12, 15, tzinfo=UTC), 1).date() == date(2027, 1, 1)
