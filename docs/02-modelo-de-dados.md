# 02 — Modelo de Dados

## 2.1 Convenções aplicadas a todas as tabelas

| Convenção | Regra |
|---|---|
| Chave primária | `BIGSERIAL` interno. **Nunca** usar o ID do marketplace como PK — eles colidem entre canais e mudam de tipo. |
| Identidade externa | `external_id VARCHAR` + `UNIQUE (channel_account_id, external_id)`. Essa é a chave natural de idempotência. |
| Isolamento | `tenant_id BIGINT NOT NULL REFERENCES tenants(id)` em toda tabela de negócio. |
| Dinheiro | `NUMERIC(18,4)` mapeado para `Decimal` em Python. **Nunca `float`** — 0,1 + 0,2 ≠ 0,3 em binário, e num extrato financeiro isso vira divergência de centavos que ninguém consegue explicar ao contador. |
| Moeda | `currency CHAR(3)` junto de todo valor monetário (multi-país: BRL, ARS, MXN…). |
| Tempo | `TIMESTAMPTZ` sempre, armazenado em UTC. A conversão para `America/Sao_Paulo` acontece na borda de apresentação. |
| Auditoria | `created_at`, `updated_at` automáticos; `synced_at` nas entidades espelhadas do marketplace. |
| Payload cru | `raw JSONB` na entidade ou em `raw_payloads` — permite reprocessar histórico quando o normalizador evolui, e serve de prova em disputa financeira. |
| Soft delete | `deleted_at TIMESTAMPTZ NULL` nas entidades que o usuário pode remover. Dado financeiro **nunca** é apagado fisicamente. |

## 2.2 Mapa de entidades

```
tenants ──┬── users ──── user_sessions
          │
          ├── channel_accounts ──┬── channel_credentials (tokens cifrados, versionados)
          │        │             ├── oauth_states (CSRF + PKCE, TTL)
          │        │             └── sync_cursors (marca d'água por recurso)
          │        │
          │        ├── listings ──── listing_variations
          │        │       └── inventory_snapshots
          │        │
          │        ├── orders ──┬── order_items ──── (FK → listings, products)
          │        │            ├── order_events (timeline)
          │        │            ├── shipments ──── shipment_events
          │        │            └── payments ──┬── payment_fees
          │        │                           └── refunds
          │        │
          │        ├── settlements ──── settlement_entries → payments/orders
          │        ├── questions
          │        ├── messages
          │        ├── claims ──── claim_events
          │        ├── reviews
          │        └── campaigns ──── campaign_items
          │
          ├── products ──── sku_links (de-para sku_canal → produto interno)
          │        └── sku_pendencies
          │
          ├── reconciliations
          ├── metrics_daily / metrics_hourly (rollups)
          ├── webhook_events (ingestão crua idempotente)
          ├── integration_logs
          ├── audit_logs
          └── alerts / alert_rules
```

## 2.3 Tabelas detalhadas

### Núcleo SaaS

**`tenants`** — a empresa cliente do SaaS.

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `name` | VARCHAR(200) | |
| `slug` | VARCHAR(60) UNIQUE | subdomínio/rota |
| `plan` | VARCHAR(30) | `trial`, `starter`, `pro`, `enterprise` |
| `timezone` | VARCHAR(50) | default `America/Sao_Paulo` |
| `status` | VARCHAR(20) | `active`, `suspended` |

**`users`** — `tenant_id`, `email` (UNIQUE por tenant), `password_hash` (bcrypt),
`full_name`, `role` (`owner`/`admin`/`analyst`/`viewer`), `is_active`,
`last_login_at`, `mfa_secret` (nullable).

**`audit_logs`** — `tenant_id`, `user_id`, `action`, `entity_type`, `entity_id`,
`before JSONB`, `after JSONB`, `ip`, `user_agent`, `created_at`. Alimenta a aba de
Auditoria em Configurações. Toda mutação sensível (conectar conta, revogar token,
alterar permissão, exportar dados) grava aqui.

### Integração

**`channel_accounts`** — uma conta conectada de marketplace.

| Coluna | Tipo | Nota |
|---|---|---|
| `tenant_id` | BIGINT FK | |
| `channel` | VARCHAR(20) | `mercadolivre`, `mercadopago`, `shopee` |
| `external_account_id` | VARCHAR(64) | `user_id` do ML, `shop_id` da Shopee |
| `nickname` | VARCHAR(120) | apelido da loja |
| `site_id` | VARCHAR(10) | `MLB`, `MLA`… / região Shopee |
| `status` | VARCHAR(20) | `connected`, `expired`, `revoked`, `error` |
| `scopes` | JSONB | permissões concedidas |
| `connected_at`, `last_sync_at`, `last_error` | | |

`UNIQUE (channel, external_account_id, tenant_id)`.

**`channel_credentials`** — o cofre. Tokens **nunca** em texto claro.

| Coluna | Tipo | Nota |
|---|---|---|
| `channel_account_id` | BIGINT FK | |
| `access_token_enc` | BYTEA | Fernet(AES-128-CBC + HMAC) |
| `refresh_token_enc` | BYTEA | idem |
| `access_expires_at` | TIMESTAMPTZ | |
| `refresh_expires_at` | TIMESTAMPTZ | ML: 6 meses; Shopee: 30 dias |
| `key_version` | SMALLINT | suporta rotação de chave sem downtime |
| `rotated_at` | TIMESTAMPTZ | |
| `is_current` | BOOLEAN | histórico preservado para auditoria |

**`oauth_states`** — `state` (UNIQUE), `code_verifier_enc` (PKCE), `tenant_id`,
`channel`, `redirect_after`, `expires_at`. TTL de 10 min; consumido uma única vez.

**`sync_cursors`** — marca d'água da sincronização incremental.

| Coluna | Nota |
|---|---|
| `channel_account_id`, `resource` | `orders`, `payments`, `items`, `questions`, `escrow`… |
| `last_synced_at` | limite superior da última janela processada |
| `last_external_id` | desempate quando o timestamp tem granularidade grossa |
| `cursor_token` | paginação opaca (`scroll_id` do ML, `next_offset` da Shopee) |
| `status`, `last_error`, `consecutive_failures` | | 

`UNIQUE (channel_account_id, resource)`.

**`webhook_events`** — porta de entrada crua, a tabela mais crítica da ingestão.

| Coluna | Tipo | Nota |
|---|---|---|
| `channel` | VARCHAR(20) | |
| `topic` | VARCHAR(60) | `orders_v2`, `payment`, `order_status`… |
| `resource` | VARCHAR(255) | `/orders/2000012345` |
| `external_event_id` | VARCHAR(128) | id da notificação quando existe |
| `idempotency_key` | VARCHAR(160) UNIQUE | `sha256(channel:topic:resource:version)` |
| `payload` | JSONB | corpo íntegro recebido |
| `signature_valid` | BOOLEAN | |
| `status` | VARCHAR(20) | `pending`, `processing`, `done`, `failed`, `dead` |
| `attempts` | SMALLINT | |
| `next_attempt_at`, `received_at`, `processed_at`, `error` | | |

Índices: `(status, next_attempt_at)` para o coletor da fila; `(channel, received_at DESC)` para a tela de diagnóstico.

### Comercial

**`orders`** — pedido canônico. Núcleo do produto.

| Coluna | Tipo | Nota |
|---|---|---|
| `tenant_id`, `channel_account_id`, `channel` | | |
| `external_id` | VARCHAR(64) | `UNIQUE (channel_account_id, external_id)` |
| `external_pack_id` | VARCHAR(64) | carrinho/pacote multi-item |
| `status` | VARCHAR(30) | canônico: `pending`, `paid`, `processing`, `shipped`, `delivered`, `cancelled`, `returned` |
| `status_raw` | VARCHAR(60) | status original do canal, preservado |
| `date_created`, `date_closed`, `date_last_updated` | TIMESTAMPTZ | |
| `currency` | CHAR(3) | |
| `gross_amount` | NUMERIC(18,4) | soma dos itens |
| `shipping_revenue` | NUMERIC(18,4) | frete cobrado do comprador |
| `shipping_cost` | NUMERIC(18,4) | custo do frete pago pelo seller (negativo) |
| `platform_fee` | NUMERIC(18,4) | comissão do marketplace |
| `payment_fee` | NUMERIC(18,4) | taxa de meio de pagamento |
| `discount_amount`, `refund_amount`, `tax_amount` | NUMERIC(18,4) | |
| `net_amount` | NUMERIC(18,4) | **calculado** — ver doc 06 |
| `net_source` | VARCHAR(20) | `api_reported`, `computed`, `settled` — a procedência importa |
| `buyer_hash` | VARCHAR(64) | SHA-256 do id do comprador (pseudonimização LGPD) |
| `buyer_nickname` | VARCHAR(120) | quando a API permite |
| `ship_state`, `ship_city` | VARCHAR | análise geográfica |
| `logistic_type` | VARCHAR(40) | `fulfillment`, `self_service`(Flex), `cross_docking`, `drop_off` |
| `is_test`, `has_multiple_items` | BOOLEAN | |
| `raw` | JSONB | |

Índices:
```sql
CREATE INDEX ix_orders_tenant_date    ON orders (tenant_id, date_created DESC);
CREATE INDEX ix_orders_tenant_status  ON orders (tenant_id, status, date_created DESC);
CREATE INDEX ix_orders_account_date   ON orders (channel_account_id, date_created DESC);
CREATE INDEX ix_orders_live           ON orders (tenant_id, date_created DESC)
                                       WHERE date_created > now() - interval '7 days';
CREATE INDEX ix_orders_geo            ON orders (tenant_id, ship_state, date_created DESC);
```
O índice parcial `ix_orders_live` é o que mantém o painel ao vivo respondendo em
milissegundos mesmo com dezenas de milhões de pedidos históricos.

**`order_items`** — `order_id`, `external_item_id`, `listing_id` FK, `variation_id`
FK, `product_id` FK (via de-para), `sku_channel`, `sku_base`, `title`, `quantity`,
`unit_price`, `gross_amount`, `platform_fee` (rateada), `discount_amount`,
`unit_cost` e `cogs` **congelados na ingestão** (regra herdada do ERP: mudar o custo
do produto hoje não pode reescrever a margem de um pedido de seis meses atrás).

**`order_events`** — timeline: `order_id`, `event_type`, `from_status`,
`to_status`, `source` (`webhook`/`sync`/`manual`), `payload JSONB`, `occurred_at`.

**`shipments`** — `order_id`, `external_id`, `status`, `substatus`, `tracking_number`,
`carrier`, `logistic_type`, `estimated_delivery`, `date_shipped`, `date_delivered`,
`cost_seller`, `cost_buyer`, `receiver_state`, `receiver_city`, `delay_days`
(calculado), `raw`.
**`shipment_events`** — histórico de rastreio: `status`, `substatus`, `description`,
`occurred_at`.

### Financeiro

**`payments`** — `order_id`, `channel_account_id`, `external_id`, `provider`
(`mercadopago`/`shopee_escrow`), `status`, `status_detail`, `payment_method`,
`installments`, `transaction_amount`, `total_paid_amount`, `net_received_amount`,
`taxes_amount`, `date_approved`, `date_released` (quando o dinheiro fica disponível),
`money_release_status`, `raw`.

**`payment_fees`** — a granularidade que torna a conciliação auditável:
`payment_id`, `fee_type` (`marketplace_fee`, `mercadopago_fee`, `financing_fee`,
`shipping_fee`, `application_fee`, `commission`, `service_fee`, `transaction_fee`),
`amount`, `payer` (`collector`/`payer`).

**`refunds`** — `payment_id`, `external_id`, `amount`, `reason`, `status`,
`is_chargeback`, `date_created`.

**`settlements`** (repasses) — `channel_account_id`, `external_id`,
`settlement_date`, `gross_amount`, `fee_amount`, `net_amount`, `status`,
`bank_reference`, `source` (`mp_release_report`, `shopee_payout`), `raw`.
**`settlement_entries`** — liga cada linha do repasse a `payment_id`/`order_id`,
com `amount` e `entry_type`. É o que responde "esse R$ 4.312,88 que caiu na conta
corresponde a quais pedidos?".

**`reconciliations`** — resultado do casamento: `order_id`, `expected_net`,
`settled_net`, `divergence`, `divergence_pct`, `status`
(`matched`, `divergent`, `pending_settlement`, `unmatched`), `checked_at`, `notes`.

### Catálogo e estoque

**`products`** — produto interno do seller (SKU base): `tenant_id`, `sku` (UNIQUE
por tenant), `name`, `brand`, `unit_cost`, `ncm`, `ean`, `weight_grams`, `is_active`.

**`listings`** — anúncio: `channel_account_id`, `external_id` (MLB…/item_id Shopee),
`title`, `status`, `listing_type` (`gold_pro`/`gold_special`), `category_id`,
`price`, `available_quantity`, `sold_quantity`, `permalink`, `thumbnail`,
`logistic_type`, `health` (qualidade do anúncio no ML), `raw`.

**`listing_variations`** — `listing_id`, `external_variation_id` (variation_id /
model_id), `sku_channel`, `attributes JSONB`, `price`, `available_quantity`.

**`inventory_snapshots`** — série temporal de estoque: `listing_id`,
`variation_id`, `available`, `reserved`, `captured_at`. Sem isso é impossível
responder "quantos dias esse SKU ficou em ruptura no mês passado?".

**`sku_links`** — de-para `sku_channel` → `product_id`, com `channel`,
`confidence`, `created_by`. **`sku_pendencies`** — SKU visto na ingestão sem
mapeamento, com `occurrences` e `last_seen_at`. Regra: pendência **nunca** bloqueia
a importação do pedido.

### Atendimento e marketing

**`questions`** — `listing_id`, `external_id`, `text`, `answer_text`, `status`,
`date_created`, `date_answered`, `response_time_seconds` (calculado).
**`messages`** — `order_id`, `external_id`, `pack_id`, `from_role`, `text`,
`attachments JSONB`, `sent_at`.
**`claims`** — `order_id`, `external_id`, `type` (`claim`/`mediation`/`dispute`/
`return`), `stage`, `status`, `reason_code`, `resolution`, `amount_involved`,
`opened_at`, `closed_at`. **`claim_events`** — andamento.
**`reviews`** — `listing_id`, `order_id`, `rating`, `comment`, `date_created`.
**`campaigns`** — `channel_account_id`, `external_id`, `name`, `type`
(`voucher`/`discount`/`bundle`/`ads`/`deal_of_day`), `status`, `start_at`,
`end_at`, `budget`, `raw`. **`campaign_items`** — `listing_id`, `promo_price`,
`stock_limit`.

### Métricas

**`metrics_hourly`** e **`metrics_daily`** — rollups pré-agregados por
`(tenant_id, channel_account_id, bucket)` com `orders_count`, `units`,
`gross_amount`, `net_amount`, `fees_amount`, `shipping_amount`, `cancelled_count`,
`cancelled_amount`, `avg_ticket`. Existem por um motivo específico: um dashboard
que agrega 2 milhões de linhas a cada F5 não escala. O rollup roda no worker e o
painel lê linhas prontas.

**`metrics_snapshots`** — fotografia diária de indicadores não-derivávveis do
histórico (reputação do seller, taxa de cancelamento reportada pelo canal, nível de
Mercado Líder). Esses valores mudam no marketplace sem deixar rastro; se não
fotografar, o histórico se perde.

## 2.4 Retenção, particionamento e arquivamento

| Tabela | Política |
|---|---|
| `webhook_events` | Particionada por mês. Retenção quente 90 dias; depois `DETACH` + export Parquet no object storage. |
| `integration_logs` | Retenção 30 dias. |
| `order_events`, `shipment_events` | Particionadas por trimestre; retenção quente 24 meses. |
| `orders`, `payments`, `settlements` | **Nunca expiram.** São a base contábil. Particionar por ano acima de ~20M linhas. |
| `inventory_snapshots` | Granularidade horária por 30 dias, depois compactada para diária. |
| `metrics_hourly` | 180 dias; `metrics_daily` permanente. |

## 2.5 Rastreabilidade e histórico de alterações

Três mecanismos complementares:

1. **`audit_logs`** — o que um *usuário humano* fez.
2. **`order_events` / `claim_events` / `shipment_events`** — o que o *marketplace*
   fez, na ordem em que aconteceu.
3. **`raw JSONB` + `webhook_events`** — a *prova bruta*. Se um seller contestar um
   número do dashboard, dá para reconstruir exatamente qual payload gerou aquele
   valor e em que momento chegou.
