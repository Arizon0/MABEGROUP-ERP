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

**Cadastro de produtos internos** com custo unitário e custo de embalagem —
os dois compõem o CMV, congelado na data da venda. Criar, editar e excluir;
produto com venda registrada é **desativado em vez de apagado**, para que o
histórico de margem continue consultável. Alerta de produtos sem custo, porque
custo zero faz a margem daquele item aparecer maior do que é.

**De-para de SKUs** em duas listas: as pendências (SKU visto na importação sem
produto correspondente) e os vínculos já configurados, estes com a ação de
desfazer — que devolve o SKU às pendências sem alterar o custo já congelado nas
vendas antigas.

## Aba 6 — Custos e Lucro Real

A aba que existe porque **nenhum marketplace conhece três números que decidem se
o negócio dá lucro**: o custo do produto, o imposto do regime do vendedor e a
despesa fixa do mês. Até o líquido recebido, o painel repete o que o canal
informa; daqui para baixo é o que só o vendedor sabe.

**DRE gerencial até o lucro operacional**, linha a linha, com o percentual de
cada linha sobre a receita bruta:

```
    Receita bruta de vendas
(−) Cancelamentos
(−) Devoluções e reembolsos
(=) Receita líquida de vendas
(+) Frete cobrado do comprador
(−) Comissão do marketplace
(−) Taxa de meio de pagamento
(−) Custo de frete
(−) Imposto retido pelo canal
(+) Descontos e bônus do canal
(±) Ajustes não discriminados pelo canal
(=) Líquido recebido dos canais        ← até aqui, o canal informa
(−) Imposto sobre vendas               ← regime do vendedor
(−) CMV (produto + embalagem)          ← custo congelado na venda
(=) Margem de contribuição
(−) Despesas operacionais
(=) Lucro operacional                  ← o lucro real
```

**A coluna fecha no total.** O líquido é importado do canal, nunca recalculado
a partir das taxas — recalculá-lo faria o painel divergir do extrato. Só que a
soma das taxas discriminadas quase nunca bate exatamente com ele: parte do custo
de frete é faturada à parte em vez de descontada do repasse, e nem todo ajuste
vem detalhado por pedido. A linha **(±) Ajustes não discriminados** absorve essa
diferença explicitamente, de modo que somar a coluna à mão dê o total impresso.
O tamanho dessa linha é, ele próprio, um diagnóstico: valor alto significa
detalhamento de taxas incompleto, e o lugar de investigar é a aba de
conciliação.

**Imposto retido ≠ imposto sobre vendas.** O primeiro o canal já desconta antes
de repassar, e por isso está embutido no líquido. O segundo chega cheio na conta
e é recolhido depois — somá-lo ao líquido contaria o tributo duas vezes. São
duas linhas distintas de propósito.

**Regras tributárias com vigência.** A alíquota tem `valid_from`/`valid_to`, e o
imposto de um pedido é calculado pela regra vigente **na data da venda**, não
pela de hoje. Sem isso, subir de faixa no Simples reescreveria retroativamente o
lucro de todos os meses anteriores. Base de cálculo configurável (receita bruta,
bruta + frete, ou líquida) e regra opcional por canal.

**Despesas operacionais por competência.** Aluguel, pró-labore, contador e
software entram no mês a que se referem, não no mês em que foram pagos, e ficam
**fora do pedido** de propósito: ratear despesa fixa por venda produziria um
"custo por pedido" que muda conforme o volume do mês, o que não ajuda ninguém a
decidir preço. Despesas marcadas como recorrentes podem ser replicadas para o
mês seguinte com um clique.

**Indicadores:** margem de contribuição (R$ e %), lucro operacional (R$ e %),
lucro por pedido, ticket médio, carga tributária efetiva, taxa efetiva do canal
e **ponto de equilíbrio** — a receita bruta necessária para cobrir todos os
custos do período.

**Sinalização de dado incompleto.** Quando falta custo de produto ou regra
tributária vigente, o DRE marca `qualidade.confiavel = false` e exibe o que
falta, em vez de apresentar um lucro incompleto como se fosse final. Um DRE com
custo faltando mostra lucro maior que o real — e é exatamente o tipo de número
em que alguém baseia uma decisão de preço.

## Aba 7 — Logística

Distribuição por status de envio e por canal logístico (Full, Flex, Correios,
Shopee Xpress). **Envios em atraso** = `now() > estimated_delivery AND status ≠
delivered`, ordenados por dias de atraso. Prazo médio real por canal e por estado.
Ocorrências logísticas (extraviado, devolvido, recusado). Mapa do Brasil por estado
com volume e prazo médio. Rastreio consultável direto na tela.

## Aba 8 — Atendimento e Reputação

Perguntas não respondidas com cronômetro desde a chegada (tempo de resposta é fator
de ranqueamento no ML). Mensagens pós-venda. Reclamações por estágio, motivo e
valor envolvido. Avaliações com distribuição de notas e leitura dos comentários
negativos. Indicadores: tempo médio de primeira resposta, % respondidas em < 1 h,
taxa de reclamação, evolução da reputação (a partir dos `metrics_snapshots`
diários — o histórico que os marketplaces não fornecem).

## Aba 9 — Marketing e Campanhas

Campanhas ativas e encerradas por canal, com período, tipo e itens participantes.
Desconto concedido em R$ e %. **Rentabilidade por campanha**: receita gerada,
desconto, taxas, custo de mídia e margem resultante — respondendo a pergunta que o seller realmente faz, que é "essa
promoção deu lucro?". Comparativo de vendas dentro e fora de campanha para o mesmo
SKU, e ROAS quando houver dados de mídia.

O custo de mídia é **lançável manualmente por campanha**: a Ads API da Shopee
exige whitelist adicional e o Mercado Livre não expõe custo de mídia consolidado
por campanha. Sem o lançamento manual, o cálculo de retorno simplesmente não
existiria enquanto a liberação não sai.

## Aba 10 — Relatórios e Análises

Série temporal com granularidade selecionável (hora/dia/semana/mês). Comparação de
períodos sobrepostos (mês atual × anterior × mesmo mês do ano passado). Curva de
crescimento com média móvel de 7 dias. Rankings de produtos, SKUs, categorias,
marketplaces e estados. Margem estimada por produto. Análise de coorte por mês de
primeira venda. **Exportação em CSV, XLSX e PDF**, com jobs assíncronos para
volumes grandes.

## Aba 11 — Configurações

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
