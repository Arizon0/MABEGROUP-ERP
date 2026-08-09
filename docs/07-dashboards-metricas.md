# 07 — Painel: Abas, Métricas e Estratégia de Agregação

## 7.1 Estratégia de agregação (por que o painel é rápido)

Três camadas de leitura, escolhidas por janela de tempo:

| Janela | Fonte | Latência alvo |
|---|---|---|
| Últimas 24 h / "ao vivo" | Query direta em `orders` com índice parcial `WHERE date_created > now() - 7d` | < 80 ms |
| 7 a 90 dias | `metrics_hourly` (rollup) | < 120 ms |
| Acima de 90 dias | `metrics_daily` (rollup) | < 200 ms |

O rollup roda no worker a cada 5 minutos, recalculando apenas os *buckets*
afetados (as últimas 3 horas + qualquer bucket tocado por pedido alterado). Nunca
recalcula o histórico inteiro.

Cache Redis de 30 s nas respostas de agregação, com chave
`{tenant}:{aba}:{filtros_hash}`, invalidado por evento de escrita. Trinta segundos
é o ponto de equilíbrio: absorve o F5 nervoso sem fazer o número parecer travado.

## 7.2 Filtros globais (persistentes em todas as abas)

`período` (presets + custom) · `marketplace` · `conta/loja` · `status` ·
`canal logístico` · `produto/SKU` · `campanha` · `estado/cidade`

Os filtros vivem na URL (`?de=2026-07-01&ate=2026-07-31&canal=mercadolivre`), o
que torna qualquer visão compartilhável por link e preserva o estado no F5.

---

## Aba 1 — Visão Geral

**Linha de KPIs** (cada um com comparação vs. período anterior e sparkline de 14 dias):

| KPI | Fórmula |
|---|---|
| Total de vendas | `COUNT(orders WHERE status ∉ cancelled)` |
| Unidades vendidas | `Σ order_items.quantity` |
| Valor bruto | `Σ order_items.unit_price × quantity` |
| Valor líquido | `Σ orders.net_amount` (com selo de procedência) |
| Taxas descontadas | `Σ platform_fee + payment_fee` + **% sobre o bruto** |
| Ticket médio | `bruto ÷ nº de pedidos` |
| Pedidos novos / em processamento / enviados / cancelados | contagem por status canônico |
| Taxa de cancelamento | `cancelados ÷ total` |
| **Conversão operacional** | `pedidos ÷ visitas` (ML `items_visits` + Shopee `get_item_extra_info`) |

**Gráficos:** receita bruta × líquida por dia (linha dupla — o "gap" visual entre
elas é a percepção mais útil do painel: mostra o peso das taxas); pizza de
participação por marketplace; funil operacional (pago → processando → enviado →
entregue) com taxa de queda entre etapas; top 10 produtos por receita.

## Aba 2 — Painel Ao Vivo

- **Feed de eventos** em ordem cronológica reversa, com badge colorido por
  marketplace, valor, SKU e status. Entrada com animação sutil de destaque.
- **Contadores da sessão**: pedidos e receita desde a abertura da tela, com
  indicador "AO VIVO" pulsante enquanto o SSE está conectado (e aviso claro de
  reconexão quando cai — silêncio ambíguo é pior que erro visível).
- **Volume por minuto** — barras dos últimos 60 minutos, atualizadas a cada evento.
- **Volume por hora** — últimas 24 h, comparado com a média do mesmo horário nos
  últimos 7 dias (revela se hoje está acima ou abaixo do normal).
- **Notificação** desktop opcional + som configurável em novo pedido.
- **Mapa de calor semanal** hora × dia da semana — orienta horário de campanha.

## Aba 3 — Faturamento e Conciliação

**Cascata (waterfall)** — a visualização central da aba:

```
Bruto ─── +Frete cobrado ─── −Comissão ─── −Taxa pgto ─── −Frete pago
      ─── −Impostos ─── +Descontos ─── −Reembolsos ─── = Líquido
```

Tabela por canal e por dia com todas as colunas da fórmula. **Fila de
divergências** ordenada por valor absoluto, cada linha expansível mostrando o
diagnóstico automático (ver doc 06). **Calendário de recebíveis** com o previsto
por dia. **Composição de taxas** por tipo, com evolução mensal — é onde o seller
descobre que a taxa de parcelamento cresceu 3 pontos sem aviso.

## Aba 4 — Pedidos

Tabela virtualizada (renderiza só o visível — suporta centenas de milhares de
linhas), busca por ID/SKU/comprador, filtros combináveis, seleção múltipla e
exportação da seleção. O painel de detalhe abre lateralmente com: itens, frete,
pagamento com breakdown de taxas, endereço (na granularidade que a API permite),
**timeline unificada** de `order_events` + `shipment_events` + mensagens e
reclamações, e o **payload cru** para auditoria.

## Aba 5 — Produtos e Estoque

Grade de anúncios com miniatura, canal, preço, estoque, vendas no período, receita
e status de saúde. Alertas destacados: **ruptura** (estoque 0 com venda nos últimos
30 dias) e **estoque crítico** (cobertura < 7 dias pelo giro atual).

Classificação automática: **giro alto**, **giro médio**, **parados** (sem venda em
60 dias) e **encalhados** (sem venda em 90 dias com estoque). Curva ABC por receita.
Visão consolidada por SKU base — o mesmo produto vendido em 4 anúncios de 2 canais
aparece como uma linha só, que é justamente o que o painel nativo de cada
marketplace não consegue mostrar.

## Aba 6 — Logística

Distribuição por status de envio e por canal logístico (Full, Flex, Correios,
Shopee Xpress). **Envios em atraso** = `now() > estimated_delivery AND status ≠
delivered`, ordenados por dias de atraso. Prazo médio real por canal e por estado.
Ocorrências logísticas (extraviado, devolvido, recusado). Mapa do Brasil por estado
com volume e prazo médio. Rastreio consultável direto na tela.

## Aba 7 — Atendimento e Reputação

Perguntas não respondidas com cronômetro desde a chegada (tempo de resposta é fator
de ranqueamento no ML). Mensagens pós-venda. Reclamações por estágio, motivo e
valor envolvido. Avaliações com distribuição de notas e leitura dos comentários
negativos. Indicadores: tempo médio de primeira resposta, % respondidas em < 1 h,
taxa de reclamação, evolução da reputação (a partir dos `metrics_snapshots`
diários — o histórico que os marketplaces não fornecem).

## Aba 8 — Marketing e Campanhas

Campanhas ativas e encerradas por canal, com período, tipo e itens participantes.
Desconto concedido em R$ e %. **Rentabilidade por campanha**: receita gerada,
desconto, taxas, custo de mídia (quando a Ads API estiver liberada) e margem
resultante — respondendo a pergunta que o seller realmente faz, que é "essa
promoção deu lucro?". Comparativo de vendas dentro e fora de campanha para o mesmo
SKU, e ROAS quando houver dados de mídia.

## Aba 9 — Relatórios e Análises

Série temporal com granularidade selecionável (hora/dia/semana/mês). Comparação de
períodos sobrepostos (mês atual × anterior × mesmo mês do ano passado). Curva de
crescimento com média móvel de 7 dias. Rankings de produtos, SKUs, categorias,
marketplaces e estados. Margem estimada por produto. Análise de coorte por mês de
primeira venda. **Exportação em CSV, XLSX e PDF**, com jobs assíncronos para
volumes grandes.

## Aba 10 — Configurações

Conexão de contas (botão "Conectar" por marketplace, status de cada uma, última
sincronização, botão de revogar). Gestão de tokens: validade, próximo refresh,
refresh manual. Usuários e papéis (RBAC de 4 níveis). Regras de alerta
configuráveis (ruptura, divergência acima de X, queda de vendas, pergunta sem
resposta há N horas) com destino por e-mail/webhook. **Monitor de integração**:
últimos webhooks recebidos, fila, falhas e botão de reprocessar. Log de auditoria
pesquisável. Preferências de sincronização (frequência por recurso).

---

## 7.3 Métricas derivadas — definições formais

Publicadas na interface junto do número, porque métrica sem definição vira
discussão:

| Métrica | Definição |
|---|---|
| Ticket médio | `bruto ÷ pedidos válidos` (exclui cancelados) |
| Conversão | `pedidos ÷ visitas ao anúncio` |
| Taxa efetiva | `(comissão + taxa pgto) ÷ bruto` |
| Cobertura de estoque | `estoque ÷ média diária de venda (30d)` |
| Giro | `unidades vendidas (30d) ÷ estoque médio` |
| Prazo real | `date_delivered − date_shipped` |
| Atraso | `date_delivered − estimated_delivery`, quando positivo |
| Margem de contribuição | `líquido − CMV − embalagem − mídia rateada` |
| Reincidência de reclamação | `pedidos com claim ÷ total`, por SKU |
