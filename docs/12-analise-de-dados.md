# 12 — Análise de Dados: Origem, Cálculo e Uso

Responde às perguntas de classificação pedidas no escopo: o que a API entrega
pronto, o que precisa ser calculado, o que é público, o que exige autorização, e
para que cada dado serve.

## 12.1 Nativo da API × calculado internamente

### Entregue pronto pelas APIs

| Dado | ML | MP | Shopee |
|---|---|---|---|
| Pedido, status, itens, quantidade, preço | ✅ | — | ✅ |
| Comissão do marketplace | ✅ `sale_fee` | — | ✅ `commission_fee` |
| Taxa de pagamento | — | ✅ `fee_details[]` | ✅ `transaction_fee` |
| **Valor líquido** | — | ✅ `net_received_amount` | ✅ `escrow_amount` (pós-conclusão) |
| Custo real de frete | ✅ `/shipments/costs` | — | ✅ `actual_shipping_fee` |
| Data de liberação do dinheiro | — | ✅ `money_release_date` | ✅ `escrow_release_time` |
| Estoque e preço do anúncio | ✅ | — | ✅ |
| Rastreio e eventos logísticos | ✅ | — | ✅ |
| Perguntas, mensagens, reclamações | ✅ | — | ✅ (chat, returns) |
| Reputação / saúde da conta | ✅ | — | ✅ |
| Visitas ao anúncio | ✅ | — | ✅ `get_item_extra_info` |
| Campanhas e cupons | ✅ | — | ✅ |

### Calculado pelo sistema (não existe em API nenhuma)

| Dado | Como |
|---|---|
| **Visão consolidada multicanal** | União do modelo canônico — a razão de existir do produto |
| Receita bruta consolidada | `Σ itens`, deduplicando pacotes por `pack_id` |
| Líquido estimado (Shopee pré-escrow) | Bruto − taxas conhecidas, marcado `computed` |
| Ticket médio, taxa efetiva de taxas | Razões sobre agregados |
| **CMV e margem de contribuição** | Exige custo interno — nenhum marketplace conhece o custo do seller |
| Cobertura e giro de estoque | Estoque ÷ média diária de venda |
| Ruptura e dias em ruptura | Série `inventory_snapshots` |
| Atraso de entrega | `date_delivered − estimated_delivery` |
| Conversão operacional | Pedidos ÷ visitas |
| Curva ABC, produtos parados | Classificação sobre a série histórica |
| **Divergência de conciliação** | Esperado × liberado |
| Fluxo de caixa projetado | Datas de liberação + prazo médio histórico |
| Evolução da reputação | Snapshots diários (a API só dá o estado atual) |
| Rentabilidade por campanha | Receita da campanha − desconto − taxas − mídia |
| Análise de recompra | `buyer_hash` ao longo do tempo |
| Sazonalidade hora × dia | Distribuição de `date_created` |

## 12.2 Público × dependente de autorização

**Público (sem token de seller):** categorias e atributos, busca de produtos,
preços de anúncios concorrentes, tabela de comissões (`listing_prices`), mais
vendidos por categoria, cotação de frete, moedas e sites.

**Exige autorização do seller (OAuth/HMAC):** absolutamente todo o resto — pedidos,
pagamentos, taxas, líquido, envios, estoque, perguntas, mensagens, reclamações,
campanhas, repasses, faturamento.

**Exige autorização adicional da plataforma:** Shopee Ads API (whitelist), Shopee
Livestream (regional), MP modo marketplace (homologação), escopo `write` do ML
(revisão da aplicação).

## 12.3 Dados por finalidade

### Faturamento bruto
`order_items.unit_price`, `quantity`, `orders.status` (para excluir cancelados),
`external_pack_id` (deduplicação), `date_created` (competência), `currency`.

### Faturamento líquido
Tudo acima **mais**: `sale_fee`/`commission_fee`/`service_fee`,
`fee_details[]`/`transaction_fee`, `shipments.costs`/`actual_shipping_fee`,
`shipping_revenue`, `taxes_amount`, `discount_amount`, `refunds`, `chargebacks`, e
as fontes primárias `net_received_amount`/`escrow_amount`.

### Cálculo de taxas
`sale_fee` por item · `fee_details[].type/amount` · `commission_fee` +
`service_fee` + `transaction_fee` · `financing_fee` (parcelamento) ·
`charges_details` (ajustes) · Billing API do ML (número oficial) ·
`listing_prices` (taxa esperada por tipo de anúncio, para detectar cobrança fora do
padrão).

### Margem
Líquido + `products.unit_cost` (interno, congelado em `order_items.unit_cost`) +
embalagem + mídia rateada. **Sem cadastro de custo não existe margem** — e assumir
custo zero produz um número bonito e falso.

### Decisão operacional
Estoque e cobertura · ruptura · pedidos pendentes de envio · envios atrasados ·
perguntas sem resposta · reclamações abertas · anúncios pausados com venda
recente · SKUs parados com estoque · saúde da conta.

### Controle financeiro e contábil
`settlements` + `settlement_entries` (o que caiu na conta e de onde veio) ·
`money_release_date`/`escrow_release_time` (competência de caixa) ·
`reconciliations` (divergências) · Billing/escrow (documento oficial) ·
`refunds`/`chargebacks` · `raw JSONB` (prova documental) · `audit_logs` (trilha).

## 12.4 Cortes de visão suportados

Todos implementados como filtros combináveis sobre o mesmo conjunto de queries:

| Visão | Chave de agregação |
|---|---|
| Consolidada por seller | `tenant_id` |
| Por loja/conta | `channel_account_id` |
| Por marketplace | `channel` |
| Por período | `date_created` com granularidade hora/dia/semana/mês |
| Por produto | `product_id` |
| Por SKU | `sku_base` (une o mesmo produto em múltiplos anúncios e canais) |
| Por campanha | `campaign_items` → `order_items` |
| Por canal logístico | `logistic_type` |
| Por geografia | `ship_state`, `ship_city` |
| Por tipo de anúncio | `listing_type` (Premium × Clássico) |

O corte por **SKU base** merece destaque: é o que responde "quanto o produto 5338
faturou no total?" quando ele está em 4 anúncios do ML e 2 da Shopee, com códigos
diferentes em cada canal (`8126`, `8126STD`, `8126a`, `8126STA`). Nenhum painel
nativo consegue fazer isso, porque nenhum deles enxerga os outros canais.

## 12.5 Qualidade de dado — como o sistema se protege

| Problema | Detecção | Tratamento |
|---|---|---|
| SKU sem mapeamento | Ingestão não acha o `sku_link` | Grava em `sku_pendencies`, **não bloqueia** o pedido |
| Produto sem custo | `unit_cost IS NULL` | Margem exibida como "indisponível", nunca como 100% |
| Pedido sem pagamento | Conciliação nível 1 | `unmatched`, entra na fila de exceções |
| Escrow atrasado | `computed` há mais de 20 dias | Alerta de possível retenção |
| Payload com campo novo | Validação do normalizador | Log de aviso + campo preservado no `raw` |
| Valor negativo inesperado | Verificação de sanidade | Bloqueia o rollup e alerta |
| Duplicata | `UNIQUE (channel_account_id, external_id)` | UPSERT idempotente |
