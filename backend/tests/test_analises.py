"""Curva ABC, coorte de compradores e média móvel.

São análises de decisão: a ABC diz onde concentrar capital de giro, a coorte
diz se o cliente volta, a média móvel diz se a queda de hoje é tendência ou
sábado. Erro aqui não quebra tela — muda decisão.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import StatusPedido
from app.models.order import Order, OrderItem
from app.services import analytics

ZERO = Decimal("0")


def _filtro(conta, dias: int = 365) -> analytics.Filtro:
    return analytics.Filtro(
        tenant_id=conta.tenant_id,
        inicio=datetime.now(UTC) - timedelta(days=dias),
        fim=datetime.now(UTC) + timedelta(days=1),
    )


async def _venda(
    db, conta, *, sku: str, receita: str, cmv: str = "0", qtd: int = 1,
    dias_atras: int = 5, comprador: str | None = None, externo: str | None = None,
) -> Order:
    pedido = Order(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=externo or f"{sku}-{dias_atras}-{receita}-{comprador or ''}",
        status=StatusPedido.ENTREGUE,
        date_created=datetime.now(UTC) - timedelta(days=dias_atras),
        gross_amount=Decimal(receita),
        net_amount=Decimal(receita),
        buyer_hash=comprador,
    )
    db.add(pedido)
    await db.flush()
    db.add(
        OrderItem(
            tenant_id=conta.tenant_id,
            order_id=pedido.id,
            sku_base=sku,
            sku_channel=sku,
            title=f"Produto {sku}",
            quantity=Decimal(qtd),
            gross_amount=Decimal(receita),
            cogs=Decimal(cmv),
        )
    )
    await db.commit()
    return pedido


class TestCurvaABC:
    async def test_classifica_pelo_acumulado_e_nao_pela_posicao(self, db, conta):
        """Um item que sozinho faz 80% da receita é classe A sozinho.

        Se o corte fosse por posição no ranking (os 20% primeiros), o resultado
        seria sempre o mesmo número de itens — e a análise não diria nada.
        """
        await _venda(db, conta, sku="DOMINANTE", receita="8000")
        for i in range(9):
            await _venda(db, conta, sku=f"CAUDA-{i}", receita="200")

        resultado = await analytics.curva_abc(db, _filtro(conta))
        classes = {i["sku"]: i["classe"] for i in resultado["itens"]}

        assert classes["DOMINANTE"] == "A"
        assert sum(1 for c in classes.values() if c == "A") == 1

    async def test_o_item_que_cruza_o_corte_fica_na_classe_de_baixo(self, db, conta):
        """O SKU que atinge os 80% ainda é o que sustenta o faturamento."""
        for i in range(4):
            await _venda(db, conta, sku=f"IGUAL-{i}", receita="2500")

        resultado = await analytics.curva_abc(db, _filtro(conta))
        # Quatro itens iguais: o acumulado ANTES de cada um é 0/25/50/75, todos
        # abaixo de 80. Distribuição uniforme não tem "poucos vitais" — todo
        # item pesa igual, e classificar todos como A é a leitura honesta.
        classes = [i["classe"] for i in resultado["itens"]]
        assert classes == ["A", "A", "A", "A"]

    async def test_produto_unico_e_classe_a(self, db, conta):
        """O item que é o negócio inteiro não pode sair como irrelevante.

        Com um SKU só, o acumulado chega a 100% nele — e classificar pelo
        acumulado já somado o colocaria em C.
        """
        await _venda(db, conta, sku="UNICO", receita="5000")

        resultado = await analytics.curva_abc(db, _filtro(conta))
        assert resultado["itens"][0]["classe"] == "A"
        assert resultado["itens"][0]["acumulado_pct"] == "100.00"

    async def test_acumulado_termina_em_cem_por_cento(self, db, conta):
        for i in range(5):
            await _venda(db, conta, sku=f"SKU-{i}", receita=str(100 * (i + 1)))

        resultado = await analytics.curva_abc(db, _filtro(conta))
        assert Decimal(resultado["itens"][-1]["acumulado_pct"]) == Decimal("100.00")

    async def test_participacoes_somam_o_total(self, db, conta):
        for i in range(6):
            await _venda(db, conta, sku=f"S-{i}", receita="150")

        resultado = await analytics.curva_abc(db, _filtro(conta))
        soma = sum(Decimal(i["receita_bruta"]) for i in resultado["itens"])
        assert soma == Decimal(resultado["total_receita"])

    async def test_resumo_das_classes_fecha_com_os_itens(self, db, conta):
        for i in range(12):
            await _venda(db, conta, sku=f"R-{i}", receita=str(1000 - i * 50))

        resultado = await analytics.curva_abc(db, _filtro(conta))
        por_classe = {r["classe"]: r for r in resultado["resumo"]}

        assert sum(r["itens"] for r in resultado["resumo"]) == resultado["total_itens"]
        soma_receita = sum(Decimal(r["receita"]) for r in resultado["resumo"])
        assert soma_receita == Decimal(resultado["total_receita"])
        assert Decimal(por_classe["A"]["receita_pct"]) <= Decimal("100")

    async def test_cancelado_fica_de_fora(self, db, conta):
        await _venda(db, conta, sku="VALIDO", receita="500")
        pedido = await _venda(db, conta, sku="CANC", receita="9000")
        pedido.status = StatusPedido.CANCELADO
        await db.commit()

        resultado = await analytics.curva_abc(db, _filtro(conta))
        assert [i["sku"] for i in resultado["itens"]] == ["VALIDO"]

    async def test_periodo_vazio_nao_divide_por_zero(self, db, conta):
        resultado = await analytics.curva_abc(db, _filtro(conta, dias=1))
        assert resultado["itens"] == []
        assert resultado["total_receita"] == "0.00"


class TestCoorte:
    async def test_agrupa_pelo_mes_da_primeira_compra(self, db, conta):
        """Quem comprou em junho é do grupo de junho, mesmo comprando em agosto."""
        await _venda(db, conta, sku="A", receita="100", comprador="cliente-1", dias_atras=70)
        await _venda(db, conta, sku="A", receita="100", comprador="cliente-1", dias_atras=5)

        resultado = await analytics.coorte_de_compradores(db, conta.tenant_id)
        assert len(resultado["coortes"]) == 1
        assert resultado["coortes"][0]["base"] == 1

    async def test_offset_zero_e_sempre_cem_por_cento(self, db, conta):
        """Todo mundo da coorte comprou no mês em que entrou nela."""
        for i in range(3):
            await _venda(db, conta, sku="X", receita="100", comprador=f"c-{i}", dias_atras=10)

        resultado = await analytics.coorte_de_compradores(db, conta.tenant_id)
        primeiro = resultado["coortes"][0]["periodos"][0]
        assert primeiro["offset"] == 0
        assert primeiro["retencao_pct"] == "100.00"
        assert primeiro["compradores"] == 3

    async def test_retencao_nunca_passa_de_cem_por_cento(self, db, conta):
        """Um comprador que volta três vezes no mês conta uma vez."""
        for dia in (70, 66, 63):
            await _venda(
                db, conta, sku="Y", receita="100", comprador="fiel",
                dias_atras=dia, externo=f"multi-{dia}",
            )

        resultado = await analytics.coorte_de_compradores(db, conta.tenant_id)
        for coorte in resultado["coortes"]:
            for periodo in coorte["periodos"]:
                assert Decimal(periodo["retencao_pct"]) <= Decimal("100")

    async def test_avisa_quando_o_canal_nao_expoe_comprador(self, db, conta):
        """Retenção sobre metade da base pareceria baixa por motivo errado."""
        await _venda(db, conta, sku="COM", receita="100", comprador="tem-hash")
        await _venda(db, conta, sku="SEM", receita="100", comprador=None)

        resultado = await analytics.coorte_de_compradores(db, conta.tenant_id)
        assert resultado["cobertura"]["pedidos_sem_comprador"] == 1
        assert "não o expõe" in resultado["cobertura"]["aviso"]

    async def test_sem_dados_devolve_estrutura_vazia(self, db, conta):
        resultado = await analytics.coorte_de_compradores(db, conta.tenant_id)
        assert resultado["coortes"] == []


class TestMediaMovel:
    async def test_primeiros_dias_saem_sem_media(self, db, conta):
        """Média de dois dias exibida como se fosse de sete inventa tendência."""
        for dia in range(10):
            await _venda(db, conta, sku="M", receita="100", dias_atras=dia)

        resultado = await analytics.serie_com_media_movel(db, _filtro(conta, 15), janela=7)
        sem_media = [p for p in resultado["pontos"] if p["media_movel_receita"] is None]
        assert len(sem_media) == 6

    async def test_media_de_valor_constante_e_o_proprio_valor(self, db, conta):
        for dia in range(14):
            await _venda(db, conta, sku="C", receita="200", dias_atras=dia)

        resultado = await analytics.serie_com_media_movel(db, _filtro(conta, 20), janela=7)
        com_media = [p for p in resultado["pontos"] if p["media_movel_receita"]]
        assert com_media[-1]["media_movel_receita"] == "200.00"

    async def test_suaviza_o_pico_de_um_dia(self, db, conta):
        """É para isso que a média móvel existe."""
        for dia in range(1, 15):
            await _venda(db, conta, sku="P", receita="100", dias_atras=dia)
        await _venda(db, conta, sku="P", receita="800", dias_atras=0, externo="pico")

        resultado = await analytics.serie_com_media_movel(db, _filtro(conta, 20), janela=7)
        ultimo = resultado["pontos"][-1]
        assert Decimal(ultimo["receita_bruta"]) == Decimal("800.00")
        assert Decimal(ultimo["media_movel_receita"]) < Decimal("300")

    async def test_tendencia_compara_medias_e_nao_dias(self, db, conta):
        """A diferença entre ontem e hoje é ruído; entre duas semanas é sinal."""
        for dia in range(14, 7, -1):
            await _venda(db, conta, sku="T", receita="100", dias_atras=dia)
        for dia in range(7, 0, -1):
            await _venda(db, conta, sku="T", receita="300", dias_atras=dia)

        resultado = await analytics.serie_com_media_movel(db, _filtro(conta, 20), janela=7)
        assert resultado["tendencia"]["direcao"] == "alta"
        assert Decimal(resultado["tendencia"]["variacao_pct"]) > ZERO

    async def test_serie_vazia_nao_quebra(self, db, conta):
        resultado = await analytics.serie_com_media_movel(db, _filtro(conta, 1))
        assert resultado["tendencia"]["direcao"] == "indefinida"


class TestEndpoints:
    async def test_as_tres_rotas_respondem(self, cliente):
        for rota in ("/api/v1/reports/abc", "/api/v1/reports/cohort",
                     "/api/v1/reports/moving-average"):
            resposta = await cliente.get(rota)
            assert resposta.status_code == 200, rota

    async def test_janela_invalida_e_recusada(self, cliente):
        resposta = await cliente.get("/api/v1/reports/moving-average", params={"janela": 1})
        assert resposta.status_code == 422
