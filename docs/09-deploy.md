# 09 — Estratégia de Deploy e Operação

## 9.1 Topologia de produção

```
                      Cloudflare (DNS + WAF + CDN)
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     app.seudominio.com                api.seudominio.com
     Frontend estático                  Load balancer
     (Vercel / CF Pages)                       │
                                ┌──────────────┴──────────────┐
                                ▼                             ▼
                        API (2..N réplicas)          Worker (1..N réplicas)
                        FastAPI + uvicorn            ARQ (fila + cron)
                        autoscale por CPU            autoscale por profundidade da fila
                                └──────────────┬──────────────┘
                                               ▼
                        PostgreSQL gerenciado (primário + réplica de leitura)
                        Redis gerenciado
                        Object storage (exports, etiquetas)
```

**Separar API e worker em serviços distintos é obrigatório**, não estético: um
backfill de 24 meses satura CPU por horas. Se rodar no mesmo processo da API, o
painel de todos os tenants fica lento durante o onboarding de um único cliente.
Além disso, cada um escala por um sinal diferente — API por latência HTTP, worker
por profundidade de fila.

## 9.2 Ambientes

| Ambiente | Banco | APIs externas | Deploy |
|---|---|---|---|
| Local | Postgres em Docker | **Mocks** (`USE_MOCK_CONNECTORS=1`) | `docker compose up` |
| CI | SQLite/Postgres efêmero | Mocks | GitHub Actions |
| Staging | Neon branch | Sandbox (ML test users, MP test, Shopee `test-stable`) | Auto no merge para `main` |
| Produção | Postgres Multi-AZ | Produção | Manual/tag, com aprovação |

O modo mock é o que permite desenvolver a aplicação inteira **sem credencial
nenhuma** — os conectores mock geram pedidos, pagamentos e envios realistas.
Sem isso, ninguém consegue rodar o projeto no primeiro dia.

## 9.3 Pipeline de CI/CD

```
push / PR
  ├── lint (ruff) + format (ruff format --check)
  ├── type check (mypy --strict no backend, tsc --noEmit no frontend)
  ├── testes backend (pytest + cobertura, mínimo 80% em services/)
  ├── testes frontend (vitest)
  ├── build do frontend (vite build)
  ├── build da imagem Docker + scan (Trivy)
  ├── secret scanning + pip-audit + npm audit
  └── se main: deploy staging → smoke test → (aprovação) → produção
```

Migrations rodam como **job separado antes** do deploy da aplicação, nunca no
startup do processo web. Startup com migration em N réplicas = N migrations
concorrentes = corrupção.

**Regra de compatibilidade:** toda migration precisa ser compatível com a versão
anterior do código (expand → migrate → contract). Coluna nova entra como
`NULL`/com default; a remoção de coluna acontece em um deploy posterior, depois que
nenhuma réplica antiga está mais no ar. Sem isso, rollback vira incidente.

## 9.4 Configuração por ambiente

Todas as variáveis em `.env.example`. As essenciais:

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
REDIS_URL=redis://host:6379/0
SECRET_KEY=...                  # JWT
MASTER_ENCRYPTION_KEY=...       # cofre de tokens (Fernet, base64 de 32 bytes)

ML_CLIENT_ID=...                ML_CLIENT_SECRET=...      ML_REDIRECT_URI=...
MP_CLIENT_ID=...                MP_CLIENT_SECRET=...      MP_WEBHOOK_SECRET=...
SHOPEE_PARTNER_ID=...           SHOPEE_PARTNER_KEY=...    SHOPEE_REDIRECT_URI=...

USE_MOCK_CONNECTORS=0
SENTRY_DSN=...                  LOG_LEVEL=INFO            ENVIRONMENT=production
```

## 9.5 Observabilidade

| Sinal | Ferramenta | O que responde |
|---|---|---|
| Logs | structlog → JSON → Loki/CloudWatch | "o que aconteceu com o pedido X?" — correlacionado por `request_id` e `tenant_id` |
| Métricas | Prometheus (`/metrics`) → Grafana | "a fila está crescendo?" |
| Traces | OpenTelemetry → Tempo/Jaeger | "onde foram os 3 s dessa requisição?" |
| Erros | Sentry | "quantos usuários esse bug atingiu?" |
| Uptime | Better Stack / UptimeRobot | "o `/health` está de pé?" |

**Métricas de negócio expostas** (não só técnicas — são elas que revelam problema
silencioso de integração):

```
marketplace_hub_webhooks_received_total{channel,topic}
marketplace_hub_webhook_processing_seconds{channel}
marketplace_hub_sync_lag_seconds{channel,resource}      ← o mais importante
marketplace_hub_api_calls_total{channel,endpoint,status}
marketplace_hub_rate_limit_hits_total{channel}
marketplace_hub_queue_depth{queue}
marketplace_hub_reconciliation_divergence_total{channel}
marketplace_hub_token_refresh_failures_total{channel}
```

### Alertas

| Alerta | Condição | Severidade |
|---|---|---|
| Sync atrasado | `sync_lag_seconds > 900` | Alta |
| Fila crescendo | `queue_depth > 1000` por 10 min | Alta |
| Falha de refresh de token | qualquer ocorrência | **Crítica** (o seller perde a conexão) |
| Rate limit recorrente | > 10/min | Média |
| Erro 5xx | > 1% das requisições | Alta |
| Divergência financeira | > 5% dos pedidos do dia | Média |
| Banco | conexões > 80% do pool | Alta |

## 9.6 Backup e recuperação

| Item | Política | RPO | RTO |
|---|---|---|---|
| Postgres | Snapshot diário + PITR contínuo | 5 min | 1 h |
| Redis | Só cache e fila (efêmero por design) | — | — |
| Object storage | Versionamento + replicação | 0 | min |
| Segredos | Cofre do provedor com versionamento | 0 | min |

**Teste de restauração mensal, obrigatório e registrado.** Um backup que nunca foi
restaurado é uma suposição, não uma garantia.

## 9.7 Custos estimados

| Estágio | Sellers | Pedidos/mês | Custo mensal |
|---|---|---|---|
| MVP | 1–10 | até 50 mil | US$ 25–60 |
| Crescimento | 10–100 | até 500 mil | US$ 150–400 |
| Escala | 100–1.000 | até 5 milhões | US$ 800–2.500 |

O custo cresce sublinearmente porque o gargalo é I/O contra API externa, não CPU —
e workers ficam ociosos entre janelas de sincronização, o que casa bem com
autoscaling.
