"""Aba Produtos e Estoque: anúncios, produtos internos e de-para de SKU."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.core.deps import AnalistaDep, CtxDep, DbDep
from app.core.errors import Conflito, NaoEncontrado
from app.models.catalog import Listing, Product, SkuLink, SkuPendency
from app.models.enums import StatusPedido
from app.models.order import Order, OrderItem
from app.schemas.common import Base, RespostaOperacao
from app.services import audit

router = APIRouter(prefix="/catalog", tags=["Produtos e estoque"])


class ProdutoIn(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = ""
    brand: str = ""
    category: str = ""
    unit_cost: Decimal = Decimal("0")
    packaging_cost: Decimal = Decimal("0")
    ncm: str = ""
    ean: str = ""
    weight_grams: int = 0


class ProdutoOut(Base):
    id: int
    sku: str
    name: str
    brand: str
    unit_cost: Decimal
    packaging_cost: Decimal
    is_active: bool


class MapeamentoIn(BaseModel):
    channel: str
    sku_channel: str
    product_id: int


class CustoEmLoteIn(BaseModel):
    sku: str
    unit_cost: Decimal = Field(ge=0)
    packaging_cost: Decimal | None = Field(default=None, ge=0)


@router.get("/listings", summary="Anúncios com sinalização de ruptura")
async def anuncios(
    ctx: CtxDep,
    db: DbDep,
    channel: str | None = None,
    apenas_ruptura: bool = False,
    busca: str | None = None,
    limite: int = Query(100, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    consulta = select(Listing).where(Listing.tenant_id == ctx.tenant_id)
    if channel:
        consulta = consulta.where(Listing.channel == channel)
    if apenas_ruptura:
        consulta = consulta.where(Listing.available_quantity <= 0, Listing.status == "active")
    if busca:
        termo = f"%{busca.strip()}%"
        consulta = consulta.where(
            or_(Listing.title.ilike(termo), Listing.sku_channel.ilike(termo),
                Listing.external_id.ilike(termo))
        )

    total = await db.scalar(
        select(func.count()).select_from(consulta.subquery())
    )
    resultado = await db.execute(
        consulta.order_by(Listing.sold_quantity.desc()).limit(limite).offset(offset)
    )

    return {
        "itens": [
            {
                "id": a.id,
                "external_id": a.external_id,
                "channel": a.channel,
                "title": a.title,
                "sku_channel": a.sku_channel,
                "status": a.status,
                "listing_type": a.listing_type,
                "price": str(a.price),
                "available_quantity": a.available_quantity,
                "sold_quantity": a.sold_quantity,
                "visits_30d": a.visits_30d,
                "conversao_pct": str(
                    round(Decimal(a.sold_quantity) / Decimal(a.visits_30d) * 100, 2)
                )
                if a.visits_30d
                else "—",
                "health": str(a.health) if a.health is not None else None,
                "thumbnail": a.thumbnail,
                "permalink": a.permalink,
                "em_ruptura": a.available_quantity <= 0 and a.status == "active",
            }
            for a in resultado.scalars()
        ],
        "total": int(total or 0),
        "limite": limite,
        "offset": offset,
    }


@router.get(
    "/stock-health",
    summary="Diagnóstico de estoque: ruptura, cobertura e produtos parados",
)
async def saude_estoque(ctx: CtxDep, db: DbDep, dias: int = Query(30, le=180)) -> dict[str, Any]:
    """Classifica os anúncios por situação de estoque e giro.

    A cobertura usa a venda média diária do período: um estoque de 10 unidades é
    confortável para um SKU que vende 1 por semana e crítico para um que vende 5
    por dia. O número absoluto, sozinho, não informa nada.
    """
    desde = datetime.now(UTC) - timedelta(days=dias)

    vendas = dict(
        (
            await db.execute(
                select(
                    func.coalesce(OrderItem.sku_base, OrderItem.sku_channel),
                    func.sum(OrderItem.quantity),
                )
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    OrderItem.tenant_id == ctx.tenant_id,
                    Order.date_created >= desde,
                    Order.status != StatusPedido.CANCELADO,
                )
                .group_by(func.coalesce(OrderItem.sku_base, OrderItem.sku_channel))
            )
        ).all()
    )

    anuncios = list(
        (
            await db.execute(
                select(Listing).where(
                    Listing.tenant_id == ctx.tenant_id, Listing.status == "active"
                )
            )
        ).scalars()
    )

    ruptura, criticos, parados, saudaveis = [], [], [], []
    for anuncio in anuncios:
        vendido = Decimal(str(vendas.get(anuncio.sku_channel, 0) or 0))
        media_diaria = vendido / dias if dias else Decimal("0")
        cobertura = (
            (Decimal(anuncio.available_quantity) / media_diaria) if media_diaria else None
        )

        registro = {
            "id": anuncio.id,
            "external_id": anuncio.external_id,
            "channel": anuncio.channel,
            "title": anuncio.title,
            "sku_channel": anuncio.sku_channel,
            "estoque": anuncio.available_quantity,
            "vendas_periodo": str(vendido),
            "media_diaria": str(round(media_diaria, 2)),
            "cobertura_dias": str(round(cobertura, 1)) if cobertura is not None else None,
        }

        if anuncio.available_quantity <= 0 and vendido > 0:
            ruptura.append(registro)
        elif cobertura is not None and cobertura < 7:
            criticos.append(registro)
        elif vendido == 0 and anuncio.available_quantity > 0:
            parados.append(registro)
        else:
            saudaveis.append(registro)

    return {
        "periodo_dias": dias,
        "resumo": {
            "ruptura": len(ruptura),
            "criticos": len(criticos),
            "parados": len(parados),
            "saudaveis": len(saudaveis),
        },
        "ruptura": sorted(ruptura, key=lambda r: Decimal(r["vendas_periodo"]), reverse=True),
        "criticos": sorted(criticos, key=lambda r: Decimal(r["cobertura_dias"] or "999")),
        "parados": parados[:100],
    }


@router.get("/products", response_model=list[ProdutoOut], summary="Produtos internos")
async def produtos(ctx: CtxDep, db: DbDep, busca: str | None = None) -> list[ProdutoOut]:
    consulta = select(Product).where(Product.tenant_id == ctx.tenant_id)
    if busca:
        termo = f"%{busca.strip()}%"
        consulta = consulta.where(or_(Product.sku.ilike(termo), Product.name.ilike(termo)))
    resultado = await db.execute(consulta.order_by(Product.sku).limit(500))
    return [ProdutoOut.model_validate(p) for p in resultado.scalars()]


@router.post(
    "/products",
    response_model=ProdutoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra produto interno (necessário para calcular margem)",
)
async def criar_produto(dados: ProdutoIn, ctx: AnalistaDep, db: DbDep) -> ProdutoOut:
    if await db.scalar(
        select(Product).where(Product.tenant_id == ctx.tenant_id, Product.sku == dados.sku)
    ):
        raise Conflito(f"Já existe um produto com o SKU {dados.sku!r}.")

    produto = Product(tenant_id=ctx.tenant_id, **dados.model_dump())
    db.add(produto)
    await db.flush()
    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.PRODUTO_CRIADO,
        entity_type="product",
        entity_id=produto.id,
        after={"sku": produto.sku, "unit_cost": str(produto.unit_cost)},
    )
    await db.commit()
    return ProdutoOut.model_validate(produto)


@router.patch("/products/{produto_id}", response_model=ProdutoOut, summary="Atualiza produto")
async def atualizar_produto(
    produto_id: int, dados: ProdutoIn, ctx: AnalistaDep, db: DbDep
) -> ProdutoOut:
    """Atualiza o cadastro.

    Alterar o custo aqui afeta apenas vendas **futuras**: o custo das vendas já
    registradas está congelado em ``order_items``, para que nenhum fechamento
    histórico se reescreva sozinho.
    """
    produto = await db.scalar(
        select(Product).where(Product.id == produto_id, Product.tenant_id == ctx.tenant_id)
    )
    if produto is None:
        raise NaoEncontrado("Produto não encontrado.")

    antes = {"sku": produto.sku, "unit_cost": str(produto.unit_cost)}
    for campo, valor in dados.model_dump().items():
        setattr(produto, campo, valor)

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.PRODUTO_ATUALIZADO,
        entity_type="product",
        entity_id=produto.id,
        before=antes,
        after={"sku": produto.sku, "unit_cost": str(produto.unit_cost)},
    )
    await db.commit()
    return ProdutoOut.model_validate(produto)


@router.delete(
    "/products/{produto_id}",
    response_model=RespostaOperacao,
    summary="Remove ou desativa um produto",
)
async def remover_produto(
    produto_id: int, ctx: AnalistaDep, db: DbDep, forcar: bool = False
) -> RespostaOperacao:
    """Desativa o produto; só exclui de fato se ele nunca foi vendido.

    Excluir um produto com histórico apagaria o custo congelado nos itens já
    vendidos e reescreveria a margem de meses fechados. Por isso o padrão é
    desativar: some das listas de seleção, o histórico continua íntegro.
    """
    produto = await db.scalar(
        select(Product).where(Product.id == produto_id, Product.tenant_id == ctx.tenant_id)
    )
    if produto is None:
        raise NaoEncontrado("Produto não encontrado.")

    vendas = await db.scalar(
        select(func.count(OrderItem.id)).where(OrderItem.product_id == produto_id)
    )

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="product.deleted" if not vendas else "product.deactivated",
        entity_type="product",
        entity_id=produto_id,
        before={"sku": produto.sku, "vendas": int(vendas or 0)},
    )

    if vendas:
        produto.is_active = False
        await db.commit()
        return RespostaOperacao(
            mensagem=(
                f"Produto {produto.sku} desativado. Não foi excluído porque tem "
                f"{vendas} itens vendidos — apagá-lo reescreveria a margem do histórico."
            ),
            dados={"desativado": True, "itens_vendidos": int(vendas)},
        )

    await db.delete(produto)
    await db.commit()
    return RespostaOperacao(mensagem=f"Produto {produto.sku} excluído.", dados={"excluido": True})


@router.delete(
    "/sku-links/{vinculo_id}",
    response_model=RespostaOperacao,
    summary="Desfaz um de-para de SKU",
)
async def remover_mapeamento(vinculo_id: int, ctx: AnalistaDep, db: DbDep) -> RespostaOperacao:
    """Desfaz o vínculo e devolve o SKU para a fila de pendências.

    O custo já congelado nos pedidos **não** é revertido: a venda aconteceu com
    aquele custo, e reescrevê-lo mudaria a margem de um período já apurado.
    """
    vinculo = await db.scalar(
        select(SkuLink).where(SkuLink.id == vinculo_id, SkuLink.tenant_id == ctx.tenant_id)
    )
    if vinculo is None:
        raise NaoEncontrado("Mapeamento não encontrado.")

    pendencia = await db.scalar(
        select(SkuPendency).where(
            SkuPendency.tenant_id == ctx.tenant_id,
            SkuPendency.channel == vinculo.channel,
            SkuPendency.sku_channel == vinculo.sku_channel,
        )
    )
    if pendencia:
        pendencia.resolved = False
        pendencia.last_seen_at = datetime.now(UTC)

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="sku_link.deleted",
        entity_type="sku_link",
        entity_id=vinculo_id,
        before={"channel": vinculo.channel, "sku_channel": vinculo.sku_channel},
    )
    await db.delete(vinculo)
    await db.commit()
    return RespostaOperacao(
        mensagem=(
            f"Vínculo de {vinculo.sku_channel} desfeito. O custo já registrado nas "
            f"vendas anteriores foi preservado."
        )
    )


@router.post(
    "/products/bulk-cost",
    response_model=RespostaOperacao,
    summary="Atualiza custos em lote",
)
async def atualizar_custos_em_lote(
    dados: list[CustoEmLoteIn], ctx: AnalistaDep, db: DbDep
) -> RespostaOperacao:
    """Ajusta custo e embalagem de vários produtos de uma vez.

    Afeta apenas vendas futuras — o custo das vendas já registradas continua
    congelado.
    """
    atualizados = 0
    nao_encontrados: list[str] = []

    for linha in dados:
        produto = await db.scalar(
            select(Product).where(
                Product.tenant_id == ctx.tenant_id, Product.sku == linha.sku
            )
        )
        if produto is None:
            nao_encontrados.append(linha.sku)
            continue
        produto.unit_cost = linha.unit_cost
        if linha.packaging_cost is not None:
            produto.packaging_cost = linha.packaging_cost
        atualizados += 1

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="product.bulk_cost",
        after={"atualizados": atualizados, "nao_encontrados": nao_encontrados},
    )
    await db.commit()
    return RespostaOperacao(
        mensagem=f"{atualizados} produtos atualizados.",
        dados={"atualizados": atualizados, "nao_encontrados": nao_encontrados},
    )


@router.get(
    "/sku-pendencies",
    summary="SKUs sem de-para",
    description=(
        "SKUs vistos na ingestão sem produto correspondente. A pendência nunca "
        "bloqueia o pedido — só deixa a margem daquele item indisponível, o que "
        "é sinalizado em vez de virar um custo zero silencioso."
    ),
)
async def pendencias(ctx: CtxDep, db: DbDep) -> list[dict[str, Any]]:
    resultado = await db.execute(
        select(SkuPendency)
        .where(SkuPendency.tenant_id == ctx.tenant_id, SkuPendency.resolved.is_(False))
        .order_by(SkuPendency.occurrences.desc())
    )
    return [
        {
            "id": p.id,
            "channel": p.channel,
            "sku_channel": p.sku_channel,
            "sample_title": p.sample_title,
            "occurrences": p.occurrences,
            "last_seen_at": p.last_seen_at,
        }
        for p in resultado.scalars()
    ]


@router.post("/sku-links", response_model=RespostaOperacao, summary="Cria o de-para de SKU")
async def mapear_sku(dados: MapeamentoIn, ctx: AnalistaDep, db: DbDep) -> RespostaOperacao:
    """Vincula um código de canal a um produto interno.

    Retroalimenta os pedidos já importados: o custo dos itens daquele SKU que
    entraram sem mapeamento é preenchido, e a margem passa a existir para o
    histórico também.
    """
    produto = await db.scalar(
        select(Product).where(
            Product.id == dados.product_id, Product.tenant_id == ctx.tenant_id
        )
    )
    if produto is None:
        raise NaoEncontrado("Produto não encontrado.")

    existente = await db.scalar(
        select(SkuLink).where(
            SkuLink.tenant_id == ctx.tenant_id,
            SkuLink.channel == dados.channel,
            SkuLink.sku_channel == dados.sku_channel,
        )
    )
    if existente:
        existente.product_id = produto.id
    else:
        db.add(
            SkuLink(
                tenant_id=ctx.tenant_id,
                channel=dados.channel,
                sku_channel=dados.sku_channel,
                product_id=produto.id,
                created_by=ctx.user_id,
            )
        )

    pendencia = await db.scalar(
        select(SkuPendency).where(
            SkuPendency.tenant_id == ctx.tenant_id,
            SkuPendency.channel == dados.channel,
            SkuPendency.sku_channel == dados.sku_channel,
        )
    )
    if pendencia:
        pendencia.resolved = True

    itens = list(
        (
            await db.execute(
                select(OrderItem).where(
                    OrderItem.tenant_id == ctx.tenant_id,
                    OrderItem.sku_channel == dados.sku_channel,
                    OrderItem.product_id.is_(None),
                )
            )
        ).scalars()
    )
    for item in itens:
        item.product_id = produto.id
        item.sku_base = produto.sku
        item.unit_cost = produto.unit_cost
        item.cogs = (produto.unit_cost * item.quantity).quantize(Decimal("0.0001"))

    pedidos_afetados = {i.order_id for i in itens}
    for pedido_id in pedidos_afetados:
        pedido = await db.get(Order, pedido_id)
        if pedido:
            total = await db.scalar(
                select(func.coalesce(func.sum(OrderItem.cogs), 0)).where(
                    OrderItem.order_id == pedido_id
                )
            )
            pedido.cogs = Decimal(str(total or 0))

    await audit.registrar(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action=audit.Acao.SKU_MAPEADO,
        entity_type="sku_link",
        entity_id=dados.sku_channel,
        after={"product_id": produto.id, "channel": dados.channel},
    )
    await db.commit()

    return RespostaOperacao(
        mensagem=f"SKU {dados.sku_channel} vinculado a {produto.sku}.",
        dados={"itens_atualizados": len(itens), "pedidos_recalculados": len(pedidos_afetados)},
    )


@router.get("/sku-links", summary="De-para configurados")
async def listar_mapeamentos(ctx: CtxDep, db: DbDep) -> list[dict[str, Any]]:
    resultado = await db.execute(
        select(SkuLink, Product)
        .join(Product, Product.id == SkuLink.product_id)
        .where(SkuLink.tenant_id == ctx.tenant_id)
        .order_by(SkuLink.channel, SkuLink.sku_channel)
    )
    return [
        {
            "id": vinculo.id,
            "channel": vinculo.channel,
            "sku_channel": vinculo.sku_channel,
            "product_id": produto.id,
            "product_sku": produto.sku,
            "product_name": produto.name,
            "confidence": vinculo.confidence,
        }
        for vinculo, produto in resultado.all()
    ]
