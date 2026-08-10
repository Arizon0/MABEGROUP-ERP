"""Auditoria da cadeia inteira: sincronizar → tributar → apurar o lucro real.

Os testes de unidade provam cada fórmula isolada. Este prova o que só aparece na
costura: que os números continuam coerentes entre si depois de passarem por
ingestão, normalização, imposto, CMV e despesa fixa — e que nenhuma etapa
introduz dinheiro que não existe.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.costs import BaseImposto, CategoriaDespesa, OperatingExpense, TaxRule
from app.models.order import Order
from app.services import analytics, dre as servico_dre, sync, taxes

ZERO = Decimal("0")


async def _cenario_completo(db, conta) -> None:
    """Sincroniza os pedidos simulados e cadastra imposto e despesa fixa."""
    await sync.sincronizar_pedidos(db, conta)
    await _tributar(db, conta)


async def _tributar(db, conta) -> None:
    """Cadastra a regra tributária e a despesa fixa, e apura o período."""
    db.add(
        TaxRule(
            tenant_id=conta.tenant_id,
            name="Simples Nacional — Anexo I",
            rate_pct=Decimal("8.00"),
            base=BaseImposto.RECEITA_BRUTA,
            valid_from=date(2020, 1, 1),
        )
    )
    db.add(
        OperatingExpense(
            tenant_id=conta.tenant_id,
            description="Aluguel",
            category=CategoriaDespesa.ALUGUEL,
            amount=Decimal("2500.00"),
            competence_month=datetime.now(UTC).date().replace(day=1),
        )
    )
    await db.commit()
    await taxes.apurar_periodo(
        db,
        conta.tenant_id,
        inicio=datetime.now(UTC) - timedelta(days=180),
        fim=datetime.now(UTC) + timedelta(days=1),
    )
    await db.commit()


async def _dre(db, conta):
    return await servico_dre.apurar(
        db,
        conta.tenant_id,
        inicio=datetime.now(UTC) - timedelta(days=180),
        fim=datetime.now(UTC) + timedelta(days=1),
    )


class TestCadeiaFinanceira:
    async def test_ordem_dos_resultados_se_mantem_com_dados_reais(self, db, conta):
        """Lucro ≤ margem ≤ líquido ≤ bruto.

        A desigualdade é o teste mais barato que existe contra dinheiro
        inventado: qualquer duplicação de pagamento ou dedução com sinal trocado
        quebra a ordem antes de quebrar qualquer outra coisa.
        """
        await _cenario_completo(db, conta)
        d = await _dre(db, conta)

        assert d.receita_bruta > ZERO
        assert d.liquido_recebido <= d.receita_bruta + d.receita_frete
        assert d.margem_contribuicao <= d.liquido_recebido
        assert d.lucro_operacional <= d.margem_contribuicao

    async def test_imposto_do_vendedor_nao_reduz_o_liquido_recebido(self, db, conta):
        """O canal repassa o valor cheio; o tributo é recolhido depois.

        Se o imposto sobre vendas encostasse no líquido, o painel passaria a
        divergir do extrato do marketplace — e o tributo seria contado duas
        vezes no lucro.
        """
        # Sincroniza uma única vez: o conector simulado gera pedidos novos a
        # cada janela, e re-sincronizar entre as duas medições mudaria o
        # conjunto comparado em vez de isolar o efeito do imposto.
        await sync.sincronizar_pedidos(db, conta)
        antes = await _dre(db, conta)

        await _tributar(db, conta)
        depois = await _dre(db, conta)

        assert depois.imposto_sobre_vendas > ZERO
        assert depois.liquido_recebido == antes.liquido_recebido
        assert depois.margem_contribuicao < antes.margem_contribuicao

    async def test_soma_das_linhas_do_dre_fecha_no_lucro(self, db, conta):
        """A coluna exibida fecha no total impresso.

        Quem confere um demonstrativo soma a coluna. Se o resultado da soma não
        for o total exibido, o relatório perde a serventia — por isso a linha de
        ajustes não discriminados existe: ela absorve a diferença entre o
        líquido informado pelo canal e a soma das taxas detalhadas.
        """
        await _cenario_completo(db, conta)
        d = await _dre(db, conta)

        soma = sum(
            (linha.valor for linha in d.linhas() if linha.tipo in ("receita", "deducao")),
            ZERO,
        )
        assert abs(soma - d.lucro_operacional) < Decimal("0.01")

    async def test_o_ajuste_reconcilia_o_liquido_informado_com_as_taxas(self, db, conta):
        """O líquido é importado do canal, nunca recalculado a partir das taxas."""
        await _cenario_completo(db, conta)
        d = await _dre(db, conta)

        assert d.componentes_do_liquido + d.ajustes_nao_discriminados == d.liquido_recebido

    async def test_o_dre_avisa_quando_o_imposto_nao_foi_apurado(self, db, conta):
        """Sem regra vigente o lucro exibido é maior que o real — e precisa dizer isso."""
        await sync.sincronizar_pedidos(db, conta)
        d = await _dre(db, conta)

        assert d.imposto_sobre_vendas == ZERO
        assert d.pedidos_sem_imposto > 0
        assert d.como_dict()["qualidade"]["confiavel"] is False
        assert "regra tributária" in d.como_dict()["qualidade"]["aviso"]

    async def test_periodo_sem_movimento_devolve_zeros_e_nao_divide_por_zero(self, db, conta):
        """Vazio é um estado normal do painel, não uma exceção."""
        await _cenario_completo(db, conta)
        vazio = await servico_dre.apurar(
            db,
            conta.tenant_id,
            inicio=datetime(2019, 1, 1, tzinfo=UTC),
            fim=datetime(2019, 2, 1, tzinfo=UTC),
        )

        assert vazio.receita_bruta == ZERO
        assert vazio.lucro_operacional == ZERO
        assert vazio.ticket_medio == ZERO
        assert vazio.lucro_operacional_pct == ZERO
        assert vazio.ponto_de_equilibrio == ZERO


class TestConsistenciaEntrePaineis:
    async def test_receita_do_dre_bate_com_a_da_visao_geral(self, db, conta):
        """Dois módulos, uma verdade.

        Visão geral e DRE consultam o mesmo período por caminhos diferentes.
        Divergir aqui significaria o vendedor ver dois faturamentos no mesmo
        painel — e não saber em qual acreditar.
        """
        await _cenario_completo(db, conta)

        inicio = datetime.now(UTC) - timedelta(days=180)
        fim = datetime.now(UTC) + timedelta(days=1)
        visao = await analytics.visao_geral(
            db, analytics.Filtro(tenant_id=conta.tenant_id, inicio=inicio, fim=fim)
        )
        d = await servico_dre.apurar(db, conta.tenant_id, inicio=inicio, fim=fim)

        assert Decimal(visao["kpis"]["receita_bruta"]["valor"]) == servico_dre.arredondar(
            d.receita_bruta
        )

    async def test_reapuracao_e_idempotente(self, db, conta):
        """Rodar de novo corrige o passado sem multiplicar o imposto."""
        await _cenario_completo(db, conta)
        primeira = await _dre(db, conta)

        await taxes.apurar_periodo(
            db,
            conta.tenant_id,
            inicio=datetime.now(UTC) - timedelta(days=180),
            fim=datetime.now(UTC) + timedelta(days=1),
        )
        await db.commit()
        segunda = await _dre(db, conta)

        assert segunda.imposto_sobre_vendas == primeira.imposto_sobre_vendas
        assert segunda.lucro_operacional == primeira.lucro_operacional

    async def test_todo_pedido_tributado_aponta_a_regra_usada(self, db, conta):
        """Auditoria fiscal precisa reconstruir a conta, não só ver o total."""
        await _cenario_completo(db, conta)

        sem_rastro = await db.scalar(
            select(func.count(Order.id)).where(
                Order.tenant_id == conta.tenant_id,
                Order.sales_tax_amount > 0,
                Order.tax_rule_id.is_(None),
            )
        )
        assert sem_rastro == 0
