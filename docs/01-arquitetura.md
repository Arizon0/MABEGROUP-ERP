# 01 — Arquitetura Técnica Completa

## 1.1 Visão em uma frase

O **Marketplace Hub** é um SaaS multi-tenant que se conecta às APIs oficiais do
Mercado Livre, Mercado Pago e Shopee, ingere pedidos/pagamentos/envios/atendimento
em tempo quase real por webhooks + polling incremental, normaliza tudo num **schema
canônico único** em PostgreSQL, e serve um painel vivo com faturamento bruto,
líquido, taxas, repasses e conciliação por seller, loja, marketplace, período,
produto, SKU, campanha e canal logístico.

## 1.2 Princípio arquitetural central: o modelo canônico

O erro clássico de um agregador de marketplaces é espalhar `if marketplace ==
"shopee"` por toda a aplicação. Aqui a regra é rígida:

```
┌────────────────────┐   payloads crus e específicos de cada API
│  Camada Conector   │   (mercadolivre/, mercadopago/, shopee/)
│  (anticorrupção)   │
└─────────┬──────────┘
          │  normalizadores → DTOs canônicos (CanonicalOrder, CanonicalPayment…)
          ▼
┌────────────────────┐   NÃO conhece nenhum marketplace
│  Camada de Domínio │   ingest / finance / reconciliation / analytics
│      + Modelos     │
└─────────┬──────────┘
          ▼
┌────────────────────┐
│  API REST + SSE    │   Também não conhece marketplace, só `channel`
└────────────────────┘
```

Consequência prática: adicionar Amazon, Magalu ou TikTok Shop no futuro é escrever
**um pacote de conector novo**, sem tocar em domínio, API ou frontend.

## 1.3 Diagrama de componentes

```
                    ┌──────────────────────────────────────────┐
   Navegador ──────▶│  Frontend SPA (React + TS + Vite)         │
   (vendedor)       │  TanStack Query · Recharts · Tailwind     │
                    └───────┬──────────────────────┬───────────┘
                            │ REST /api/v1         │ SSE /api/v1/live/stream
                            ▼                      ▼
                    ┌──────────────────────────────────────────┐
                    │      API FastAPI (stateless, N réplicas) │
                    │  auth JWT · RBAC · tenant scoping        │
                    └───┬──────────┬──────────┬────────────┬───┘
                        │          │          │            │
              ┌─────────▼──┐  ┌────▼─────┐ ┌──▼────────┐ ┌─▼─────────────┐
              │ PostgreSQL │  │  Redis   │ │  Object   │ │  Cofre de     │
              │ (Neon/RDS) │  │ fila +   │ │  Storage  │ │  Segredos     │
              │            │  │ cache +  │ │ (R2/S3)   │ │ (env/KMS)     │
              │            │  │ pub/sub  │ │ exports   │ │               │
              └─────▲──────┘  └────▲─────┘ └───────────┘ └───────────────┘
                    │              │
                    │         ┌────┴──────────────────────────┐
                    └─────────│  Workers ARQ (asyncio)        │
                              │  · sync incremental            │
                              │  · processamento de webhooks   │
                              │  · rollup de métricas          │
                              │  · conciliação financeira      │
                              │  · refresh de tokens (cron)    │
                              └────┬───────────────────────────┘
                                   │ HTTPS (httpx, retry, rate limit)
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
    ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
    │  Mercado Livre   │ │  Mercado Pago    │ │  Shopee Open     │
    │  api.mercado     │ │  api.mercado     │ │  partner.shopee  │
    │  libre.com       │ │  pago.com        │ │  mobile.com      │
    └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
             │ webhooks           │ webhooks           │ push
             └────────────────────┴────────────────────┘
                                  ▼
                     POST /api/v1/webhooks/{canal}
                     (valida assinatura → grava cru → ACK <500ms → enfileira)
```

## 1.4 Fluxo de um pedido, ponta a ponta

Este é o caminho crítico do produto. Vale entender em detalhe:

1. **Comprador finaliza a compra** no Mercado Livre.
2. **ML dispara webhook** `POST /api/v1/webhooks/mercadolivre` com
   `{topic: "orders_v2", resource: "/orders/2000012345", user_id: 123}`.
   O ML exige resposta `HTTP 200` em **até 500 ms**, senão reenvia e, após muitas
   falhas, suspende a aplicação.
3. O endpoint faz **apenas três coisas**: valida a origem, grava o evento cru na
   tabela `webhook_events` com chave de idempotência, e devolve `200`. Nenhuma
   chamada externa, nenhum processamento. Tempo típico: ~5 ms.
4. Um **worker ARQ** consome o evento: resolve a conta pelo `user_id`, pega o token
   descriptografado do cofre, chama `GET /orders/2000012345`, e também
   `GET /shipments/{id}` e `GET /v1/payments/{id}` conforme o payload referencia.
5. O **normalizador** converte os três payloads em `CanonicalOrder`,
   `CanonicalShipment` e `CanonicalPayment`.
6. O serviço de **ingestão** faz UPSERT idempotente (`ON CONFLICT` na chave
   natural `(channel_account_id, external_id)`), grava `order_events` para a
   timeline e recalcula o resumo financeiro do pedido em `Decimal`.
7. O serviço publica um evento no **Redis pub/sub**: `tenant:{id}:live`.
8. Todas as réplicas da API assinadas nesse canal empurram o evento pela conexão
   **SSE** aberta com o navegador. O painel ao vivo atualiza sem F5, tipicamente
   **2 a 6 segundos** depois da compra real.
9. Mais tarde (T+1 a T+14), o job de **conciliação** casa esse pedido com o
   relatório de liberação do Mercado Pago e marca divergência se o valor
   efetivamente creditado diferir do líquido previsto.

## 1.5 Por que cada tecnologia

| Camada | Escolha | Justificativa objetiva |
|---|---|---|
| Backend | **Python 3.11 + FastAPI** | Async nativo (essencial: a carga é I/O-bound contra 3 APIs externas), OpenAPI automático, Pydantic v2 para tipagem forte na borda. Mantém continuidade com o ERP Python já existente da operação. |
| ORM | **SQLAlchemy 2.0 async + Alembic** | `Mapped[]` dá tipagem estática real; Alembic é o padrão de facto para migrations versionadas e reversíveis. |
| Banco | **PostgreSQL 15+ gerenciado** | Integridade referencial é inegociável em dado financeiro; `JSONB` guarda o payload cru para auditoria e reprocessamento; índices parciais e `BRIN` em séries temporais; particionamento nativo para retenção. |
| Fila/Jobs | **ARQ sobre Redis** | Asyncio-nativo (mesmo loop do FastAPI e do httpx — sem ponte sync/async como no Celery), tem cron embutido, retentativas com backoff e deferimento. Menos peça móvel que Celery+Beat. |
| Cache/PubSub | **Redis 7** | Um único serviço resolve três necessidades: broker da fila, cache de respostas caras (categorias, taxas) e pub/sub que alimenta o SSE entre réplicas. |
| Realtime | **SSE (Server-Sent Events)** | O tráfego é unidirecional servidor→cliente. SSE reconecta sozinho (`Last-Event-ID`), passa em qualquer proxy HTTP e não exige sticky session como WebSocket. Metade da complexidade, 100% do resultado. |
| Frontend | **React 18 + TypeScript + Vite** | Ecossistema maduro, build instantâneo, tipagem ponta a ponta com os schemas do backend. |
| Estado servidor | **TanStack Query** | Cache, revalidação, polling e estados de erro/loading resolvidos por biblioteca em vez de `useEffect` manual. |
| Gráficos | **Recharts** | Declarativo, componível com React, suficiente para séries temporais, rankings e comparativos. |
| Estilo | **Tailwind CSS** | Consistência visual sem CSS órfão; tema claro/escuro por tokens. |
| HTTP externo | **httpx + tenacity** | Cliente async com pool de conexões, timeouts granulares e retry exponencial com jitter. |
| Cripto | **cryptography (Fernet)** | AES-128-CBC + HMAC autenticado para o cofre de tokens, com chave fora do banco. |
| Observabilidade | **structlog + Prometheus + Sentry + OpenTelemetry** | Log estruturado JSON correlacionado por `request_id`; métricas RED; traces distribuídos API→worker→API externa. |

## 1.6 Componentes de infraestrutura recomendados (serviços reais)

Não existe "nuvem da Claude". A infraestrutura roda em provedores gerenciados
reais. Três combinações válidas, por estágio:

### Estágio A — MVP / primeiros clientes (~US$ 25–60/mês)

| Componente | Serviço | Plano | Por quê |
|---|---|---|---|
| Banco | **Neon** ou **Supabase** | Postgres gerenciado, free→US$19 | Branching de banco por PR (Neon), backup PITR, escala a zero. |
| API + Worker | **Railway** ou **Render** | US$5–20 | Deploy por Git push, dois serviços do mesmo repo (web + worker). |
| Redis | **Upstash Redis** | pay-per-request | Serverless, sem servidor ocioso; compatível com o protocolo Redis. |
| Frontend | **Vercel** ou **Cloudflare Pages** | free | CDN global, preview por PR. |
| Storage | **Cloudflare R2** | US$0,015/GB | Sem custo de egress — importante para exportações. |
| Erros | **Sentry** | free tier | |

### Estágio B — Produto com tração (~US$ 300–800/mês)

| Componente | Serviço | Detalhe |
|---|---|---|
| Banco | **AWS RDS Postgres Multi-AZ** ou **Neon Scale** | Réplica de leitura dedicada às queries analíticas. |
| Compute | **AWS ECS Fargate** ou **Google Cloud Run** | Autoscaling por CPU/fila; worker escala separado da API. |
| Redis | **AWS ElastiCache** ou **Upstash Pro** | |
| Observabilidade | **Grafana Cloud** | Prometheus + Loki + Tempo num só lugar. |

### Estágio C — Escala (múltiplos milhares de sellers)

Adicionar: **ClickHouse** ou **Postgres + TimescaleDB** como *data warehouse* de
leitura para as agregações pesadas; **Kafka/Redpanda** substituindo o Redis Streams
quando o volume de eventos passar de ~10k/s; particionamento de `orders` e
`webhook_events` por mês com política de arquivamento em Parquet no S3.

> **Recomendação para começar hoje:** Neon (Postgres) + Upstash (Redis) +
> Railway (API + worker) + Vercel (frontend). Custo inicial próximo de zero,
> caminho de migração claro para o Estágio B sem reescrever nada — todos falam
> Postgres e Redis padrão.

## 1.7 Camadas do backend

```
app/
├── api/v1/          Borda HTTP. Só valida entrada, chama serviço, formata saída.
│                    Proibido: SQL, regra de negócio, chamada a API externa.
├── services/        Regra de negócio. Recebe sessão + DTOs. Sem conhecimento de HTTP.
├── connectors/      Camada anticorrupção. Fala HTTP com as APIs externas e traduz.
│   ├── base.py      Protocolo comum (Connector) que todo marketplace implementa.
│   ├── http.py      Cliente resiliente: retry, backoff, rate limit, circuit breaker.
│   └── {canal}/     client.py · oauth.py · normalizer.py · sync.py · mock.py
├── models/          SQLAlchemy. Estrutura, índices, constraints. Sem lógica.
├── schemas/         Pydantic v2. Contratos de entrada/saída da API.
├── events/          Barramento de eventos (Redis pub/sub, fallback em memória).
├── workers/         Definições de tarefas e cron do ARQ.
├── core/            Config, segurança, cripto, erros, logging, rate limit, deps.
└── db/              Engine, sessão, tipos customizados.
```

**Regra de dependência (verificada em teste):** as setas apontam sempre para
dentro. `api → services → models`. `connectors` só é chamado por `services` e
`workers`. `models` não importa nada de cima. Um teste automatizado
(`test_arquitetura.py`) falha o CI se essa regra for violada.

## 1.8 Multi-tenancy e isolamento

O modelo é **multi-tenant com banco compartilhado e coluna discriminadora**,
reforçado em três níveis independentes:

1. **Modelo**: toda tabela de negócio carrega `tenant_id NOT NULL` com FK.
2. **Aplicação**: a dependência `get_tenant_scope()` extrai o `tenant_id` do JWT
   e **todo** repositório recebe esse escopo. Não existe query de negócio sem
   filtro de tenant — há teste que percorre os routers verificando isso.
3. **Banco (opcional, recomendado em produção)**: `ROW LEVEL SECURITY` no Postgres
   com `SET LOCAL app.current_tenant`, de forma que mesmo uma query com bug não
   consiga enxergar dados de outro tenant.

Hierarquia: `Tenant` (empresa cliente do SaaS) → `ChannelAccount` (cada conta ML,
MP ou Shopee conectada) → dados operacionais. Um tenant pode ter N contas do mesmo
marketplace — o caso real de quem opera várias lojas.

## 1.9 Resiliência

| Falha | Tratamento |
|---|---|
| API externa fora do ar | Retry exponencial com jitter (1s, 2s, 4s, 8s, 16s), circuit breaker abre após 5 falhas consecutivas e semiabre em 60s. |
| Rate limit (HTTP 429) | Respeita `Retry-After`; token bucket local por conta impede estourar antes de bater no limite. |
| Token expirado (401) | Refresh automático, uma única vez por requisição, com lock distribuído no Redis para evitar refresh concorrente (o refresh token do ML é de uso único — dois refresh simultâneos invalidam a conta). |
| Webhook duplicado | Chave de idempotência `(channel, external_event_id)` com UNIQUE; a segunda entrega vira no-op. |
| Worker morre no meio | O evento continua `pending` na tabela; o job de varredura reprocessa o que passou do SLA. |
| Erro permanente (4xx) | Após `max_attempts`, vai para `status='dead'` com o erro registrado — DLQ consultável e reprocessável pela tela de Configurações. |
| Perda de webhook | Polling incremental de segurança roda a cada 5–15 min por marca d'água (`sync_cursors`), pegando o que o webhook não entregou. |

**Ponto importante:** webhook e polling são redundantes de propósito. Webhook dá
latência baixa; polling dá garantia de completude. Ambos convergem no mesmo UPSERT
idempotente, então processar o mesmo pedido duas vezes é inofensivo.

## 1.10 Versionamento de integrações

Cada conector declara `API_VERSION` e cada payload cru é gravado com a versão que
o produziu (`raw_payloads.connector_version`). Quando um marketplace muda um
contrato, o normalizador ganha uma variante nova e os registros antigos continuam
reprocessáveis com o normalizador da versão que os criou. Sem isso, uma mudança de
contrato corrompe silenciosamente o histórico financeiro.
