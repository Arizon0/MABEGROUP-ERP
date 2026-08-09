# 04 — Levantamento das APIs Oficiais

Inventário dos módulos oficiais consumidos, por marketplace e por tipo de dado.
Cada módulo traz: o que permite, o que retorna, limitações/permissões, utilidade
analítica, se alimenta o painel ao vivo e se entra na conciliação financeira.

Legenda das colunas de uso:
**AN** = análise · **RT** = tempo real · **FIN** = conciliação financeira

---

## PARTE A — MERCADO LIVRE (`https://api.mercadolibre.com`)

### A.1 Identidade e conta

| Endpoint | O que faz / retorna | Uso |
|---|---|---|
| `GET /users/me` | Conta autenticada: `id`, `nickname`, `site_id`, `email`, `user_type`, `tags`. Usado no fim do OAuth para carimbar a conta. | — |
| `GET /users/{id}` | Perfil público + **`seller_reputation`**: `level_id` (verde/amarelo…), `power_seller_status` (Mercado Líder), `transactions.ratings` (positive/neutral/negative), `metrics.claims/delayed_handling_time/cancellations` com taxa e período. | AN |
| `GET /users/{id}/brands` | Marcas oficiais associadas. | AN |

**Limitação:** e-mail e dados pessoais do comprador são restritos por LGPD. A
reputação é *snapshot* — o ML não expõe histórico, por isso gravamos em
`metrics_snapshots` diariamente. Sem essa fotografia diária, não existe gráfico de
evolução de reputação.

### A.2 Catálogo e anúncios

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /users/{id}/items/search` | IDs dos anúncios do seller. Paginação por `scroll_id` (obrigatória acima de 1.000 itens). Filtros por `status`, `listing_type`. | AN |
| `GET /items?ids={id1,id2}` | **Multiget, até 20 por chamada.** Reduz 20× o consumo de rate limit — é o método padrão do conector. | AN |
| `GET /items/{id}` | `title`, `price`, `base_price`, `available_quantity`, `sold_quantity`, `status`, `listing_type_id`, `category_id`, `permalink`, `attributes[]`, `variations[]`, `shipping.logistic_type`, `health`. | AN, RT |
| `GET /items/{id}/description` | Descrição em texto/HTML. | AN |
| `PUT /items/{id}` | Atualiza preço, estoque, status. Requer escopo `write`. | — |
| `GET /categories/{id}` | Nome, caminho da raiz, `settings`. | AN |
| `GET /categories/{id}/attributes` | Atributos obrigatórios/opcionais — usado para diagnosticar ficha técnica incompleta. | AN |
| `GET /sites/MLB/listing_prices?price=X` | **Taxa de venda por tipo de anúncio, antes de vender.** Base do simulador de margem. | AN, FIN |
| `GET /items/{id}/visits/time_window` | Visitas por janela (`last=30&unit=day`). | AN |
| `GET /users/{id}/items_visits` | Visitas agregadas do seller. Permite **taxa de conversão real** = pedidos ÷ visitas. | AN |
| `GET /highlights/MLB/category/{cat}` | Mais vendidos da categoria — inteligência competitiva. | AN |

### A.3 Estoque Full (Fulfillment)

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /inventories/{inventory_id}/stock/fulfillment` | Estoque no CD do ML: `available_quantity`, `not_available_quantity` com detalhamento (danificado, em transferência, retido). | AN, RT |
| `GET /user-products/{user_product_id}/stock` | Estoque por produto de usuário. | AN |
| `GET /inventories/{id}/operations/search` | Movimentações no Full — entradas, saídas, ajustes. | AN, FIN |

Relevante porque **93% dos envios da operação atual são Full**: a ruptura no CD do
ML é o principal risco operacional, e ela não aparece no estoque próprio.

### A.4 Pedidos

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /orders/search?seller={id}&order.date_created.from=&order.date_created.to=` | Lista paginada. Filtros por `order.status`, `sort=date_desc`. | AN, RT, FIN |
| `GET /orders/search/recent?seller={id}` | Atalho para os mais recentes — usado no polling de segurança de 5 min. | RT |
| `GET /orders/{id}` | `status`, `status_detail`, `date_created/closed/last_updated`, `order_items[]` (item, `unit_price`, `quantity`, `sale_fee`), `payments[]`, `shipping.id`, `buyer` (parcial), `taxes`, `pack_id`, `total_amount`, `paid_amount`. | AN, RT, FIN |
| `GET /orders/{id}/feedback` | Avaliação trocada entre comprador e vendedor. | AN |
| `GET /packs/{pack_id}` | Carrinho com múltiplos pedidos — evita contar receita em duplicidade. | FIN |

> **Limitações críticas de paginação.** `offset` máximo de 1.000 e janela útil de
> ~60 dias por consulta. Backfill histórico **exige** varrer em janelas de 30 dias
> caminhando para trás. Um loop ingênuo de `offset` para silenciosamente em 1.000
> pedidos e o seller nunca descobre que faltam dados. O conector implementa janela
> deslizante por data.

**Campo `sale_fee` em `order_items`:** é a comissão do ML naquele item. É a fonte
mais confiável de taxa por pedido — melhor que estimar por `listing_prices`.

### A.5 Envios

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /shipments/{id}` | `status`, `substatus`, `tracking_number`, `tracking_method`, `logistic_type`, `date_first_printed`, `estimated_delivery_time`, `receiver_address` (cidade/estado/CEP). | AN, RT |
| `GET /shipments/{id}/costs` | **`gross_amount`, `receiver_cost` (quanto o comprador pagou), `senders[].cost` (quanto o seller pagou), descontos aplicados.** Esta é a fonte real do custo de frete. | FIN |
| `GET /shipments/{id}/items` | Itens do envio — necessário para ratear frete entre SKUs. | FIN |
| `GET /shipments/{id}/history` | Timeline completa de status. | AN, RT |
| `GET /shipments/{id}/lead_time` | Prazo prometido vs. real → **cálculo de atraso**. | AN |
| `GET /shipment_labels?shipment_ids=&savePdf=Y` | Etiquetas em PDF/ZPL. | — |
| `GET /sites/MLB/shipping_options?...` | Cotação de frete. | AN |

**Limitação:** o endereço completo do comprador só é liberado para envios do tipo
Flex/self-service. Em Full e Correios vem parcial (cidade/estado). Suficiente para
análise geográfica, insuficiente para logística própria.

### A.6 Financeiro e faturamento

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /billing/integration/monthly/periods?group=MP&document_type=BILL` | Períodos de faturamento disponíveis. | FIN |
| `GET /billing/integration/periods/key/{key}/group/MP/details` | **Detalhe linha a linha de todas as cobranças do período**: comissão, frete, publicidade, taxa de parcelamento, ajustes. | FIN |
| `GET /billing/integration/periods/key/{key}/group/MP/summary` | Resumo do período. | FIN, AN |
| `GET /users/{id}/mercadopago_account/bill_data` | Dados fiscais do faturamento. | FIN |

Este é o módulo que fecha a conta com **exatidão contábil**. As taxas por pedido
(`sale_fee`) dão a visão gerencial em tempo real; o Billing dá o número oficial que
bate com a nota fiscal do Mercado Livre. O sistema usa os dois e reconcilia.

### A.7 Perguntas, mensagens, reclamações e avaliações

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /questions/search?seller_id={id}&status=UNANSWERED` | Perguntas com `text`, `date_created`, `item_id`, `from.id`. | AN, RT |
| `POST /answers` | Responde pergunta. | — |
| `GET /messages/packs/{pack_id}/sellers/{seller_id}` | Mensagens pós-venda do pacote. | AN, RT |
| `POST /messages/packs/{pack_id}/sellers/{seller_id}` | Envia mensagem. Sujeito a moderação e janela temporal. | — |
| `GET /post-purchase/v1/claims/search` | Reclamações: `type`, `stage`, `status`, `reason_id`, `resource_id`. | AN, FIN |
| `GET /post-purchase/v1/claims/{id}/actions` | Ações disponíveis (aceitar devolução, contestar). | — |
| `GET /post-purchase/v1/claims/{id}/messages` | Conversa da mediação. | AN |
| `GET /post-purchase/v1/claims/{id}/returns` | Devolução vinculada: status, rastreio, valor. | FIN |
| `GET /reviews/item/{item_id}` | Avaliações do produto: nota, comentário, distribuição. | AN |

**Limitação:** a API de mensagens só funciona **pós-venda** (exige `pack_id`); não
existe acesso a chat pré-venda. Mensagens têm janela de resposta e moderação
automática — anexar link externo pode bloquear o envio.

### A.8 Notificações (webhooks)

Configuradas no DevCenter. Tópicos relevantes:

| Tópico | Dispara quando | Uso |
|---|---|---|
| `orders_v2` | Pedido criado ou alterado | RT, FIN |
| `shipments` | Mudança de status de envio | RT |
| `payments` | Mudança de status de pagamento | RT, FIN |
| `items` | Anúncio alterado (preço, estoque, status) | RT |
| `questions` | Nova pergunta | RT |
| `messages` | Nova mensagem pós-venda | RT |
| `post_purchase` / `claims` | Reclamação/mediação | RT |
| `invoices` | Nota fiscal emitida | FIN |
| `stock-locations` | Estoque Full alterado | RT |
| `flex-handshakes` | Aceite de envio Flex | RT |

O payload é **magro de propósito** — só `{topic, resource, user_id, attempts,
sent}`. É um "vá buscar", não um "aqui está". O sistema sempre faz a chamada de
detalhe depois.

> **Regra dura do ML:** responder `200` em **até 500 ms**. Acima disso conta como
> falha, e falhas recorrentes levam à suspensão do envio de notificações para a
> aplicação. Por isso o endpoint só persiste e enfileira.

---

## PARTE B — MERCADO PAGO (`https://api.mercadopago.com`)

### B.1 Pagamentos

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /v1/payments/{id}` | O registro financeiro mais completo do ecossistema (detalhe abaixo). | FIN, RT |
| `GET /v1/payments/search?sort=date_created&criteria=desc&range=date_created&begin_date=&end_date=` | Busca paginada. | FIN, AN |
| `GET /v1/payments/{id}/refunds` | Reembolsos totais/parciais. | FIN |
| `POST /v1/payments/{id}/refunds` | Reembolsa. | — |
| `GET /v1/chargebacks/{id}` | Chargeback: valor, motivo, prazo de contestação. | FIN |
| `GET /merchant_orders/{id}` | Agrupa pagamentos e envios de uma mesma ordem. | FIN |

**Campos decisivos de `/v1/payments/{id}`:**

```jsonc
{
  "status": "approved",
  "status_detail": "accredited",
  "transaction_amount": 129.90,        // valor da venda
  "taxes_amount": 0,
  "shipping_amount": 21.90,
  "fee_details": [                     // ← granularidade de taxa por tipo
    { "type": "mercadopago_fee", "amount": 14.02, "fee_payer": "collector" },
    { "type": "financing_fee",   "amount":  3.10, "fee_payer": "collector" }
  ],
  "transaction_details": {
    "net_received_amount": 112.78,     // ← LÍQUIDO OFICIAL, não recalcular
    "total_paid_amount": 129.90,
    "installment_amount": 43.30
  },
  "charges_details": [ ... ],          // ajustes, descontos, penalidades
  "money_release_date": "2026-08-23T00:00:00Z",   // ← quando cai na conta
  "money_release_status": "released",
  "date_approved": "2026-08-09T14:22:31Z"
}
```

`net_received_amount` é a **fonte primária de faturamento líquido** quando o
pagamento passa pelo MP. `money_release_date` é o que permite projeção de fluxo de
caixa — saber que R$ 112,78 caem no dia 23 vale mais operacionalmente do que saber
que a venda ocorreu.

### B.2 Relatórios e conciliação bancária

| Endpoint | Retorna | Uso |
|---|---|---|
| `POST /v1/account/release_report` | **Gera relatório "Liberações"** — o dinheiro efetivamente liberado, com data. | FIN |
| `GET /v1/account/release_report/list` | Lista relatórios gerados. | FIN |
| `GET /v1/account/release_report/{file_name}` | Baixa o CSV. | FIN |
| `POST/GET /v1/account/settlement_report/*` | Relatório de "Valores a liberar" (agenda futura). | FIN |
| `GET /v1/account/bank_report/*` | Conciliação com a conta bancária. | FIN |
| `GET /v1/account/balance` | Saldo disponível e a liberar. | FIN, RT |

> **Limitação importante:** esses relatórios são **assíncronos**. O fluxo é
> `POST` para solicitar → *polling* até `status=completed` → `GET` do arquivo. Não
> existe versão síncrona. O sistema trata isso como job de duas fases com estado
> persistido, não como chamada bloqueante.

**Este é o único endpoint que responde "quanto realmente caiu na conta".** Sem ele,
o "valor líquido" do dashboard é uma previsão, não um fato. Com ele, o campo
`net_source` do pedido vira `settled`.

### B.3 Webhooks do MP

Tópicos: `payment`, `merchant_order`, `chargebacks`, `point_integration_wh`.
Assinatura `x-signature` obrigatória (ver doc 03).

---

## PARTE C — SHOPEE OPEN PLATFORM (`https://partner.shopeemobile.com`)

### C.1 Loja e conta

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/shop/get_shop_info` | `shop_name`, `region`, `status`, `is_cb` (cross-border), `shop_fulfillment_flag`. | — |
| `GET /api/v2/shop/get_profile` | Logo, descrição. | — |
| `GET /api/v2/public/get_shops_by_partner` | Todas as lojas autorizadas ao app — usado para detectar loja nova/revogada. | — |
| `GET /api/v2/account_health/get_shop_performance` | **Métricas de saúde**: taxa de cancelamento, atraso de envio, pontos de penalidade. | AN |

### C.2 Produtos

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/product/get_item_list` | IDs paginados por `offset`/`page_size` (máx. 100), filtro por `item_status` e `update_time_from/to`. | AN |
| `GET /api/v2/product/get_item_base_info` | **Até 50 itens por chamada**: nome, preço, estoque, status, categoria, imagens. | AN, RT |
| `GET /api/v2/product/get_model_list` | Variações (models): `model_id`, `model_sku`, `price_info`, `stock_info`. | AN, RT |
| `GET /api/v2/product/get_item_extra_info` | `sales`, `views`, `likes`, `rating_star` → conversão por anúncio. | AN |
| `POST /api/v2/product/update_price` · `update_stock` | Atualização em massa. | — |

### C.3 Pedidos

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/order/get_order_list` | Lista por `time_range_field` (`create_time` ou `update_time`). **Janela máxima de 15 dias por chamada.** Paginação por `cursor`. | AN, RT |
| `GET /api/v2/order/get_order_detail` | **Até 50 pedidos por chamada.** `order_status`, `item_list[]`, `total_amount`, `payment_method`, `recipient_address` (cidade/estado), `note`, `cancel_reason`. | AN, RT, FIN |
| `GET /api/v2/order/get_shipment_list` | Pedidos já despachados. | AN |
| `GET /api/v2/returns/get_return_list` · `get_return_detail` | Devoluções: motivo, status, valor reembolsado. | AN, FIN |

> **Limitação estrutural:** janela de 15 dias. Um backfill de 2 anos = ~49
> chamadas sequenciais **por loja**, respeitando rate limit. O conector implementa
> isso como job em lote com retomada por cursor persistido — se cair no meio,
> recomeça de onde parou, não do zero.

`optional_fields` em `get_order_detail` precisa ser pedido explicitamente
(`item_list`, `recipient_address`, `buyer_username`…). Esquecer disso retorna um
payload mutilado que parece completo.

### C.4 Financeiro — **o módulo mais importante da Shopee**

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/payment/get_escrow_detail` | **A fonte da verdade financeira da Shopee.** | FIN |
| `GET /api/v2/payment/get_escrow_list` | Lista de escrows por período. | FIN |
| `GET /api/v2/payment/get_escrow_detail_batch` | Até 50 pedidos por chamada. | FIN |
| `GET /api/v2/payment/get_payout_detail` | Repasses agregados (o que caiu na conta). | FIN |
| `GET /api/v2/payment/get_wallet_transaction_list` | Extrato da carteira Shopee. | FIN |

Campos de `get_escrow_detail` → `order_income`:

```jsonc
{
  "escrow_amount": 46.13,             // ← LÍQUIDO que o seller recebe
  "original_price": 69.90,
  "seller_discount": 5.00,
  "shopee_discount": 3.00,
  "commission_fee": 6.99,             // comissão Shopee
  "service_fee": 4.19,                // taxa de serviço (Frete Grátis etc.)
  "transaction_fee": 1.40,            // taxa de transação
  "buyer_paid_shipping_fee": 0,
  "actual_shipping_fee": 18.90,
  "reverse_shipping_fee": 0,          // frete de devolução
  "escrow_release_time": 1723334400
}
```

> **Diferença fundamental entre os canais:** o Mercado Livre entrega o líquido
> pronto (`net_received_amount` no MP). A **Shopee não** — o `escrow_amount` só
> fica disponível **após** o pedido ser concluído, e antes disso o líquido precisa
> ser **estimado** pelo sistema a partir das taxas conhecidas. Por isso o campo
> `orders.net_source` existe: distingue `computed` (estimado) de `settled`
> (confirmado pelo escrow). Um dashboard que mistura os dois mente para o usuário.

### C.5 Logística

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/logistics/get_tracking_number` | Código de rastreio. | RT |
| `GET /api/v2/logistics/get_tracking_info` | Eventos de rastreio com timestamp. | AN, RT |
| `GET /api/v2/logistics/get_shipping_parameter` | Parâmetros obrigatórios do envio. | — |
| `POST /api/v2/logistics/ship_order` | Despacha. | — |
| `POST /api/v2/logistics/create_shipping_document` | Gera etiqueta. | — |
| `GET /api/v2/logistics/get_channel_list` | Canais logísticos disponíveis. | AN |

### C.6 Marketing

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/voucher/get_voucher_list` · `get_voucher` | Cupons: tipo, desconto, uso, limite. | AN, FIN |
| `GET /api/v2/discount/get_discount_list` · `get_discount` | Promoções por período e itens. | AN, FIN |
| `GET /api/v2/bundle_deal/get_bundle_deal_list` | Kits promocionais. | AN |
| `GET /api/v2/ads/*` | Métricas de anúncios pagos: impressões, cliques, GMV, ROAS. | AN |

**Limitação:** a **Ads API exige whitelist separada** da Shopee. Não vem com a
autorização padrão do app. Sem ela, o custo de mídia entra por lançamento manual
(o sistema suporta isso na aba de Marketing).

### C.7 Chat e livestream

| Endpoint | Retorna | Uso |
|---|---|---|
| `GET /api/v2/sellerchat/get_conversation_list` | Conversas. | AN, RT |
| `GET /api/v2/sellerchat/get_message` | Mensagens. | AN, RT |
| `POST /api/v2/sellerchat/send_message` | Envia. | — |
| `GET /api/v2/livestream/get_session_list` · `get_session_item` · `get_session_metric` | Sessões de live, itens, GMV/viewers. | AN |

**Limitação:** Livestream API tem disponibilidade **regional** e nem sempre está
liberada para o Brasil. O conector detecta a indisponibilidade e desativa o módulo
na interface em vez de exibir erro repetido.

### C.8 Push (webhooks) da Shopee

| Código | Evento | Uso |
|---|---|---|
| `1` | Shop authorization | — |
| `2` | Shop deauthorization | RT |
| `3` | **Order status update** | RT, FIN |
| `4` | Tracking number gerado | RT |
| `5` | Shop info alterada | — |
| `6` | Banned item | RT |
| `9` | Promotion update | AN |
| `10` | Webchat | RT |
| `15` | Order trackingno update | RT |

---

## PARTE D — Síntese por tipo de dado

| Tipo | Mercado Livre | Mercado Pago | Shopee |
|---|---|---|---|
| Pedidos | `/orders/*` ✅ completo | `/merchant_orders` (complementar) | `/order/*` ✅ (janela 15d) |
| Receita bruta | `order_items.unit_price × quantity` | `transaction_amount` | `original_price` / `total_amount` |
| Comissão | `order_items.sale_fee` | `fee_details[]` | `commission_fee` + `service_fee` |
| Taxa de pagamento | via MP | `mercadopago_fee`, `financing_fee` | `transaction_fee` |
| **Líquido** | via MP `net_received_amount` | ✅ nativo | `escrow_amount` (só após conclusão) |
| Repasse/data | `money_release_date` | ✅ release report | `get_payout_detail` |
| Frete (custo real) | `/shipments/{id}/costs` ✅ | `shipping_amount` | `actual_shipping_fee` |
| Estoque | `/items` + Full API ✅ | — | `get_model_list` ✅ |
| Atendimento | perguntas + mensagens ✅ | — | sellerchat ✅ |
| Reclamações | `/post-purchase/claims` ✅ | chargebacks | `returns` |
| Campanhas | `/seller-promotions` | — | voucher/discount ✅ |
| Reputação | `seller_reputation` ✅ | — | `account_health` ✅ |
| Visitas/conversão | `/items/{id}/visits` ✅ | — | `get_item_extra_info` ✅ |
