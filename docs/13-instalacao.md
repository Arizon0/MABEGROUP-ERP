# 13 — Instalação e Operação

## 13.1 Requisitos

| Item | Versão | Observação |
|---|---|---|
| Python | 3.11+ | |
| Node.js | 20+ | 22 recomendado |
| PostgreSQL | 15+ | Opcional em desenvolvimento (SQLite serve) |
| Redis | 7+ | Opcional em desenvolvimento (barramento cai para memória) |
| Docker | 24+ | Caminho mais rápido |

## 13.2 Instalação local — caminho rápido

```bash
git clone https://github.com/Arizon0/MABEGROUP-ERP.git
cd MABEGROUP-ERP
cp .env.example .env      # os padrões já funcionam em modo simulado
docker compose up --build
```

Sobe Postgres, Redis, migrations, API, worker e painel. Acesse
<http://localhost:5173> com `admin@marketplacehub.com.br` / `admin123`.

## 13.3 Instalação local — sem Docker

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export USE_MOCK_CONNECTORS=1       # roda sem credencial nenhuma
uvicorn app.main:app --reload --port 8000
```

Fora de produção, o schema e o usuário inicial são criados no primeiro start.

### Worker (outro terminal)

```bash
cd backend && source .venv/bin/activate
export REDIS_URL=redis://localhost:6379/0
arq app.workers.settings.WorkerSettings
```

Sem Redis, os webhooks são processados no próprio processo da API e os jobs
agendados não rodam — suficiente para desenvolver, insuficiente para produção.

### Frontend (outro terminal)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

## 13.4 Popular com dados de demonstração

Pela interface: **Configurações → Sincronizar** em qualquer conta.

Pelo terminal:

```bash
cd backend && source .venv/bin/activate
python -c "
import asyncio
from app.workers.tasks import semear_demonstracao
print(asyncio.run(semear_demonstracao({}, 1)))
"
```

## 13.5 Testes

```bash
cd backend && pytest -q             # 107 testes
cd frontend && npx vitest run       # 13 testes
cd frontend && npx tsc --noEmit     # verificação de tipos
```

A suíte roda **sem rede**: os conectores simulados garantem que o CI não dependa
da disponibilidade das APIs dos marketplaces.

## 13.6 Migrations

```bash
cd backend
alembic upgrade head                              # aplicar
alembic revision --autogenerate -m "descrição"    # gerar após mudar models
alembic downgrade -1                              # reverter uma
alembic history                                   # histórico
```

> **Regra de compatibilidade.** Toda migration precisa funcionar com a versão
> anterior do código (expandir → migrar → contrair). Coluna nova entra como
> `NULL` ou com default; a remoção acontece num deploy posterior, quando nenhuma
> réplica antiga estiver no ar. Sem isso, um rollback vira incidente.

## 13.7 Produção

### Antes do primeiro deploy

- [ ] `SECRET_KEY` forte e única
- [ ] `MASTER_ENCRYPTION_KEY` gerada (`Fernet.generate_key()`) — **obrigatória**
- [ ] `BUYER_HASH_PEPPER` alterada
- [ ] `ENVIRONMENT=production`
- [ ] `USE_MOCK_CONNECTORS=0`
- [ ] `CORS_ORIGINS` com as origens explícitas (nunca `*`)
- [ ] Senha do administrador trocada
- [ ] `DATABASE_URL` com `sslmode=require`
- [ ] Backup automático com PITR ativado
- [ ] **NTP ativo no host** — a Shopee rejeita requisições com timestamp fora de ±5 min

### Sequência de deploy

```bash
# 1. Migrations como job separado, ANTES da aplicação
alembic upgrade head

# 2. API (N réplicas)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# 3. Worker (escala independente, por profundidade de fila)
arq app.workers.settings.WorkerSettings
```

Rodar migration no startup de N réplicas causa migrations concorrentes —
por isso o job separado.

### Provedores recomendados

| Componente | Estágio inicial | Escala |
|---|---|---|
| Banco | Neon / Supabase | AWS RDS Multi-AZ |
| API + Worker | Railway / Render | ECS Fargate / Cloud Run |
| Redis | Upstash | ElastiCache |
| Frontend | Vercel / Cloudflare Pages | idem |
| Storage | Cloudflare R2 | S3 |

Detalhes e custos em [`docs/09-deploy.md`](09-deploy.md).

## 13.8 Verificação pós-deploy

```bash
curl https://api.seudominio.com/health
```

Deve responder `status: ok`, `banco: ok` e `redis: ok`. O campo
`hora_servidor` existe para diagnosticar desvio de relógio — causa comum de
falha de autenticação na Shopee, cujo sintoma é um erro genérico difícil de
rastrear.

## 13.9 Problemas frequentes

| Sintoma | Causa provável | Solução |
|---|---|---|
| `MASTER_ENCRYPTION_KEY é obrigatória` | Chave ausente em produção | Gerar com `Fernet.generate_key()` |
| Painel ao vivo não atualiza | Sem Redis, ou proxy com buffer ligado | Configurar `REDIS_URL`; `proxy_buffering off` |
| Shopee: erro de autenticação constante | Relógio dessincronizado | Ativar NTP no host |
| ML: conta desconectada sozinha | Refresh concorrente sem Redis | Configurar `REDIS_URL` (lock distribuído) |
| Webhooks não chegam | URL não cadastrada no portal | Conferir no DevCenter / Open Platform |
| `no such table` em teste | Banco de teste não recriado | `rm -f /tmp/marketplace_hub_teste.db` |
| Margem aparece como "—" | Produto sem custo cadastrado | Cadastrar em Produtos, ou mapear o SKU |
