# 10 — Riscos e Limitações das APIs Oficiais

Documentar limitação **antes** de construir evita promessa que a interface não pode
cumprir. Tudo aqui é limitação real das APIs oficiais, com a alternativa técnica
adotada.

## 10.1 Limitações do Mercado Livre

### 🔴 Refresh token de uso único (risco de desconexão)
Cada refresh invalida o anterior. Dois workers renovando ao mesmo tempo derrubam a
conta e o seller precisa reautorizar manualmente.
**Mitigação:** lock distribuído no Redis, refresh proativo aos 80% da vida do
token, gravação na mesma transação, alerta crítico em qualquer falha.

### 🔴 SLA de 500 ms no webhook
Resposta lenta conta como falha; falhas recorrentes suspendem as notificações da
aplicação inteira — para todos os sellers.
**Mitigação:** o endpoint só persiste e enfileira (~5 ms). Zero I/O externo.

### 🟠 `offset` máximo de 1.000 em `/orders/search`
Um loop de paginação ingênuo para em 1.000 pedidos **sem erro**. Silencioso.
**Mitigação:** janela deslizante por data (30 dias por consulta) com `sort=date_asc`
e cursor persistido, em vez de offset.

### 🟠 Dados do comprador restritos (LGPD)
E-mail, telefone e documento não são expostos. Endereço completo só em Flex.
**Mitigação:** análise geográfica por cidade/estado (disponível em todos os tipos);
identidade pseudonimizada em `buyer_hash` para métrica de recompra.

### 🟠 API de mensagens só pós-venda
Exige `pack_id`; não há acesso a chat pré-venda. Há moderação automática e janela
de resposta.
**Mitigação:** cobrimos perguntas (pré-venda) + mensagens (pós-venda), que juntas
representam o fluxo de atendimento real.

### 🟡 Rate limit não publicado formalmente
O ML aplica limites por app e por usuário sem documentar os números exatos.
**Mitigação:** token bucket conservador por conta, respeito a `Retry-After`,
multiget de 20 itens (reduz 20× o consumo), circuit breaker.

### 🟡 Billing API com defasagem
O detalhe oficial de cobrança fecha por período; não há taxa oficial em tempo real.
**Mitigação:** `sale_fee` por pedido dá a visão gerencial imediata; o Billing
reconcilia depois e corrige. Dois números, procedências distintas, ambos exibidos.

## 10.2 Limitações do Mercado Pago

### 🟠 Relatórios financeiros são assíncronos
`POST` para solicitar → polling → `GET` do arquivo. Não existe versão síncrona.
**Mitigação:** job de duas fases com estado persistido; a interface mostra
"processando" com previsão, em vez de travar.

### 🟠 `money_release_date` pode mudar
Retenção por análise de risco, reclamação ou antecipação altera a data prometida.
**Mitigação:** re-sincronização de pagamentos não liberados; a projeção de fluxo de
caixa é rotulada como previsão, e a mudança de data gera evento na timeline.

### 🟡 Vínculo ML ↔ MP nem sempre é 1:1
Contas antigas, múltiplas contas MP ou recebimento por fora do ML quebram a
associação automática.
**Mitigação:** casamento por `payment.order_id`/`external_reference` e, quando
falha, fila de conciliação manual na interface — em vez de adivinhar.

### 🟡 Chargeback com prazo longo
Pode aparecer meses depois da venda e reabrir um período já fechado.
**Mitigação:** chargeback lança na data do evento, não retroage o fechamento, e
dispara alerta.

## 10.3 Limitações da Shopee

### 🔴 Janela de 15 dias em `get_order_list`
Backfill de 2 anos = ~49 chamadas sequenciais por loja.
**Mitigação:** job em lote com cursor persistido e retomada; a interface mostra
progresso real do backfill.

### 🔴 `escrow_amount` só após conclusão do pedido
O líquido real só existe depois que o comprador confirma o recebimento (7–15 dias).
**Mitigação:** líquido estimado (`net_source='computed'`) a partir das taxas
conhecidas, sobrescrito por `settled` quando o escrow chega, com a divergência
medida e exposta como qualidade da estimativa.

### 🟠 Homologação obrigatória do app
Produção exige aprovação da Shopee; até lá, só `test-stable`.
**Mitigação:** conectores mock permitem desenvolver e demonstrar o produto
completo durante a homologação.

### 🟠 Ads API exige whitelist separada
Custo de mídia não vem na autorização padrão.
**Mitigação:** lançamento manual de custo de campanha na aba de Marketing; a
integração automática entra quando a whitelist for concedida.

### 🟠 Livestream API com disponibilidade regional
Nem sempre habilitada para o Brasil.
**Mitigação:** detecção de indisponibilidade e ocultação do módulo, em vez de erro
recorrente na interface.

### 🟡 Tolerância de ±5 min no timestamp
Relógio dessincronizado = 100% de falha de autenticação, com mensagem pouco clara.
**Mitigação:** NTP no host + verificação de *drift* no `/health` + mensagem de erro
específica que aponta a causa.

### 🟡 `optional_fields` mutila o payload por omissão
Sem pedir explicitamente, `get_order_detail` retorna um objeto que **parece**
completo mas não tem itens nem endereço.
**Mitigação:** lista de campos centralizada e explícita no conector, coberta por
teste de contrato.

## 10.4 Riscos transversais

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Mudança de contrato sem aviso | Alta | Alto | Versionamento de conector, payload cru preservado, teste de contrato, alerta em campo inesperado |
| Indisponibilidade do marketplace | Média | Médio | Fila persistente, retry, circuit breaker, banner de status na interface |
| Suspensão da aplicação | Baixa | **Crítico** | Cumprir SLA de webhook, respeitar rate limit, seguir termos de uso |
| Seller revoga autorização | Média | Médio | Detecção via webhook de desautorização, dados históricos preservados |
| Crescimento do volume de dados | Alta | Médio | Particionamento, rollups, arquivamento, réplica de leitura |
| Divergência financeira sistemática | Média | Alto | Conciliação diária + alerta acima de limiar |
| Dependência de um único fornecedor | Baixa | Médio | Postgres e Redis padrão — migração entre provedores sem reescrita |

## 10.5 O que **não** é possível pelas APIs oficiais

Explicitado para não gerar expectativa falsa:

| Não disponível | Por quê | Alternativa |
|---|---|---|
| Dados de concorrentes por seller | Não exposto | Preços e mais vendidos públicos por categoria |
| Custo de aquisição do produto | Não é dado do marketplace | Cadastro interno de custo (implementado) |
| Métricas de tráfego pré-clique | Não exposto | Visitas ao anúncio (`items_visits`) |
| Chat pré-venda no ML | Só perguntas públicas | Módulo de perguntas |
| Endereço completo em Full/Correios | Restrição LGPD | Cidade/estado |
| Alteração de comissão | Definida pelo marketplace | Simulador com `listing_prices` |
| Histórico de reputação | Só o estado atual | `metrics_snapshots` diários (construímos o histórico) |
| Ads da Shopee sem whitelist | Restrição da plataforma | Lançamento manual |

> **Nota de conformidade:** o sistema usa **exclusivamente** APIs oficiais
> autenticadas. Não há scraping, automação de navegador, engenharia reversa de
> endpoint interno ou uso de credencial fora do fluxo oficial de autorização.
> Isso é decisão de arquitetura, não só ética: integração não-oficial quebra sem
> aviso, viola os termos de uso e coloca a conta do seller em risco de banimento.
