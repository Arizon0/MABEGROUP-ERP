# 11 — Plano de Evolução em Fases

## Fase 0 — Fundação ✅ (entregue neste repositório)

Núcleo multi-tenant, modelo de dados completo, conectores dos três marketplaces com
OAuth/HMAC, cofre de tokens cifrado, ingestão de webhooks idempotente, fila e jobs,
painel ao vivo por SSE, motor financeiro em `Decimal`, conciliação, API REST
documentada, painel com as 10 abas, conectores mock, testes e infraestrutura de
deploy.

**Critério de pronto:** `docker compose up` sobe o sistema com dados simulados e o
painel funciona ponta a ponta sem nenhuma credencial real.

## Fase 1 — Produção com clientes reais (4–6 semanas)

| Entrega | Detalhe |
|---|---|
| Homologação Shopee | Submeter o app, ajustar ao feedback, sair do `test-stable` |
| Conexão real ML/MP | Validar com conta de produção, medir latência real de webhook |
| Backfill de 24 meses | Job em lote validado com volume real |
| Billing API do ML | Reconciliação com o número oficial de cobrança |
| Release report do MP | Conciliação bancária automática |
| Exportações | CSV/XLSX/PDF assíncronos com link temporário |
| Alertas | E-mail + webhook para as regras configuráveis |
| Onboarding | Fluxo guiado de conexão de conta com progresso de backfill |

## Fase 2 — Inteligência operacional (6–10 semanas)

- **Previsão de demanda** por SKU (média móvel + sazonalidade semanal) e sugestão
  de reposição considerando lead time do fornecedor.
- **Alerta de ruptura preditivo**: "SKU 5338 acaba em 6 dias no Full".
- **Simulador de precificação**: preço-alvo por margem desejada, já com comissão,
  frete e taxa de parcelamento do canal.
- **Detecção de anomalia**: queda de vendas, pico de cancelamento, taxa fora do
  padrão — comparando contra a própria linha de base do seller.
- **Análise de recompra** por `buyer_hash` (sem dado pessoal).
- **Consolidação de SKU entre canais**: o painel unificado por SKU base que nenhum
  marketplace oferece.

## Fase 3 — Ações de volta ao marketplace (8–12 semanas)

Sai da leitura e entra na escrita — exige escopo `write` e cuidado redobrado.

- Atualização de preço e estoque em massa, multicanal, com simulação prévia.
- Resposta a perguntas e mensagens direto do painel, com modelos salvos.
- Despacho e geração de etiqueta.
- Gestão de campanhas e cupons.
- **Regras de automação**: "se estoque < 5, pausar anúncio"; "se pergunta sem
  resposta há 2 h, notificar no WhatsApp".
- Fila de aprovação para ações em massa (evita alterar 3.000 preços por engano).

## Fase 4 — Plataforma (12+ semanas)

- **Novos canais**: Amazon (SP-API), Magalu, Americanas, TikTok Shop, Shopify,
  Nuvemshop. O modelo canônico já foi desenhado para isso — cada canal novo é um
  pacote de conector, sem tocar em domínio nem interface.
- **API pública** do próprio Marketplace Hub, com chaves por tenant e webhooks
  de saída.
- **Integração contábil**: exportação para Omie, Bling, Conta Azul; SPED.
- **Emissão de NF-e** via provedor (Focus NFe, PlugNotas).
- **App mobile** (React Native) para o painel ao vivo e alertas.
- **Marketplace de integrações** e white-label para contadores e agências.

## Modelo de negócio sugerido

| Plano | Preço/mês | Limites |
|---|---|---|
| Starter | R$ 97 | 1 conta, 1.000 pedidos/mês, 3 usuários |
| Pro | R$ 297 | 3 contas, 10.000 pedidos, 10 usuários, alertas, exportação |
| Business | R$ 697 | 10 contas, 50.000 pedidos, ilimitado de usuários, API |
| Enterprise | sob consulta | Ilimitado, SLA, white-label, suporte dedicado |

Custo de infraestrutura por seller no plano Pro fica na casa de US$ 2–4/mês — margem
bruta acima de 90%, típica de SaaS de dados.

## Métricas do produto a acompanhar

**Ativação:** % que conecta ao menos uma conta em 24 h · tempo até o primeiro
dashboard útil.
**Engajamento:** sessões/semana · abas mais usadas · uso do painel ao vivo.
**Retenção:** churn mensal · NPS.
**Técnicas:** latência p95 do painel · sync lag · taxa de divergência de
conciliação · uptime.
