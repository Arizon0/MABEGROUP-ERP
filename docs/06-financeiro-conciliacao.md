# 06 — Faturamento Bruto, Líquido e Conciliação

## 6.1 A regra de ouro: procedência antes de precisão

Todo valor líquido no sistema carrega o campo `net_source`, com três níveis:

| `net_source` | Significado | Confiabilidade |
|---|---|---|
| `settled` | Confirmado por repasse/escrow — **o dinheiro caiu** | 100% — valor contábil |
| `api_reported` | O canal informou o líquido (`net_received_amount`) mas ainda não liberou | ~99% |
| `computed` | O sistema calculou a partir das taxas conhecidas | ~95% — estimativa gerencial |

Um dashboard que mistura os três sem distinguir mente para o usuário. A interface
sempre mostra o selo de procedência ao lado do número, e o relatório contábil
filtra apenas `settled`.

## 6.2 Faturamento bruto

Definição única, aplicada a todos os canais:

```
receita_bruta = Σ (order_items.unit_price × order_items.quantity)
                para pedidos com status ∉ {cancelled}
```

Decisões que evitam erro de dupla contagem:

- **Frete cobrado do comprador NÃO entra na receita bruta.** É repasse de custo
  logístico, não venda. Aparece em linha separada (`shipping_revenue`).
- **Pedidos cancelados são excluídos, não zerados.** Ficam no banco com
  `status='cancelled'` e alimentam a métrica de cancelamento, mas não somam receita.
- **Pacotes multi-item (`pack_id` do ML / carrinho Shopee)** são contados uma única
  vez. Este é o erro clássico: a exportação do ML repete o valor do pacote em cada
  linha-componente, inflando a receita. O sistema agrupa por `external_pack_id`.
- **Devoluções** reduzem a receita **na data da devolução**, não retroativamente —
  senão o fechamento de um mês já publicado muda sozinho.

## 6.3 Faturamento líquido — a fórmula canônica

```
líquido = receita_bruta
        + receita_de_frete          (o que o comprador pagou de frete)
        − comissão_do_marketplace   (sale_fee / commission_fee + service_fee)
        − taxa_de_pagamento         (mercadopago_fee, financing_fee, transaction_fee)
        − custo_de_frete            (o que o seller efetivamente pagou)
        − impostos_retidos
        + descontos_e_bônus         (subsídio do marketplace ao seller)
        − reembolsos_e_estornos
        − chargebacks
```

Todos os termos em `Decimal`, com `ROUND_HALF_UP` a 2 casas **apenas na
apresentação** — nunca em cálculo intermediário. Arredondar no meio da cadeia
produz o erro de centavos que ninguém consegue explicar depois.

### Por canal — de onde vem cada termo

#### Mercado Livre

| Termo | Origem | Prioridade |
|---|---|---|
| Bruto | `order_items[].unit_price × quantity` | — |
| Comissão | `order_items[].sale_fee` | 1ª |
| Comissão (verificação) | Billing API, detalhe do período | 2ª — número oficial |
| Frete (custo real) | `GET /shipments/{id}/costs` → `senders[].cost` | 1ª |
| Frete (receita) | `costs.receiver_cost` | |
| Taxa de pagamento | MP `fee_details[]` | |
| **Líquido** | MP `transaction_details.net_received_amount` | **fonte primária** |
| Confirmação | Release report do MP | → `settled` |

O ML entrega o líquido pronto via Mercado Pago. **Não recalcular quando ele
existe** — recalcular introduz divergência com o extrato do próprio seller.

#### Shopee

| Termo | Origem |
|---|---|
| Bruto | `original_price × quantity` (ou `total_amount` do pedido) |
| Comissão | `commission_fee` |
| Taxa de serviço | `service_fee` (programas Frete Grátis / Shopee Ads) |
| Taxa de transação | `transaction_fee` |
| Frete | `actual_shipping_fee` − `buyer_paid_shipping_fee` |
| Devolução | `reverse_shipping_fee` |
| **Líquido** | `escrow_amount` de `get_escrow_detail` |

**Problema real:** `escrow_amount` só existe **depois** que o pedido é concluído
(comprador confirma o recebimento — pode levar 7 a 15 dias). Antes disso, a Shopee
não informa líquido nenhum.

**Solução implementada:** líquido estimado com as taxas conhecidas do pedido,
marcado como `computed`. Quando o escrow chega, o valor é sobrescrito com o real e
`net_source` vira `settled`. A diferença entre estimativa e realizado é registrada
em `reconciliations.divergence` e vira métrica de qualidade da própria estimativa —
com o tempo, o percentual de erro do modelo fica visível e ajustável.

## 6.4 Conciliação em três níveis

```
NÍVEL 1 — Pedido ↔ Pagamento
  Cada order tem ao menos 1 payment aprovado?
  Σ payments.transaction_amount == order.gross_amount + shipping?
  ▸ Detecta: pedido pago sem registro, pagamento parcial, valor divergente.

NÍVEL 2 — Pagamento ↔ Repasse
  Cada payment aparece em algum settlement_entry?
  payment.net_received_amount == Σ entries do repasse?
  ▸ Detecta: dinheiro não liberado, retenção, taxa cobrada a mais.

NÍVEL 3 — Repasse ↔ Conta bancária
  settlement.net_amount bate com o extrato (release report / payout)?
  ▸ Detecta: repasse não creditado, ajuste retroativo, débito de reclamação.
```

### Estados de conciliação

| Status | Condição | Ação no painel |
|---|---|---|
| `matched` | Divergência ≤ R$ 0,01 | Verde, silencioso |
| `divergent` | Divergência > tolerância | **Amarelo/vermelho, entra na fila de exceções** |
| `pending_settlement` | Venda ok, repasse ainda não ocorreu | Cinza, previsão de data |
| `unmatched` | Pagamento sem pedido, ou repasse sem pagamento | Vermelho, investigação manual |

A tolerância padrão é R$ 0,01 (arredondamento legítimo), configurável por tenant.

### Causas frequentes de divergência (e o que o sistema diz ao usuário)

O valor de um conciliador não é só apontar diferença — é explicar a diferença.
A tabela `reconciliations.notes` recebe o diagnóstico automático:

| Causa | Assinatura detectável |
|---|---|
| Taxa de parcelamento não prevista | `financing_fee` presente e ausente da estimativa |
| Frete recalculado após despacho | `shipments.costs` mudou depois de `date_shipped` |
| Devolução parcial | `refunds.amount < payment.transaction_amount` |
| Débito de reclamação | Repasse menor com `claim` aberta no mesmo período |
| Ajuste retroativo do canal | `charges_details` com data posterior ao pedido |
| Antecipação de recebíveis | Taxa extra + `money_release_date` adiantada |
| Escrow Shopee ainda não liberado | `net_source='computed'` há mais de 20 dias |

## 6.5 Margem de contribuição

Faturamento líquido ainda não é lucro. A margem exige o custo do produto:

```
margem_contribuicao = líquido − CMV − custo_de_embalagem − custo_de_mídia_rateado
margem_%            = margem_contribuicao / receita_bruta
```

O **CMV é congelado na ingestão** (`order_items.unit_cost` e `cogs`), replicando
a regra do ERP existente: alterar o custo de um produto hoje não pode reescrever a
margem de um pedido de seis meses atrás. Sem esse congelamento, todo fechamento
histórico se torna irreprodutível.

Produtos sem custo cadastrado entram em `sku_pendencies` e a interface mostra a
margem como "indisponível" — explicitamente, em vez de exibir 100% de margem, que
é o que acontece quando se assume custo zero por omissão.

## 6.6 Fluxo de caixa projetado

Diferencial competitivo real, e barato de construir com os dados já ingeridos:

- Mercado Pago: `money_release_date` por pagamento → calendário exato.
- Shopee: `escrow_release_time` → calendário exato.
- Pedidos ainda não liberados: projeção pelo prazo médio histórico da conta.

Resultado: "nos próximos 30 dias entram R$ 47.320, sendo R$ 12.400 já liberados e
R$ 34.920 previstos" — informação que nenhum dos painéis nativos entrega de forma
consolidada entre canais.

## 6.7 Relatórios contábeis exportáveis

| Relatório | Conteúdo | Uso |
|---|---|---|
| Faturamento por período | Bruto, taxas, frete, líquido por canal/dia | Apuração |
| Extrato de repasses | Cada crédito com os pedidos que o compõem | Conciliação bancária |
| Detalhamento de taxas | Por tipo, canal, produto | Renegociação e análise de custo |
| Divergências | Lista de exceções com diagnóstico | Auditoria |
| DRE gerencial | Receita → CMV → taxas → despesas → resultado | Gestão |
| Base para NF-e | Pedidos com valores e destinatário | Emissão fiscal |

Exportação em **CSV** (streaming, sem limite de linhas), **XLSX** (com formatação e
totalizadores) e **PDF** (relatório assinado com data de geração). Exportações
grandes rodam como job assíncrono, gravam no object storage e notificam por link
temporário — em vez de travar a requisição HTTP e dar timeout.
