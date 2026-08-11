# 17 — Padrões de frontend e responsividade

Regras que o painel segue. Estão aqui porque a maioria delas resolve um defeito
que já apareceu — não são preferências de estilo.

---

## 17.1 Responsividade

### A regra que resolve 90% dos casos

**Item de grid ou flex nasce com `min-width: auto` e se recusa a encolher abaixo
do conteúdo.** Basta uma tabela larga dentro de um cartão para a faixa inteira
esticar e a página passar a rolar na horizontal. É o defeito de responsividade
mais comum e o que menos aparece no desktop, porque só se manifesta quando a
tela é menor que o conteúdo.

Três camadas de defesa, aplicadas no sistema e não página a página:

```css
/* base — vale para todo grid do painel */
.grid > * { min-width: 0; }
```

```tsx
<section className="card min-w-0">          {/* componente Secao */}
<div className="w-full min-w-0 overflow-x-auto">  {/* componente Tabela */}
```

O `min-w-0` no contêiner de rolagem não é redundante: sem ele o
`overflow-x-auto` **não tem efeito**, porque o contêiner cresce até caber a
tabela em vez de recortá-la.

Em **flex** o ajuste é pontual, nunca global: `min-width: 0` numa barra de
ferramentas deixaria os botões serem espremidos.

### Breakpoints

Os do Tailwind, sem customização — `sm` 640, `md` 768, `lg` 1024, `xl` 1280.
Convenções em uso:

| Padrão | Onde |
|---|---|
| `lg:flex-row` | A navegação vira barra lateral a partir de 1024px; abaixo é menu recolhível |
| `grid-cols-2 lg:grid-cols-4` | Cartões de indicador: dois por linha no celular, quatro no desktop |
| `gap-4 xl:grid-cols-2` | Seções lado a lado só em tela larga |
| `flex flex-wrap` | Toda barra de ações — a ação desce para baixo do título em vez de vazar |
| `hidden sm:block` | Texto secundário que não cabe no celular |

### Tabela larga

Nunca encolher a tabela: **ela rola dentro do próprio contêiner**. Reduzir fonte
ou esconder colunas no celular tira do usuário justamente o dado que ele foi
buscar. O `min-w-[640px]` garante que as colunas não se esmaguem.

### Como verificar

A verificação é mecânica e não depende de olhar no olho:

```js
document.documentElement.scrollWidth > document.documentElement.clientWidth
```

Se for verdadeiro, alguma coisa vaza. Rodar em **390px** (celular), **768px**
(tablet) e **1440px** (desktop), nos dois temas.

---

## 17.2 Cores e tema

Três estados de tema: escolha explícita (`data-theme`), e o padrão do sistema
via `prefers-color-scheme`. Toda cor é definida como token no `:root` e
redefinida nos dois blocos — **nunca só dentro de um media query**, senão a cor
some no outro modo.

O modo escuro **não é o espelho automático do claro**: os degraus foram
escolhidos e validados separadamente contra a superfície escura.

### Paletas

| Papel | Tokens | Regra |
|---|---|---|
| Categórica (identidade) | `--series-1..3` | Ordem fixa, atribuída à **entidade** e não à posição no ranking. Filtrar séries não pode repintar as que sobraram |
| Ordinal (magnitude) | `--ramp-1..5` | Uma matiz só, luminosidade monotônica. Classes ABC e faixas de retenção medem importância, não identidade |
| Divergente (polaridade) | `--diverge-pos/neg/mid` | Duas matizes opostas, cinza no meio |
| Estado | `--status-good/warning/critical` | **Reservadas** — nunca reaproveitadas como "série 4". Sempre com ícone ou rótulo, nunca cor sozinha |

Toda paleta foi validada com o script do guia de visualização (`--ordinal` para
as rampas), nos dois modos: matiz única, luminosidade monotônica, degraus
visíveis e a ponta clara acima de 2:1 contra a superfície. **Validar é rodar o
script, não olhar e achar bonito.**

---

## 17.3 Gráficos

- **Eixo único, sempre.** Nunca dois eixos Y no mesmo gráfico — a relação entre
  as séries passaria a refletir a escala escolhida, não os dados. Duas grandezas
  diferentes viram dois gráficos: é por isso que a curva de acumulado da ABC
  fica num gráfico próprio abaixo das barras.
- **Legenda a partir de duas séries.** Identidade nunca depende só da cor.
- **Traços finos** (2px), grade e eixos recessivos, pontas arredondadas de 4px.
- **Rótulo direto seletivo** — nunca um número em cada ponto.
- **Hover por padrão**, com tooltip formatado em português.
- **Texto usa token de texto**, nunca a cor da série.
- **Mesma medida, mesma matiz.** Receita do dia e média móvel são a mesma
  grandeza com suavizações diferentes: mesma matiz em dois pesos. Duas matizes
  sugeririam grandezas distintas.
- **Cortar antes de ficar ilegível.** Um Pareto com 500 SKUs vira parede de
  barras de 1px; o gráfico mostra os maiores e diz quantos ficaram de fora.

---

## 17.4 Estados da tela

Toda tela que carrega dados trata os quatro:

| Estado | Componente | Regra |
|---|---|---|
| Carregando | `<Carregando />` | Bloco pulsante da altura do conteúdo final, para o layout não pular |
| Vazio | `<Vazio />` | Diz **por que** está vazio e o que fazer, não só "sem dados" |
| Erro | `<ErroBox />` | Mensagem do backend, que já vem em português |
| Incompleto | `<AvisoQualidade />` | O dado existe mas não está completo — e o painel diz o que falta em vez de exibir número parcial como final |

O último é o que diferencia este painel: um DRE com custo faltando mostra lucro
maior que o real, e é exatamente o tipo de número em que alguém baseia decisão
de preço.

---

## 17.5 Formulários e ações destrutivas

- Valores monetários trafegam como **string** e viram número só na borda de
  apresentação — JSON não tem decimal exato.
- `<Campo>` para rótulo + dica; a dica explica **o que o número significa**, não
  como preencher o campo.
- `<Modal>` usa `<dialog>` nativo: foco preso, `Esc` fecha, semântica de modal
  para leitor de tela — tudo que uma `div` não dá de graça.
- `<BotaoExcluir>` confirma no próprio botão e **desarma sozinho em 4 segundos**:
  um botão que fica armado indefinidamente vira armadilha para o clique
  seguinte.
- Toda escrita que muda custo, imposto ou despesa invalida o cache inteiro —
  o lucro aparece em várias abas ao mesmo tempo, e invalidar só a própria lista
  deixaria as outras exibindo o resultado anterior.
