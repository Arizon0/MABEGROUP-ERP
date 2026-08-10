# Marketplace Hub

Plataforma SaaS multicanal para consolidação e análise de vendas de **Mercado
Livre**, **Mercado Pago** e **Shopee**, usando exclusivamente as **APIs oficiais**
de cada plataforma.

Responde, num painel só, a pergunta que nenhum marketplace responde sozinho:
*quanto eu realmente vendi, quanto realmente recebi, e onde foi a diferença?*

```
┌──────────────────────────────────────────────────────────────────────┐
│  Bruto R$ 62.580  →  −taxas R$ 11.540  →  −frete R$ 9.750            │
│                     →  Líquido R$ 45.904  (taxa efetiva 18,4%)       │
│                     →  −CMV  →  Margem R$ 37.000 (59,1%)             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Começar em dois minutos, sem nenhuma credencial

Homologação da Shopee leva semanas e credenciais do Mercado Livre exigem conta
de vendedor ativa. Nada disso pode ser pré-requisito para ver o produto
funcionando — por isso existem os **conectores simulados**, que geram pedidos,
pagamentos e envios realistas sem uma única chamada de rede.

```bash
git clone https://github.com/Arizon0/MABEGROUP-ERP.git
cd MABEGROUP-ERP
docker compose up --build
```

| | |
|---|---|
| Painel | <http://localhost:5173> |
| API + documentação | <http://localhost:8000/docs> |
| Acesso | `admin@marketplacehub.com.br` / `admin123` |

Na aba **Configurações**, use *Sincronizar* em qualquer conta para popular o
painel com dados simulados.

### Sem Docker

```bash
make instalar     # cria a venv do backend e instala o frontend
make api          # API em http://localhost:8000
make worker       # (outro terminal) sincronização e jobs agendados
make frontend     # (outro terminal) painel em http://localhost:5173
make testes       # suíte completa: 107 testes de backend + 13 de frontend
```

---

## O que o sistema faz

| Aba | Entrega |
|---|---|
| **Visão geral** | Faturamento bruto e líquido, taxas, ticket médio, conversão, funil de status, comparação com o período anterior |
| **Ao vivo** | Feed de eventos por SSE, volume por minuto, contadores do dia, notificação de novo pedido |
| **Faturamento** | Cascata do bruto ao líquido, composição de taxas, conciliação em 3 níveis, fluxo de caixa projetado |
| **Pedidos** | Lista filtrável, detalhe com itens, frete, taxas e **linha do tempo unificada** (pedido + envio + mensagens + reclamações) |
| **Produtos** | Anúncios de todos os canais, ruptura, cobertura de estoque, giro, de-para de SKU |
| **Logística** | Envios por status e canal, atrasos, prazo real por estado |
| **Atendimento** | Perguntas pendentes com cronômetro, reclamações, avaliações, evolução da reputação |
| **Marketing** | Campanhas com receita gerada, custo de mídia e resultado |
| **Relatórios** | Séries, rankings, geografia, exportação CSV/Excel |
| **Configurações** | Conexão de contas, tokens, monitor de integração, DLQ de webhooks, auditoria |

---

## Arquitetura em uma imagem

```
Navegador ──REST──▶ ┌─────────────┐ ◀──pub/sub── ┌──────────────┐
          ◀──SSE─── │ API FastAPI │              │ Workers ARQ  │
                    └──────┬──────┘              └───────┬──────┘
                           │                             │
                    ┌──────▼──────┐  ┌────────┐          │ HTTPS
                    │ PostgreSQL  │  │ Redis  │          │
                    └─────────────┘  └────────┘          ▼
                                            ┌───────────────────────────┐
        webhooks ──────────────────────────▶│ ML · Mercado Pago · Shopee│
                                            └───────────────────────────┘
```

**Princípio central — o modelo canônico.** Tudo que vem de um marketplace é
traduzido para DTOs neutros na camada de conectores, *antes* de tocar em
qualquer regra de negócio. Nenhum serviço, endpoint ou tela sabe que o Mercado
Livre chama a comissão de `sale_fee` e a Shopee de `commission_fee`.
Consequência prática: adicionar Amazon ou Magalu é escrever um pacote novo em
`connectors/`, sem tocar em domínio, API ou frontend.

### Stack

| Camada | Escolha | Por quê |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Async nativo — a carga é I/O contra 3 APIs externas; OpenAPI automático |
| ORM | SQLAlchemy 2.0 async + Alembic | Tipagem real com `Mapped[]`, migrations reversíveis |
| Banco | PostgreSQL 15+ | Integridade referencial em dado financeiro; `JSONB` para o payload cru |
| Fila | ARQ sobre Redis | Asyncio-nativo (mesmo loop do FastAPI), cron embutido, sem ponte sync/async |
| Tempo real | SSE | Tráfego é unidirecional; reconecta sozinho, passa em qualquer proxy |
| Frontend | React 18 + TS + Vite + Tailwind | Tipagem ponta a ponta, build instantâneo |
| Dados/gráficos | TanStack Query + Recharts | Cache e revalidação por biblioteca; gráficos declarativos |

---

## Decisões que mudam o resultado

Estas não são preferências de estilo — cada uma corrige um erro concreto que
apareceu durante o desenvolvimento ou que quebraria a integração em produção.

**Todo valor líquido carrega procedência.** `settled` (dinheiro em conta),
`api_reported` (o canal informou mas não liberou) ou `computed` (estimado pelo
sistema). Somar os três num indicador único, sem distinguir, faz o painel
divergir do extrato do vendedor — e o selo aparece sempre ao lado do número.

**O refresh token do Mercado Livre é de uso único.** Duas renovações
simultâneas desconectam a conta e exigem reautorização manual. Sob carga isso
acontece em minutos, por isso a renovação é serializada com lock distribuído no
Redis e relê a credencial depois de adquirir o lock.

**O endpoint de webhook só persiste e enfileira.** O Mercado Livre exige
resposta em 500 ms e suspende aplicações lentas — para *todos* os vendedores
conectados, não só um. Nenhuma chamada externa acontece no caminho da resposta.

**Webhook e polling são redundantes de propósito.** Webhook dá latência baixa;
polling garante completude. Ambos convergem no mesmo UPSERT idempotente, então
processar o mesmo pedido duas vezes é inofensivo.

**Na Shopee, o escrow *é* o pagamento.** Buscar pagamento e escrow e somá-los
contaria o mesmo dinheiro duas vezes — foi exatamente o bug que fez o líquido
aparecer maior que o bruto durante o desenvolvimento.

**O custo do produto é congelado na venda.** Alterar o custo hoje não pode
reescrever a margem de um pedido de seis meses atrás, senão nenhum fechamento
histórico é reproduzível.

**Pendência de SKU nunca bloqueia a importação.** O dinheiro entra mesmo sem o
de-para; o que fica indisponível é a margem daquele item — sinalizada na
interface, em vez de virar um custo zero silencioso que exibiria 100% de margem.

**Dinheiro em `Decimal`, nunca `float`.** E arredondamento só na apresentação:
arredondar no meio da cadeia produz a divergência de centavos que ninguém
consegue explicar depois.

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [01 — Arquitetura](docs/01-arquitetura.md) | Componentes, fluxo ponta a ponta, stack justificada, infraestrutura gerenciada real com custos |
| [02 — Modelo de dados](docs/02-modelo-de-dados.md) | 39 tabelas, índices, particionamento, retenção, rastreabilidade |
| [03 — Autenticação](docs/03-autenticacao-marketplaces.md) | OAuth+PKCE do ML, OAuth do MP, assinatura HMAC da Shopee, armadilhas de cada um |
| [04 — APIs oficiais](docs/04-mapa-de-endpoints-apis.md) | Levantamento completo dos módulos, o que cada um retorna e para que serve |
| [05 — Tempo real](docs/05-tempo-real.md) | Webhooks + polling + reconciliação, latências reais, orçamento de rate limit |
| [06 — Financeiro](docs/06-financeiro-conciliacao.md) | Fórmulas de bruto e líquido por canal, conciliação em 3 níveis, diagnóstico de divergência |
| [07 — Painel](docs/07-dashboards-metricas.md) | As 10 abas em detalhe e a definição formal de cada métrica |
| [08 — Segurança](docs/08-seguranca.md) | Modelo de ameaças, cofre de tokens, isolamento multi-tenant, LGPD |
| [09 — Deploy](docs/09-deploy.md) | Topologia, CI/CD, observabilidade, backup, custos por estágio |
| [10 — Riscos](docs/10-riscos-limitacoes.md) | Limitações reais de cada API e a alternativa adotada |
| [11 — Roadmap](docs/11-roadmap.md) | Evolução em fases e modelo de negócio |
| [12 — Análise de dados](docs/12-analise-de-dados.md) | O que a API entrega pronto × o que o sistema calcula |
| [13 — Instalação](docs/13-instalacao.md) | Passo a passo local e de produção |
| [14 — Conectar APIs reais](docs/14-conectar-apis-reais.md) | **Do modo simulado às contas reais**, portal por portal |

---

## Estrutura

```
backend/
├── app/
│   ├── api/v1/      Borda HTTP — sem SQL, sem regra de negócio
│   ├── services/    Regra de negócio — sem conhecimento de HTTP
│   ├── connectors/  Camada anticorrupção (um pacote por marketplace)
│   ├── models/      SQLAlchemy — estrutura, índices, constraints
│   ├── events/      Barramento do painel ao vivo
│   ├── workers/     Tarefas e cron do ARQ
│   ├── core/        Config, segurança, cripto, erros, log
│   └── db/          Engine, sessão, tipos
├── alembic/         Migrations versionadas
└── tests/           107 testes
frontend/src/
├── pages/           As 10 abas
├── components/      Interface e gráficos
├── api/             Cliente HTTP tipado + hooks de dados
└── hooks/           SSE do painel ao vivo
```

As setas de dependência apontam sempre para dentro: `api → services → models`.
`connectors` só é chamado por `services` e `workers`.

---

## Conectar contas reais

Passo a passo completo em **[docs/14-conectar-apis-reais.md](docs/14-conectar-apis-reais.md)**.
Resumo:

1. Suba um túnel público (`cloudflared tunnel --url http://localhost:8000`) — os
   marketplaces não alcançam `localhost` para entregar as notificações.
2. Crie as aplicações nos portais de desenvolvedor, usando a URL do túnel nos
   campos de redirect e de webhook.
3. Preencha `.env` e defina `USE_MOCK_CONNECTORS=0`.
4. `docker compose down -v && docker compose up --build` — o `-v` apaga os dados
   simulados, que contaminariam os relatórios se misturados ao histórico real.
5. No painel, **Configurações → Conectar**. A autorização acontece no site do
   próprio marketplace; ao voltar, o backfill de 90 dias começa sozinho.

> **Nota de conformidade:** o sistema usa exclusivamente APIs oficiais
> autenticadas. Não há scraping, automação de navegador nem uso de endpoint
> interno. Isso é decisão de arquitetura, não só ética: integração não-oficial
> quebra sem aviso e coloca a conta do vendedor em risco de banimento.

---

## Licença

Proprietário — MABE Group.
