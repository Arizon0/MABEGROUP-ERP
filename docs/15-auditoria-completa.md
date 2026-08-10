# 15 — Auditoria completa da aplicação

Auditoria de ponta a ponta: cobertura funcional, correção dos cálculos
financeiros, completude do CRUD e integridade da lógica. Este documento registra
**o que foi verificado, o que foi encontrado e o que foi corrigido** — inclusive
os defeitos, porque um relatório que só lista acertos não serve para decidir
nada.

---

## 15.1 Resumo

| Dimensão | Situação |
|---|---|
| Testes automatizados | **197 passando**, 13 pulados (métodos que um canal genuinamente não implementa) |
| Endpoints REST | 84 — sendo 33 de escrita |
| Tabelas | 42, sem divergência entre modelos e migrations |
| Abas do painel | 11 |
| Build do frontend | Limpo (`tsc --noEmit` + `vite build`), 17 testes passando |
| Defeitos encontrados nesta auditoria | 8 — todos corrigidos |

Os 13 testes pulados não são cobertura faltante: o contrato de conectores
percorre todos os métodos de todos os canais e pula o que não existe naquele
canal — o Mercado Pago não tem anúncios nem perguntas, por exemplo.

---

## 15.2 Defeitos encontrados e corrigidos

### 1. Lucro real não existia — o imposto do vendedor nunca era calculado

**Gravidade: alta.** O campo `tax_amount` existia no modelo e era somado nos
relatórios, mas **nada jamais atribuía valor a ele**. Nenhum marketplace informa
o imposto do regime tributário do vendedor — ele não é retido, o dinheiro chega
cheio e é recolhido depois. O resultado é que o sistema exibia como "líquido" um
valor do qual ainda sairia o tributo, e chamava de margem o que era margem antes
do imposto.

**Correção.** Módulo de impostos com **vigência** (`tax_rules`): alíquota, base
de cálculo (receita bruta, bruta + frete ou líquida), canal opcional e período
de validade. O imposto de um pedido é calculado pela regra vigente **na data da
venda**, não pela regra de hoje — sem isso, mudar de faixa no Simples reescreveria
retroativamente o lucro de todos os meses anteriores.

A distinção mais importante virou duas colunas separadas no pedido:

- `tax_amount` — **imposto retido pelo canal**, já descontado do repasse e
  portanto embutido no líquido;
- `sales_tax_amount` — **imposto apurado pelo regime do vendedor**, deduzido
  apenas no DRE.

Somar o segundo ao líquido contaria o tributo duas vezes.

### 2. Não havia lucro operacional — só margem de contribuição

**Gravidade: alta.** O sistema chegava até a margem de contribuição (líquido −
CMV) e parava ali. Aluguel, pró-labore, contador e software não entravam em
lugar nenhum, então o número mais visível do painel era uma margem que o
vendedor podia facilmente ler como lucro.

**Correção.** `operating_expenses` por **competência** — a despesa entra no mês a
que se refere, não no mês em que foi paga — e um **DRE gerencial** completo até o
lucro operacional, com ponto de equilíbrio, lucro por pedido e série mensal.

As despesas ficam **fora do pedido** de propósito: ratear despesa fixa por venda
produziria um "custo por pedido" que muda conforme o volume do mês, o que não
ajuda ninguém a decidir preço.

### 3. A coluna do DRE não fechava com o total impresso

**Gravidade: média.** Encontrado pelo teste de ponta a ponta, não por inspeção.
Somando à mão as linhas de receita e dedução do demonstrativo, o resultado
divergia do subtotal "Líquido recebido" em **R$ 13.365,36** sobre R$ 96.369,72
na base de demonstração — cerca de 14%.

A causa não é um erro de cálculo: o líquido é **importado do canal, nunca
recalculado** (recalculá-lo faria o painel divergir do extrato). Acontece que a
soma das taxas discriminadas não fecha com ele — parte do custo de frete é
faturada à parte em vez de descontada do repasse, e nem todo ajuste vem
detalhado por pedido. O demonstrativo exibia os componentes e o total, e os dois
não conversavam.

**Correção.** Linha explícita **(±) Ajustes não discriminados pelo canal**, igual
a `líquido informado − soma das taxas discriminadas`. A coluna passa a fechar, e
o tamanho da linha é ele próprio um diagnóstico: valor alto significa
detalhamento de taxas incompleto, e o lugar de investigar é a conciliação.

### 4. Erro de validação em campo monetário devolvia 500 em vez de 422

**Gravidade: média.** `RequestValidationError.errors()` devolve o `ctx` cru do
Pydantic, que em campos com limite numérico carrega o limite como `Decimal`. O
`JSONResponse` serializa com `json.dumps`, que não conhece `Decimal` — então
qualquer alíquota acima de 100% ou valor negativo derrubava o handler de erro e
o cliente recebia um 500 genérico em vez da mensagem que dizia qual campo estava
errado.

**Correção.** `jsonable_encoder` no tratador. Coberto por teste.

### 5. O mesmo valor era serializado com duas escalas diferentes

**Gravidade: baixa, mas contaminava o contrato da API.** `str(Decimal)` preserva
a escala do objeto, e a escala variava conforme o caminho do código: um registro
recém-criado devolvia `10.00` (a escala do corpo da requisição), enquanto o mesmo
registro relido do banco devolvia `10.0000` (a escala da coluna `Numeric(18,4)`).
Duas representações do mesmo valor quebram comparação no cliente.

**Correção.** Serialização canônica: zeros à direita descartados, mínimo de duas
casas. Precisão real acima de centavos — custo unitário de peça, alíquota
fracionária — é preservada, porque só os zeros irrelevantes somem.

### 6. Simples Nacional aplicado como alíquota fixa

**Gravidade: alta.** A regra tributária multiplicava a receita por um percentual
único. O Simples não funciona assim: a alíquota é função da receita bruta
acumulada dos 12 meses anteriores (RBT12), pela fórmula do art. 18 da
LC 123/2006 — `(RBT12 × nominal − parcela a deduzir) ÷ RBT12`. Uma alíquota fixa
erra sempre que o faturamento cruza faixa, e erra para os dois lados.

**Correção.** Regime progressivo com faixas **editáveis no banco**, resolvido uma
vez por mês (não por pedido, para que dois pedidos do mesmo mês nunca saiam com
alíquotas diferentes). A RBT12 é exibida ao lado da alíquota, porque sem ela a
apuração vira um número que o contador teria de aceitar por fé. Tratados também:
proporcionalização para empresa com menos de 12 meses, e alerta de
desenquadramento quando a receita passa do teto da tabela.

### 7. Frete do fornecedor até o galpão não existia

**Gravidade: alta.** O custo do produto era apenas o preço pago ao fornecedor. O
frete de compra — que contabilmente **integra o custo de aquisição do estoque**,
não é despesa do mês — não tinha onde ser lançado. O CMV saía subestimado e o
lucro inflado, com o erro concentrado justamente nos itens pesados, que são os
que mais custam para trazer.

**Correção.** `freight_in_cost` e `other_acquisition_cost` no produto, ambos no
CMV congelado da venda, e um endpoint de **rateio do frete da nota** por
quantidade ou por valor, com simulação antes de aplicar.

### 8. CMV divergente entre ingestão e remapeamento de SKU

**Gravidade: média.** Ao vincular um SKU pendente a um produto, o recálculo usava
só `unit_cost`, enquanto a ingestão usava custo mais embalagem. O mesmo pedido
saía com CMV diferente conforme tivesse sido mapeado antes ou depois da
importação. Corrigido para usar o mesmo custo nos dois caminhos.

---

## 15.3 Cálculos financeiros verificados

### Cadeia completa

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
(=) Líquido recebido dos canais          ← informado pelo canal
(−) Imposto sobre vendas                 ← Simples progressivo por RBT12
(−) CMV (aquisição + frete de compra + embalagem)  ← congelado na venda
(=) Margem de contribuição
(−) Despesas operacionais
(=) Lucro operacional                    ← o lucro real
```

### Invariantes testadas

| Invariante | Por que importa |
|---|---|
| `lucro ≤ margem ≤ líquido ≤ bruto` | O teste mais barato contra dinheiro inventado: duplicação de pagamento ou dedução com sinal trocado quebra a ordem antes de quebrar qualquer outra coisa |
| Soma da coluna = total impresso | Quem confere um demonstrativo soma a coluna; se não fechar, o relatório não serve |
| Imposto do vendedor não altera o líquido | O canal repassa o valor cheio; se encostasse no líquido, o painel divergiria do extrato e o tributo entraria duas vezes no lucro |
| Reapuração é idempotente | Corrigir o passado não pode multiplicar o imposto |
| Todo pedido tributado aponta a regra usada | Auditoria fiscal reconstrói a conta, não só o total |
| Receita do DRE = receita da visão geral | Dois módulos, uma verdade — dois faturamentos no mesmo painel destroem a confiança em ambos |
| Período vazio devolve zeros | Vazio é estado normal do painel, não exceção; nada divide por zero |
| Cancelado não gera imposto | Venda desfeita não é fato gerador |
| Devolução reduz a base tributável | Idem, proporcionalmente |
| Regra escolhida pela data da venda | Mudança de faixa não reescreve o passado |
| Custo congelado na venda | Alterar o custo hoje não pode alterar a margem histórica |
| Sem salto de degrau ao cruzar faixa do Simples | R$ 1 a mais de faturamento não pode custar milhares em tributo |
| Alíquota efetiva cresce com o faturamento | Monotonicidade da tabela progressiva |
| RBT12 exclui o mês corrente | Incluir faria a alíquota mudar a cada venda do mês |
| Cancelado fora da RBT12 | Venda desfeita não é receita bruta |
| Rateio de frete soma o total da nota | Nenhum centavo de frete pode sumir ou ser criado no rateio |

### Tratamento numérico

`Decimal` da ingestão à apresentação; arredondamento (`ROUND_HALF_UP`, 2 casas)
só na borda de saída. Colunas monetárias em `Numeric(18,4)` — as duas casas
extras existem para custo unitário, que em autopeça frequentemente tem fração de
centavo.

---

## 15.4 Cobertura funcional — controle operacional

As 11 abas cobrem o ciclo: **visão geral · ao vivo · faturamento e conciliação ·
pedidos · produtos e estoque · custos e lucro · logística · atendimento ·
marketing · relatórios · configurações**.

### CRUD — o que dá para editar e excluir

A auditoria encontrou o sistema **quase só de leitura**: havia 9 endpoints de
escrita, e faltava editar produto, excluir produto, desfazer um de-para de SKU,
editar usuário e lançar custo de mídia (documentado, mas sem endpoint). Hoje são
31.

| Entidade | Criar | Editar | Excluir | Regra de exclusão |
|---|---|---|---|---|
| Produto | ✅ | ✅ | ✅ | Com venda registrada é **desativado**, não apagado — o histórico de margem continua consultável |
| De-para de SKU | ✅ | — | ✅ | Desfazer devolve o SKU às pendências; o custo já congelado nas vendas antigas não muda |
| Custo em lote | — | ✅ | — | Afeta só vendas futuras |
| Frete de compra | — | ✅ | — | Rateio por quantidade ou valor, com simulação |
| Faixas do Simples | ✅ | ✅ | ✅ | Substituídas por inteiro, nunca mescladas |
| Regra tributária | ✅ | ✅ | ✅ | Reapuração recalcula os pedidos do período |
| Despesa operacional | ✅ | ✅ | ✅ | Recorrentes replicáveis para o mês seguinte |
| Usuário | ✅ | ✅ | Desativa | **Nunca apagado**: o `user_id` é referenciado na trilha de auditoria, e apagá-lo deixaria registros órfãos justamente na tabela usada para investigar incidentes |
| Conta de canal | OAuth | — | Revoga | |
| Custo de mídia | — | ✅ | — | Lançamento manual por campanha |
| Regra de alerta | ✅ | ✅ | ✅ | |
| Pedido | Ingestão | — | — | **Intencionalmente imutável**: pedido é fato consumado do canal; corrigir é re-sincronizar |

Duas escolhas merecem registro porque parecem lacunas e não são: **pedidos não
são editáveis** (o dado pertence ao canal — permitir edição criaria uma verdade
paralela que a próxima sincronização desfaria) e **usuários não são excluíveis**
(a auditoria depende da referência). A última conta de proprietário ativa é
protegida contra desativação e rebaixamento, para que a organização nunca fique
sem quem a administre.

---

## 15.5 Qualidade do dado — o painel diz o que não sabe

O DRE marca `qualidade.confiavel = false` e explica o que falta quando há item
sem custo cadastrado ou pedido sem regra tributária vigente. A escolha é
deliberada: um DRE com custo faltando mostra lucro **maior** que o real, e é
exatamente o tipo de número em que alguém baseia uma decisão de preço.

Pela mesma razão, a alíquota e as despesas de exemplo são semeadas **apenas em
modo simulado**. Numa instalação real, semear uma alíquota produziria um lucro
aparentemente apurado sobre um imposto que o vendedor não paga — um erro de
decisão com cara de relatório pronto. Instalação nova exibe o aviso de regra
ausente, que é a resposta correta, não uma falha.

O selo de procedência acompanha todo valor líquido: **liquidado** (repasse
confirmado), **informado** (o canal declarou, ainda não liberou) ou **estimado**
(calculado a partir das taxas conhecidas).

---

## 15.6 Limitações que permanecem

Nenhuma delas é contornável por código — todas dependem de terceiros:

1. **Custo de mídia** — a Ads API da Shopee exige whitelist adicional e o
   Mercado Livre não expõe custo consolidado por campanha. Mitigado com
   lançamento manual, que faz o cálculo de retorno funcionar normalmente.
2. **Ajustes não discriminados** — os canais não detalham todo ajuste por
   pedido. Mitigado pela linha explícita de reconciliação, que ao menos torna a
   diferença visível e mensurável.
3. **Janela de 15 dias da Shopee** para listagem de pedidos, contornada com
   paginação por janelas deslizantes.
4. **Teto de offset de 1000 do Mercado Livre**, contornado com paginação por
   data.
5. **Refresh token de uso único do Mercado Livre**, tratado com trava
   distribuída para evitar que duas rotinas simultâneas invalidem a credencial.

---

## 15.7 Como reproduzir esta auditoria

```bash
# Backend — 177 testes
cd backend && .venv/bin/python -m pytest tests/ -v

# Só a cadeia financeira de ponta a ponta
.venv/bin/python -m pytest tests/test_auditoria_ponta_a_ponta.py tests/test_impostos_e_dre.py \
                          tests/test_simples_e_custo_aquisicao.py -v

# Divergência entre modelos e migrations (deve gerar um upgrade vazio)
.venv/bin/alembic revision --autogenerate -m "verificacao"

# Frontend — tipos, build e 17 testes
cd frontend && npm run build && npm run test -- --run
```
