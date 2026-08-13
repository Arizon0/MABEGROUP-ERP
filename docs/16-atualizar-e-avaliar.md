# 16 — Atualizar a aplicação e começar a avaliar

Guia para trazer as mudanças novas para a máquina e percorrer o sistema com
sentido, em vez de clicar aba por aba sem saber o que procurar.

---

## 16.1 Atualizar (5 minutos)

Abra o Terminal na pasta do projeto:

```bash
cd ~/MABEGROUP-ERP          # ajuste se o clone estiver em outro lugar
git pull origin claude/marketplace-sales-consolidation-hvazwg
docker compose up -d --build
```

É isso. **Não precisa apagar o banco.** O Compose roda as migrations num serviço
próprio (`migrations`) e a API só sobe depois que elas terminam — as colunas e
tabelas novas são criadas por cima do que já existe, com os dados preservados.
Esse caminho é testado automaticamente em `tests/test_migrations.py`, justamente
porque uma migration que só funciona em banco vazio falha exatamente uma vez: no
dia da atualização, com dados reais dentro.

Acompanhe a subida:

```bash
docker compose logs -f migrations   # deve terminar com "Running upgrade ... 734efb8728a7"
docker compose ps                   # api, worker, db, redis e frontend "Up"
```

Painel em **http://localhost:5173** · API em **http://localhost:8000/docs**

### Se algo não subir

```bash
docker compose logs api --tail 50
```

Recomeçar do zero (**apaga os dados locais** — em modo simulado não custa nada,
com contas reais conectadas o próximo sync traz tudo de volta):

```bash
docker compose down -v && docker compose up -d --build
```

---

## 16.2 Uma pendência de quem já atualizou antes

Se você chegou a rodar a versão anterior, ela criou uma regra tributária de
exemplo com **alíquota fixa de 8%**. A carga inicial não sobrescreve regra
existente — de propósito, para nunca mexer em configuração fiscal que alguém
possa ter ajustado.

Para usar o Simples progressivo, vá em **Custos e lucro → Regras tributárias**,
edite a regra e troque o regime para *Simples Nacional (progressivo)*, ou apague
e deixe a carga inicial recriar. As faixas do Anexo I vêm preenchidas.

---

## 16.2b Sincronizar uma conta

```bash
./scripts/sincronizar.sh          # conta 1, completa
./scripts/sincronizar.sh 2        # outra conta
./scripts/sincronizar.sh 1 rapida # só pedidos
```

O script faz login, dispara a sincronização em segundo plano e diz onde ficou a
saída. Ele existe porque a alternativa manual falha de três formas silenciosas:
o token expira em 30 minutos, a variável de ambiente só vale na aba onde foi
criada, e fechar o terminal cancela um backfill que leva dezenas de minutos. Nos
três casos a requisição sai sem autenticação e volta apenas "não autorizado",
sem dizer qual dos três aconteceu.

Acompanhe o avanço:

```bash
docker compose exec db psql -U marketplace -d marketplace_hub \
  -c "select count(*) from orders;"
```

---

## 16.2c Completar o frete depois de um backfill

O backfill de volume alto importa sem buscar o frete de cada pedido, para não
triplicar as chamadas à API. Enquanto o frete não chega, **o líquido do painel
fica inflado** — o frete é dedução na fórmula.

O worker completa sozinho, em lotes a cada três minutos. Logo depois de um
backfill isso leva horas, e o painel exibe margem maior que a real nesse
intervalo. Para resolver de uma vez:

```bash
docker compose exec api python -m app.workers.completar
```

Roda a mesma tarefa em sequência até a fila zerar, imprimindo o progresso. Ao
terminar, confira a convergência contra o relatório do canal:

```bash
docker compose exec db psql -U marketplace -d marketplace_hub \
  -c "select count(*) filter (where shipping_cost=0) sem_frete,
             round(sum(net_amount),0) liquido
      from orders where status <> 'cancelled';"
```

---

## 16.3 Roteiro de avaliação

A ordem importa: cada passo alimenta o seguinte.

### Passo 1 — Custos reais dos produtos

**Produtos → Produtos internos.** É a base de tudo; sem custo, toda margem do
sistema é ficção.

1. Lance o **custo unitário** (o que você paga ao fornecedor) de alguns SKUs.
2. Em **Frete de compra**, pegue uma nota real: informe o frete total, escolha o
   critério e clique em **Simular** antes de aplicar. Confira o valor por unidade
   contra a sua conta.
3. Repare na coluna **Custo total** — é `fornecedor + frete + outros + embalagem`,
   e é esse valor que entra no CMV.

> **O que verificar:** o custo total bate com o que você calcularia na planilha?
> Se não bater, o erro está aqui e contamina todo o resto.

### Passo 2 — Regra tributária

**Custos e lucro → Regras tributárias.** Confira as faixas do Anexo I com seu
contador antes de confiar na apuração. Depois clique em **Reapurar período** para
recalcular os pedidos já importados.

Suba até **Apuração do Simples Nacional** e confira a RBT12: o acumulado dos 12
meses anteriores e a alíquota que ele produziu. Refaça a conta à mão uma vez —
`(RBT12 × nominal − parcela a deduzir) ÷ RBT12` — para ganhar confiança no número.

### Passo 3 — Despesas do mês

**Custos e lucro → Despesas operacionais.** Lance aluguel, pró-labore,
contador, software. Marque as fixas como **recorrentes** para replicá-las no mês
seguinte com um clique.

### Passo 4 — Ler o DRE

Com custo, imposto e despesa no lugar, o **Demonstrativo de resultado** passa a
ser o lucro real.

Como conferir de verdade:

- **Some a coluna à mão.** Ela fecha no lucro operacional — é para isso que
  existe a linha *(±) Ajustes não discriminados*.
- **Olhe o tamanho dessa linha de ajustes.** Ela é a diferença entre o líquido
  que o canal repassa e a soma das taxas que ele detalha. Grande demais significa
  detalhamento incompleto, e o lugar de investigar é a aba de conciliação.
- **Confira o aviso de qualidade.** Se aparecer, o lucro exibido está maior que o
  real: falta custo em algum item ou regra tributária em algum pedido.

### Passo 5 — Dinheiro a receber

**Custos e lucro → Saldo a receber.** Compare o total com o que o Mercado Pago e a
Shopee mostram nos apps deles. O campo **Vencido** merece atenção: data de
liberação no passado com status ainda pendente significa repasse atrasado ou
webhook que não chegou.

### Passo 6 — O resto do painel

Visão geral, Ao vivo, Faturamento, Pedidos, Logística, Atendimento, Marketing,
Relatórios. Aqui a pergunta é diferente: **falta alguma informação que você usa
para decidir?**

---

## 16.4 O que olhar com desconfiança

Coisas que o sistema faz por decisão de projeto e que podem surpreender:

| Comportamento | Por quê |
|---|---|
| Alterar o custo não muda a margem de vendas antigas | O custo é congelado na venda; senão todo fechamento se reescreveria sozinho |
| Pedido não é editável | O dado pertence ao canal — editar criaria uma verdade que o próximo sync desfaz |
| Produto com venda é desativado, não apagado | O histórico de margem continua consultável |
| Usuário nunca é excluído | O `user_id` é referenciado na trilha de auditoria |
| O líquido não é recalculado a partir das taxas | É o valor que o canal repassa; recalcular faria o painel divergir do extrato |
| Instalação nova avisa que falta regra tributária | É a resposta correta — semear uma alíquota que você não paga seria pior |

---

## 16.5 Rodar os testes você mesmo

```bash
docker compose exec api pytest -q          # 198 testes
docker compose exec api pytest tests/test_simples_e_custo_aquisicao.py -v
```

O segundo comando é o mais interessante para conferir a parte fiscal: os nomes
dos testes descrevem cada regra em português.
