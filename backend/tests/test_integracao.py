"""Teste de integração do fluxo completo, com os conectores simulados.

Cobre o caminho que o produto realmente percorre: sincronizar → normalizar →
persistir → agregar → conciliar. É o teste que pega regressões que nenhum teste
unitário pega, porque elas só aparecem na costura entre as camadas.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.enums import FonteLiquido, StatusPedido
from app.models.finance import Payment
from app.models.order import Order, OrderItem
from app.services import analytics, reconciliation, sync

pytestmark = pytest.mark.asyncio


async def test_sincronizacao_completa_produz_numeros_coerentes(db, conta):
    resultado = await sync.sincronizar_pedidos(db, conta)
    assert resultado.erros == []
    assert resultado.criados > 0

    total = await db.scalar(select(func.count(Order.id)))
    assert total == resultado.criados

    inicio, fim = analytics.periodo_padrao(120)
    filtro = analytics.Filtro(tenant_id=conta.tenant_id, inicio=inicio, fim=fim)
    visao = await analytics.visao_geral(db, filtro)

    bruto = Decimal(visao["kpis"]["receita_bruta"]["valor"])
    liquido = Decimal(visao["kpis"]["receita_liquida"]["valor"])

    assert bruto > 0
    # A propriedade que mais importa: o líquido nunca supera o bruto. Quando
    # isso quebra, é sinal de pagamento contado em duplicidade ou associado ao
    # pedido errado.
    assert liquido <= bruto
    assert Decimal(visao["derivados"]["margem_pct"]) <= 100


async def test_reprocessar_a_mesma_janela_nao_duplica_pedidos(db, conta):
    """Webhook, polling e reconciliação diária cobrem o mesmo dado de propósito.

    A redundância só é barata porque a ingestão é idempotente.
    """
    primeira = await sync.sincronizar_pedidos(db, conta)
    total_apos_primeira = await db.scalar(select(func.count(Order.id)))

    cursor = await sync.obter_cursor(db, conta.id, sync.Recurso.PEDIDOS)
    cursor.last_synced_at = None  # força a mesma janela de novo
    await db.commit()

    segunda = await sync.sincronizar_pedidos(db, conta)
    total_apos_segunda = await db.scalar(select(func.count(Order.id)))

    assert total_apos_segunda == total_apos_primeira
    assert segunda.criados == 0
    assert segunda.atualizados >= primeira.criados


async def test_pagamentos_ficam_vinculados_ao_pedido(db, conta):
    """Se o pagamento não se vincula, todo pedido vira 'sem correspondência'."""
    await sync.sincronizar_pedidos(db, conta)

    pagamentos = await db.scalar(select(func.count(Payment.id)))
    orfaos = await db.scalar(
        select(func.count(Payment.id)).where(Payment.order_id.is_(None))
    )

    assert pagamentos > 0
    assert orfaos == 0


async def test_conciliacao_classifica_todos_os_pedidos(db, conta):
    await sync.sincronizar_pedidos(db, conta)
    resultado = await reconciliation.conciliar_periodo(db, conta.tenant_id, dias=120)

    assert resultado.analisados > 0
    # Todo pedido analisado precisa cair em exatamente uma classificação.
    assert (
        resultado.conciliados
        + resultado.divergentes
        + resultado.aguardando
        + resultado.sem_correspondencia
        == resultado.analisados
    )


async def test_shopee_entra_com_liquido_estimado_e_declarado(db, conta_shopee):
    """Antes do escrow a Shopee não informa líquido — a estimativa precisa ser
    marcada como tal, para o painel não apresentá-la como valor confirmado."""
    await sync.sincronizar_pedidos(db, conta_shopee)

    pedidos = list((await db.execute(select(Order))).scalars())
    assert pedidos

    fontes = {p.net_source for p in pedidos}
    assert fontes <= {
        FonteLiquido.CALCULADO,
        FonteLiquido.REPORTADO_API,
        FonteLiquido.LIQUIDADO,
    }
    for p in pedidos:
        if p.status != StatusPedido.CANCELADO:
            assert p.net_amount <= p.gross_amount + p.shipping_revenue


async def test_sku_sem_mapeamento_nao_bloqueia_a_importacao(db, conta):
    """Regra explícita do produto: o dinheiro entra mesmo sem o de-para.

    O que fica indisponível é apenas a margem daquele item — e isso é
    sinalizado, em vez de virar um custo zero silencioso (que exibiria 100% de
    margem).
    """
    from app.models.catalog import SkuPendency

    await sync.sincronizar_pedidos(db, conta)

    pedidos = await db.scalar(select(func.count(Order.id)))
    pendencias = await db.scalar(select(func.count(SkuPendency.id)))
    itens_sem_custo = await db.scalar(
        select(func.count(OrderItem.id)).where(OrderItem.unit_cost == 0)
    )

    assert pedidos > 0          # importação seguiu normalmente
    assert pendencias > 0       # e as pendências foram registradas
    assert itens_sem_custo > 0  # com custo zerado e sinalizável na interface


async def test_rollup_reproduz_o_total_da_consulta_direta(db, conta):
    """O painel lê rollup em janelas longas e a tabela em janelas curtas.

    Se os dois discordarem, o mesmo período mostra números diferentes conforme
    o filtro escolhido — e ninguém confia mais no painel.
    """
    from app.models.metrics import MetricDaily

    await sync.sincronizar_pedidos(db, conta)
    await analytics.recalcular_rollups(db, conta.tenant_id, horas=24 * 120)

    direto = await db.scalar(
        select(func.coalesce(func.sum(Order.gross_amount), 0)).where(
            Order.tenant_id == conta.tenant_id, Order.status != StatusPedido.CANCELADO
        )
    )
    do_rollup = await db.scalar(
        select(func.coalesce(func.sum(MetricDaily.gross_amount), 0)).where(
            MetricDaily.tenant_id == conta.tenant_id
        )
    )

    assert abs(Decimal(str(direto)) - Decimal(str(do_rollup))) < Decimal("0.05")
