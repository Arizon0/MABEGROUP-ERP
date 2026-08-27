# Publicar a custo zero — Render free + Supabase + cron do GitHub

A arquitetura completa do Hub é API + worker + Postgres + Redis. Este guia
publica uma variante **deliberadamente enxuta** que roda de graça e não muda
uma linha de código — só troca *quem* dispara as coisas:

| Peça da arquitetura | Nesta variante | Custo |
|---|---|---|
| API + painel | 1 serviço web no Render (free, 750h/mês) | R$ 0 |
| Postgres | Supabase (projeto existente) | R$ 0 |
| Redis | **dispensado** — modo degradado documentado (`REDIS_URL=""`) | R$ 0 |
| Worker | **dispensado** — cron do GitHub Actions chama `POST /accounts/{id}/sync` | R$ 0 |
| Anti-soneca | UptimeRobot (free) pinga `/health` a cada 5 min | R$ 0 |

O que se perde, honestamente: o painel "Ao vivo" atualiza a cada
sincronização (≤30 min), não a cada segundo; webhooks são gravados mas
processados no próximo sync. Para voltar ao tempo real: criar Key Value +
worker no Render e preencher `REDIS_URL` (~US$ 14/mês) — nada muda no código.

## Passo 1 — Banco (Supabase)

Use a string do **Session pooler** (botão *Connect* no topo do projeto), com o
driver async:

    postgresql+asyncpg://postgres.<ref>:<senha>@aws-1-<regiao>.pooler.supabase.com:5432/postgres

Duas armadilhas conhecidas:
- O host direto (`db.<ref>.supabase.co`) **só publica IPv6** — do Render, a
  resolução falha antes de conectar. Sempre o pooler.
- O usuário do pooler é `postgres.<ref>`, não `postgres`.

## Passo 2 — Serviço (Render)

**New → Blueprint** → conectar este repositório → branch. O `render.yaml`
pergunta o que é segredo:

| Variável | O que colar |
|---|---|
| `DATABASE_URL` | a string do passo 1 |
| `MASTER_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — **tem de ser uma chave Fernet**, string qualquer é rejeitada no primeiro uso do cofre de tokens |
| `ADMIN_PASSWORD` | a senha do primeiro acesso do proprietário |

No primeiro boot o contêiner roda `alembic upgrade head` e o
`python -m app.bootstrap` — que cria **só** a organização e o proprietário
(`ADMIN_EMAIL`), sem nenhum dado de demonstração, e nunca reescreve a senha
de quem já existe. Com uma réplica única (free) não há migração concorrente,
que é a razão de o compose rodar migrations como job separado.

## Passo 3 — O "worker" (GitHub Actions)

`.github/workflows/sincronizar.yml` roda a cada 30 min. Em
**Settings → Secrets and variables → Actions** do repositório, crie:

| Segredo | Valor |
|---|---|
| `HUB_URL` | a URL do serviço no Render |
| `HUB_ADMIN_EMAIL` | o e-mail do proprietário |
| `HUB_ADMIN_PASSWORD` | a senha dele |

O workflow acorda o serviço (o free dorme), autentica, lista as contas
conectadas e dispara `sync` em cada uma. Sem conta conectada, encerra sem
erro. O botão *Run workflow* sincroniza na hora.

## Passo 4 — Anti-soneca (opcional, recomendado)

UptimeRobot (gratuito): monitor HTTP em `https://<serviço>/health` a cada
5 min. Mantém o serviço acordado dentro das 750h/mês do free (um serviço
24/7 usa ~730h) e ainda avisa por e-mail se cair.

## Passo 5 — Primeiro acesso

1. Entre com `ADMIN_EMAIL` / `ADMIN_PASSWORD` e **troque a senha** em
   Configurações.
2. Conecte as contas (Mercado Livre/Shopee) quando tiver as credenciais de
   aplicativo — até lá o painel funciona, vazio.
3. Rode o workflow manualmente após conectar, para o primeiro backfill.
