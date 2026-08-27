"""Margem por pedido: a análise venda-a-venda.

O teste âncora reproduz, ao centavo, quatro pedidos da ferramenta que o
cliente usa como referência de mercado. Se a fórmula da margem, o rateio de
Ads ou a leitura do imposto mudarem de comportamento, é ele que quebra
primeiro.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import StatusPedido
from app.models.marketing import AdSpend, EscopoAds
from app.models.order import Order, OrderItem
from app.services import analytics, margens

D = Decimal
ZERO = D("0")
AGORA = datetime.now(UTC)


def _filtro(conta, dias: int = 365) -> analytics.Filtro:
    return analytics.Filtro(
        tenant_id=conta.tenant_id,
        inicio=AGORA - timedelta(days=dias),
        fim=AGORA + timedelta(days=1),
    )


async def pedido_completo(
    db, conta, *,
    externo: str,
    total: str,
    cmv: str = "0",
    frete: str = "0",
    comissao: str = "0",
    taxa_pagamento: str = "0",
    imposto: str = "0",
    frete_cobrado: str = "0",
    liquido: str | None = None,
    sku: str | None = "5338",
    anuncio: str = "",
    titulo: str = "Retentor",
    dias_atras: int = 5,
    status: str = StatusPedido.ENTREGUE,
    multi: bool = False,
    itens_extra: list[dict] | None = None,
) -> Order:
    """Pedido com a composição financeira completa.

    Quando ``liquido`` não é informado, usa a identidade do canal
    (bruto + frete cobrado − comissão − taxa pgto − frete pago), que é o caso
    são; informar outro valor simula divergência com o extrato.
    """
    bruto = D(total)
    if liquido is None:
        liquido = str(
            bruto + D(frete_cobrado) - D(comissao) - D(taxa_pagamento) - D(frete)
        )
    pedido = Order(
        tenant_id=conta.tenant_id,
        channel_account_id=conta.id,
        channel=conta.channel,
        external_id=externo,
        status=status,
        date_created=AGORA - timedelta(days=dias_atras),
        gross_amount=bruto,
        shipping_revenue=D(frete_cobrado),
        shipping_cost=D(frete),
        platform_fee=D(comissao),
        payment_fee=D(taxa_pagamento),
        sales_tax_amount=D(imposto),
        net_amount=D(liquido),
        cogs=D(cmv),
        has_multiple_items=multi,
    )
    db.add(pedido)
    await db.flush()
    linhas = [{"sku": sku, "total": total, "cmv": cmv, "anuncio": anuncio, "titulo": titulo}]
    linhas += itens_extra or []
    for linha in linhas:
        db.add(
            OrderItem(
                tenant_id=conta.tenant_id,
                order_id=pedido.id,
                sku_base=linha.get("sku"),
                sku_channel=linha.get("sku") or "",
                external_item_id=linha.get("anuncio", ""),
                title=linha.get("titulo", "Produto"),
                quantity=D("1"),
                gross_amount=D(linha.get("total", "0")),
                cogs=D(linha.get("cmv", "0")),
            )
        )
    await db.commit()
    return pedido


async def ads(
    db, conta, valor: str, *, escopo=EscopoAds.ANUNCIO, referencia="MLB1",
    receita_ads: str | None = None, ano: int | None = None, mes: int | None = None,
    canal: str | None = None,
):
    quando = AGORA - timedelta(days=5)
    db.add(
        AdSpend(
            tenant_id=conta.tenant_id,
            channel=canal or conta.channel,
            year=ano or quando.year,
            month=mes or quando.month,
            scope=escopo,
            reference=referencia if escopo != EscopoAds.CANAL else "",
            amount=D(valor),
            attributed_revenue=D(receita_ads) if receita_ads else None,
        )
    )
    await db.commit()


def por_externo(resultado: dict) -> dict[str, dict]:
    return {p["external_id"]: p for p in resultado["pedidos"]}


# --------------------------------------------------------------------------- #
# O teste âncora: os números da ferramenta de referência                        #
# --------------------------------------------------------------------------- #

# (externo, total, custo, frete, comissão, ads, receita_ads,
#  imposto, margem R$, margem %)  — margens conferidas à mão.
REFERENCIA = [
    ("NF1735", "33.87", "16.02", "7.95", "4.06", "8.79", None, "2.78", "-5.73", "-16.92"),
    ("NF1725", "135.87", "76.02", "19.25", "16.30", "9.35", "89.13", "11.17", "3.78", "2.78"),
    ("NF1734", "199.74", "103.50", "28.90", "23.96", "12.42", "139.86", "16.42", "14.54", "7.28"),
    ("NF1730", "161.22", "72.54", "39.90", "18.54", "1.55", None, "13.25", "15.44", "9.58"),
]


class TestReferencia:
    async def _montar(self, db, conta):
        for i, (externo, total, cmv, frete, comissao, gasto, receita_ads,
                imposto, *_ ) in enumerate(REFERENCIA):
            anuncio = f"MLB{i}"
            await pedido_completo(
                db, conta, externo=externo, total=total, cmv=cmv, frete=frete,
                comissao=comissao, imposto=imposto, sku=f"SKU{i}", anuncio=anuncio,
            )
            await ads(db, conta, gasto, referencia=anuncio, receita_ads=receita_ads)

    async def test_reproduz_a_margem_da_referencia(self, db, conta):
        await self._montar(db, conta)
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        for externo, total, cmv, frete, comissao, gasto, _r, imposto, \
                margem, margem_pct in REFERENCIA:
            linha = linhas[externo]
            assert linha["total"] == total
            assert linha["custo"] == cmv
            assert linha["frete"] == frete
            assert linha["comissao"] == comissao
            assert linha["ads"] == gasto
            assert linha["imposto"] == imposto
            assert linha["margem_valor"] == margem, f"margem R$ de {externo}"
            assert linha["margem_pct"] == margem_pct, f"margem %% de {externo}"

    async def test_tacos_sobre_a_receita_total(self, db, conta):
        await self._montar(db, conta)
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["NF1735"]["tacos_pct"] == "25.95"   # 8.79 / 33.87
        assert linhas["NF1730"]["tacos_pct"] == "0.96"

    async def test_acos_so_com_receita_atribuida(self, db, conta):
        await self._montar(db, conta)
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["NF1735"]["acos_pct"] is None       # canal não informou
        assert linhas["NF1725"]["acos_pct"] == "10.49"    # 9.35 / 89.13
        assert linhas["NF1734"]["acos_pct"] == "8.88"

    async def test_o_resumo_soma_as_colunas(self, db, conta):
        await self._montar(db, conta)
        resumo = (await margens.analisar(db, _filtro(conta)))["resumo"]
        assert resumo["pedidos"] == 4
        assert resumo["total"] == "530.70"
        assert resumo["custo"] == "268.08"
        assert resumo["ads"] == "32.11"
        assert resumo["imposto"] == "43.62"
        assert resumo["margem_valor"] == "28.03"
        assert resumo["negativos"] == 1
        assert resumo["pct_negativos"] == "25.00"


# --------------------------------------------------------------------------- #
# Rateio de publicidade                                                         #
# --------------------------------------------------------------------------- #


class TestRateio:
    async def test_proporcional_a_receita(self, db, conta):
        await pedido_completo(db, conta, externo="G", total="300", anuncio="MLB1")
        await pedido_completo(db, conta, externo="P", total="100", anuncio="MLB1")
        await ads(db, conta, "40", referencia="MLB1")
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["G"]["ads"] == "30.00"
        assert linhas["P"]["ads"] == "10.00"

    async def test_a_soma_fecha_com_o_lancamento(self, db, conta):
        """Três pedidos iguais e R$ 10: sem o resto, sobraria 1 centavo."""
        for i in range(3):
            await pedido_completo(db, conta, externo=f"P{i}", total="100", anuncio="MLB1")
        await ads(db, conta, "10", referencia="MLB1")
        resultado = await margens.analisar(db, _filtro(conta))
        assert resultado["resumo"]["ads"] == "10.00"
        assert resultado["resumo"]["ads_nao_alocado"] == "0.00"

    async def test_anuncio_vence_sku_e_canal(self, db, conta):
        await pedido_completo(db, conta, externo="A", total="100", sku="S1", anuncio="MLB1")
        await ads(db, conta, "10", escopo=EscopoAds.ANUNCIO, referencia="MLB1")
        await ads(db, conta, "99", escopo=EscopoAds.SKU, referencia="S1")
        await ads(db, conta, "999", escopo=EscopoAds.CANAL, referencia="")
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["A"]["ads"] == "10.00"

    async def test_sku_vence_canal(self, db, conta):
        await pedido_completo(db, conta, externo="A", total="100", sku="S1", anuncio="MLB9")
        await ads(db, conta, "20", escopo=EscopoAds.SKU, referencia="S1")
        await ads(db, conta, "999", escopo=EscopoAds.CANAL, referencia="")
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["A"]["ads"] == "20.00"

    async def test_canal_cobre_o_que_sobrou_sem_duplicar(self, db, conta):
        await pedido_completo(db, conta, externo="COM", total="100", sku="S1", anuncio="MLB1")
        await pedido_completo(db, conta, externo="SEM", total="100", sku="S9", anuncio="MLB9")
        await ads(db, conta, "10", escopo=EscopoAds.ANUNCIO, referencia="MLB1")
        await ads(db, conta, "50", escopo=EscopoAds.CANAL, referencia="")
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["COM"]["ads"] == "10.00"
        assert linhas["SEM"]["ads"] == "50.00"

    async def test_outra_competencia_nao_vaza(self, db, conta):
        quando = AGORA - timedelta(days=5)
        anterior = (quando.replace(day=1) - timedelta(days=1))
        await pedido_completo(db, conta, externo="A", total="100", anuncio="MLB1")
        await ads(db, conta, "10", referencia="MLB1",
                  ano=anterior.year, mes=anterior.month)
        resultado = await margens.analisar(db, _filtro(conta))
        assert por_externo(resultado)["A"]["ads"] == "0.00"
        assert resultado["resumo"]["ads_nao_alocado"] == "10.00"

    async def test_outro_canal_nao_vaza(self, db, conta):
        await pedido_completo(db, conta, externo="A", total="100", anuncio="MLB1")
        await ads(db, conta, "10", referencia="MLB1", canal="shopee")
        resultado = await margens.analisar(db, _filtro(conta))
        assert por_externo(resultado)["A"]["ads"] == "0.00"

    async def test_receita_zero_nao_divide_por_zero(self, db, conta):
        await pedido_completo(db, conta, externo="A", total="0", anuncio="MLB1",
                              liquido="0")
        await ads(db, conta, "10", referencia="MLB1")
        resultado = await margens.analisar(db, _filtro(conta))
        assert resultado["resumo"]["ads_nao_alocado"] == "10.00"


# --------------------------------------------------------------------------- #
# Recortes e alertas                                                            #
# --------------------------------------------------------------------------- #


class TestRecortes:
    async def _cenario(self, db, conta):
        await pedido_completo(db, conta, externo="NEG", total="100", cmv="90",
                              comissao="15", frete="10")
        await pedido_completo(db, conta, externo="BOM", total="200", cmv="60",
                              comissao="20", frete="10")
        await pedido_completo(db, conta, externo="SEMCUSTO", total="150", cmv="0",
                              comissao="15", frete="10")
        await pedido_completo(db, conta, externo="SEMTAXA", total="120", cmv="50",
                              comissao="0", frete="0")
        await pedido_completo(db, conta, externo="PAC", total="180", cmv="70",
                              comissao="18", frete="9", multi=True,
                              itens_extra=[{"sku": "S9", "total": "80", "cmv": "30"}])
        await pedido_completo(db, conta, externo="DIVERG", total="100", cmv="40",
                              comissao="10", frete="5", liquido="70")

    async def _ids(self, db, conta, recorte):
        return set(por_externo(await margens.analisar(db, _filtro(conta), recorte=recorte)))

    async def test_negativos(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ids(db, conta, margens.RECORTE_NEGATIVOS) == {"NEG"}

    async def test_sem_custo(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ids(db, conta, margens.RECORTE_SEM_CUSTO) == {"SEMCUSTO"}

    async def test_sem_comissao(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ids(db, conta, margens.RECORTE_SEM_COMISSAO) == {"SEMTAXA"}

    async def test_sem_frete(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ids(db, conta, margens.RECORTE_SEM_FRETE) == {"SEMTAXA"}

    async def test_pacotes(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ids(db, conta, margens.RECORTE_PACOTES) == {"PAC"}

    async def test_revisar_junta_os_alertas(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ids(db, conta, margens.RECORTE_REVISAR) == {
            "SEMCUSTO", "SEMTAXA", "DIVERG",
        }

    async def test_o_alerta_explica_o_problema(self, db, conta):
        await self._cenario(db, conta)
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["DIVERG"]["alertas"] == [margens.ALERTA_LIQUIDO_DIVERGE]
        assert linhas["DIVERG"]["diferenca_liquido"] == "15.00"
        assert margens.ALERTA_SEM_CUSTO in linhas["SEMCUSTO"]["alertas"]
        assert linhas["BOM"]["alertas"] == []

    async def test_contagem_nao_muda_com_o_recorte_aplicado(self, db, conta):
        await self._cenario(db, conta)
        tudo = await margens.analisar(db, _filtro(conta))
        so_negativos = await margens.analisar(
            db, _filtro(conta), recorte=margens.RECORTE_NEGATIVOS
        )
        assert tudo["contagem_por_recorte"] == so_negativos["contagem_por_recorte"]
        assert tudo["contagem_por_recorte"][margens.RECORTE_TODOS] == 6

    async def test_cancelado_fica_de_fora_por_padrao(self, db, conta):
        await pedido_completo(db, conta, externo="OK", total="100")
        await pedido_completo(db, conta, externo="CANC", total="100",
                              status=StatusPedido.CANCELADO)
        tudo = await margens.analisar(db, _filtro(conta))
        assert set(por_externo(tudo)) == {"OK"}
        com_cancelados = await margens.analisar(
            db, _filtro(conta), incluir_cancelados=True
        )
        assert set(por_externo(com_cancelados)) == {"OK", "CANC"}

    async def test_pedido_de_outro_tenant_nao_aparece(self, db, conta, conta_shopee):
        """O isolamento é pelo tenant do filtro, nunca pelo volume de dados."""
        await pedido_completo(db, conta, externo="MEU", total="100")
        outro = _filtro(conta)
        outro.tenant_id = conta.tenant_id + 999
        assert (await margens.analisar(db, outro))["resumo"]["pedidos"] == 0


# --------------------------------------------------------------------------- #
# Ordenação, busca e paginação                                                  #
# --------------------------------------------------------------------------- #


class TestOrdenacao:
    async def _cenario(self, db, conta):
        await pedido_completo(db, conta, externo="P1", total="100", cmv="90",
                              comissao="20", frete="5", dias_atras=3)   # −15
        await pedido_completo(db, conta, externo="P2", total="500", cmv="100",
                              comissao="50", frete="40", dias_atras=2)  # +310
        await pedido_completo(db, conta, externo="P3", total="200", cmv="150",
                              comissao="20", frete="60", dias_atras=1)  # −30

    async def _ordem(self, db, conta, ordem):
        resultado = await margens.analisar(db, _filtro(conta), ordem=ordem)
        return [p["external_id"] for p in resultado["pedidos"]]

    async def test_pior_margem_em_reais(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ordem(db, conta, margens.ORDEM_PIOR_MARGEM_VALOR) == ["P3", "P1", "P2"]

    async def test_melhor_margem_em_reais(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ordem(db, conta, margens.ORDEM_MELHOR_MARGEM_VALOR) == ["P2", "P1", "P3"]

    async def test_maior_venda(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ordem(db, conta, margens.ORDEM_MAIOR_VENDA) == ["P2", "P3", "P1"]

    async def test_maior_frete(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ordem(db, conta, margens.ORDEM_MAIOR_FRETE) == ["P3", "P2", "P1"]

    async def test_data_mais_recente_primeiro(self, db, conta):
        await self._cenario(db, conta)
        assert await self._ordem(db, conta, margens.ORDEM_DATA) == ["P3", "P2", "P1"]

    async def test_sem_receita_nao_encabeca_pior_pct(self, db, conta):
        await pedido_completo(db, conta, externo="PREJ", total="100", cmv="150")
        await pedido_completo(db, conta, externo="ZERO", total="0", liquido="0")
        ordem = await self._ordem(db, conta, margens.ORDEM_PIOR_MARGEM_PCT)
        assert ordem == ["PREJ", "ZERO"]
        linhas = por_externo(await margens.analisar(db, _filtro(conta)))
        assert linhas["ZERO"]["margem_pct"] is None


class TestBuscaEPaginacao:
    async def _cenario(self, db, conta):
        for i in range(1, 8):
            await pedido_completo(
                db, conta, externo=f"P{i:02d}", total="100", cmv="40",
                sku=f"SKU{i}", titulo=f"Retentor {i}", dias_atras=i,
            )

    async def test_pagina_recorta_a_janela(self, db, conta):
        await self._cenario(db, conta)
        resultado = await margens.analisar(db, _filtro(conta), tamanho=3, pagina=1)
        assert len(resultado["pedidos"]) == 3
        assert resultado["paginacao"] == {
            "pagina": 1, "tamanho": 3, "total": 7, "paginas": 3, "de": 1, "ate": 3,
        }

    async def test_ultima_pagina_parcial(self, db, conta):
        await self._cenario(db, conta)
        resultado = await margens.analisar(db, _filtro(conta), tamanho=3, pagina=3)
        assert len(resultado["pedidos"]) == 1
        assert resultado["paginacao"]["de"] == 7

    async def test_resumo_cobre_o_conjunto_e_nao_a_pagina(self, db, conta):
        await self._cenario(db, conta)
        resultado = await margens.analisar(db, _filtro(conta), tamanho=3)
        assert resultado["resumo"]["pedidos"] == 7
        assert resultado["resumo"]["total"] == "700.00"

    async def test_busca_por_sku(self, db, conta):
        await self._cenario(db, conta)
        resultado = await margens.analisar(db, _filtro(conta), busca="SKU7")
        assert set(por_externo(resultado)) == {"P07"}

    async def test_busca_por_titulo_ignora_caixa(self, db, conta):
        await self._cenario(db, conta)
        resultado = await margens.analisar(db, _filtro(conta), busca="retentor 3")
        assert set(por_externo(resultado)) == {"P03"}


# --------------------------------------------------------------------------- #
# Endpoints                                                                     #
# --------------------------------------------------------------------------- #


class TestEndpoints:
    async def test_margins_responde_a_tela(self, cliente, db, conta):
        await pedido_completo(db, conta, externo="A", total="100", cmv="40")
        resposta = await cliente.get("/api/v1/orders/margins")
        assert resposta.status_code == 200
        corpo = resposta.json()
        assert corpo["resumo"]["pedidos"] == 1
        assert set(corpo["contagem_por_recorte"]) == set(margens.RECORTES)

    async def test_options_lista_o_vocabulario(self, cliente):
        corpo = (await cliente.get("/api/v1/orders/margins/options")).json()
        assert corpo["recortes"] == list(margens.RECORTES)
        assert corpo["ordenacoes"] == list(margens.ORDENACOES)

    async def test_margins_sem_token_e_recusado(self, cliente):
        cliente.headers.pop("Authorization")
        assert (await cliente.get("/api/v1/orders/margins")).status_code == 401

    async def test_tamanho_absurdo_e_recusado(self, cliente):
        resposta = await cliente.get("/api/v1/orders/margins", params={"tamanho": 10_000})
        assert resposta.status_code == 422

    async def test_ciclo_de_vida_do_ad_spend(self, cliente):
        criado = await cliente.put("/api/v1/costs/ad-spend", json={
            "channel": "mercadolivre", "year": 2026, "month": 7,
            "scope": "listing", "reference": "MLB123",
            "amount": "120.50", "attributed_revenue": "1000",
        })
        assert criado.status_code == 200
        ad_id = criado.json()["id"]

        # Regravar a mesma chave atualiza em vez de duplicar.
        await cliente.put("/api/v1/costs/ad-spend", json={
            "channel": "mercadolivre", "year": 2026, "month": 7,
            "scope": "listing", "reference": "MLB123", "amount": "200",
        })
        lista = (await cliente.get(
            "/api/v1/costs/ad-spend", params={"year": 2026, "month": 7}
        )).json()
        assert len(lista) == 1
        assert lista[0]["amount"] == "200.00"

        removido = await cliente.delete(f"/api/v1/costs/ad-spend/{ad_id}")
        assert removido.status_code == 200
        assert (await cliente.delete(f"/api/v1/costs/ad-spend/{ad_id}")).status_code == 404

    async def test_escopo_invalido_e_recusado(self, cliente):
        resposta = await cliente.put("/api/v1/costs/ad-spend", json={
            "channel": "mercadolivre", "year": 2026, "month": 7,
            "scope": "galaxia", "amount": "10",
        })
        assert resposta.status_code == 422

    async def test_escopo_de_canal_descarta_referencia(self, cliente):
        """Referência em lançamento de canal criaria dois 'canal inteiro'."""
        await cliente.put("/api/v1/costs/ad-spend", json={
            "channel": "mercadolivre", "year": 2026, "month": 7,
            "scope": "channel", "reference": "LIXO", "amount": "10",
        })
        lista = (await cliente.get("/api/v1/costs/ad-spend")).json()
        assert lista[0]["reference"] == ""
