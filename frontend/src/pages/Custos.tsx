/**
 * Custos, impostos e lucro real.
 *
 * Esta aba existe porque nenhum marketplace conhece três números que decidem
 * se o negócio dá lucro: o custo do produto, o imposto do regime do vendedor e
 * a despesa fixa do mês. O painel até o líquido recebido é o que o canal informa;
 * daqui para baixo é o que só o vendedor sabe.
 */
import { useState } from 'react'

import {
  useDRE,
  useDREMensal,
  useDespesas,
  useRegrasImposto,
  useReapurarImpostos,
  useRemoverDespesa,
  useRemoverRegra,
  useReplicarDespesas,
  useSalvarDespesa,
  useSalvarRegra,
} from '@/api/queries'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import {
  AvisoQualidade,
  BotaoExcluir,
  Campo,
  Carregando,
  ErroBox,
  KpiCard,
  Modal,
  Secao,
  Tabela,
  Vazio,
} from '@/components/ui'
import { brl, data, pct } from '@/lib/format'
import type { Despesa, LinhaDRE, RegraImposto } from '@/types/api'

const BASES: Record<string, string> = {
  gross_revenue: 'Receita bruta',
  gross_plus_shipping: 'Receita bruta + frete',
  net_revenue: 'Receita líquida',
}

const CATEGORIAS: Record<string, string> = {
  rent: 'Aluguel',
  payroll: 'Pessoal',
  software: 'Software',
  marketing: 'Marketing',
  packaging: 'Embalagem',
  logistics: 'Logística',
  accounting: 'Contabilidade',
  fixed_taxes: 'Impostos fixos',
  other: 'Outra',
}

const mesAtual = () => new Date().toISOString().slice(0, 7)

export function Custos() {
  const [filtros] = useFiltros()
  const dre = useDRE(filtros)
  const mensal = useDREMensal(12)

  if (dre.isError) return <ErroBox erro={dre.error} />

  const ind = dre.data?.indicadores

  return (
    <div className="space-y-4">
      <FiltroGlobal />

      {dre.data && !dre.data.qualidade.confiavel && (
        <AvisoQualidade texto={dre.data.qualidade.aviso} />
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          rotulo="Margem de contribuição"
          valor={ind?.margem_contribuicao ?? 0}
          formato="moeda"
          dica="Líquido recebido menos imposto sobre vendas e custo do produto."
        />
        <KpiCard
          rotulo="Lucro operacional"
          valor={ind?.lucro_operacional ?? 0}
          formato="moeda"
          dica="O lucro real: margem de contribuição menos as despesas fixas do período."
        />
        <KpiCard
          rotulo="Margem líquida"
          valor={ind?.lucro_operacional_pct ?? 0}
          formato="percentual"
          dica="Lucro operacional sobre a receita bruta."
        />
        <KpiCard
          rotulo="Ponto de equilíbrio"
          valor={ind?.ponto_de_equilibrio ?? 0}
          formato="moeda"
          dica="Receita bruta necessária para cobrir todos os custos do período."
        />
      </div>

      <Secao
        titulo="Demonstrativo de resultado"
        descricao="Da receita bruta ao lucro operacional. O percentual é sobre a receita bruta."
      >
        {dre.isLoading ? (
          <Carregando altura="h-96" />
        ) : (
          <QuadroDRE linhas={dre.data?.linhas ?? []} />
        )}
      </Secao>

      <div className="grid gap-4 lg:grid-cols-2">
        <Secao titulo="Lucro mês a mês" descricao="Tendência dos últimos 12 meses.">
          {mensal.isLoading ? (
            <Carregando altura="h-64" />
          ) : (
            <Tabela colunas={['Mês', 'Receita bruta', 'Líquido', 'Lucro', '%']}>
              {(mensal.data ?? []).map((m) => (
                <tr key={m.mes} className="hover:bg-surface-raised">
                  <td className="td num text-xs">{m.mes}</td>
                  <td className="td num">{brl(m.receita_bruta)}</td>
                  <td className="td num">{brl(m.liquido)}</td>
                  <td
                    className={`td num font-medium ${
                      Number(m.lucro_operacional) >= 0 ? 'text-good' : 'text-bad'
                    }`}
                  >
                    {brl(m.lucro_operacional)}
                  </td>
                  <td className="td num text-xs text-ink-muted">{pct(m.lucro_pct)}</td>
                </tr>
              ))}
            </Tabela>
          )}
        </Secao>

        <Secao
          titulo="Despesas por categoria"
          descricao="Composição das despesas fixas lançadas no período."
        >
          {Object.keys(dre.data?.despesas_por_categoria ?? {}).length === 0 ? (
            <Vazio
              titulo="Nenhuma despesa no período"
              descricao="Sem despesas lançadas, o lucro operacional é igual à margem de contribuição — ou seja, ainda não é o lucro real."
            />
          ) : (
            <Tabela colunas={['Categoria', 'Valor']}>
              {Object.entries(dre.data?.despesas_por_categoria ?? {}).map(([cat, valor]) => (
                <tr key={cat} className="hover:bg-surface-raised">
                  <td className="td">{CATEGORIAS[cat] ?? cat}</td>
                  <td className="td num">{brl(valor)}</td>
                </tr>
              ))}
            </Tabela>
          )}
        </Secao>
      </div>

      <RegrasTributarias />
      <DespesasOperacionais />
    </div>
  )
}

// --- DRE ---------------------------------------------------------------------

function QuadroDRE({ linhas }: { linhas: LinhaDRE[] }) {
  if (linhas.length === 0) return <Vazio titulo="Sem movimento no período" />

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-collapse">
        <tbody>
          {linhas.map((l) => {
            const destaque = l.tipo === 'subtotal' || l.tipo === 'resultado'
            const negativo = Number(l.valor) < 0
            return (
              <tr
                key={l.rotulo}
                className={
                  l.tipo === 'resultado'
                    ? 'border-t-2 border-line bg-surface-raised'
                    : l.tipo === 'subtotal'
                      ? 'border-t border-line'
                      : ''
                }
              >
                <td className={`td ${destaque ? 'font-semibold text-ink' : 'text-ink-soft'}`}>
                  {l.rotulo}
                  {l.detalhe && (
                    <span className="ml-1.5 text-[10px] text-ink-muted" title={l.detalhe}>
                      ⓘ
                    </span>
                  )}
                </td>
                <td
                  className={`td num text-right ${
                    l.tipo === 'resultado'
                      ? `text-base font-semibold ${negativo ? 'text-bad' : 'text-good'}`
                      : destaque
                        ? 'font-semibold text-ink'
                        : negativo
                          ? 'text-bad'
                          : 'text-ink'
                  }`}
                >
                  {brl(l.valor)}
                </td>
                <td className="td num w-20 text-right text-xs text-ink-muted">
                  {pct(l.percentual)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// --- Regras tributárias ------------------------------------------------------

const REGRA_VAZIA = {
  name: '',
  kind: 'simples_nacional',
  rate_pct: '',
  base: 'gross_revenue',
  channel: '',
  valid_from: new Date().toISOString().slice(0, 10),
  valid_to: '',
  notes: '',
}

function RegrasTributarias() {
  const regras = useRegrasImposto()
  const salvar = useSalvarRegra()
  const remover = useRemoverRegra()
  const reapurar = useReapurarImpostos()

  const [edicao, setEdicao] = useState<(typeof REGRA_VAZIA & { id?: number }) | null>(null)

  const abrir = (r?: RegraImposto) =>
    setEdicao(
      r
        ? {
            id: r.id,
            name: r.name,
            kind: r.kind,
            rate_pct: r.rate_pct,
            base: r.base,
            channel: r.channel,
            valid_from: r.valid_from,
            valid_to: r.valid_to ?? '',
            notes: r.notes,
          }
        : { ...REGRA_VAZIA },
    )

  return (
    <Secao
      titulo="Regras tributárias"
      descricao="A alíquota do regime do vendedor, com período de vigência. Uma venda de março é tributada pela regra de março, não pela de hoje."
      acao={
        <div className="flex gap-2">
          <button
            type="button"
            className="btn text-xs"
            disabled={reapurar.isPending}
            onClick={() => reapurar.mutate({})}
            title="Recalcula o imposto dos pedidos já importados usando as regras atuais."
          >
            {reapurar.isPending ? 'Reapurando…' : 'Reapurar período'}
          </button>
          <button type="button" className="btn btn-primary text-xs" onClick={() => abrir()}>
            Nova regra
          </button>
        </div>
      }
    >
      {regras.isLoading ? (
        <Carregando altura="h-32" />
      ) : (regras.data ?? []).length === 0 ? (
        <Vazio
          titulo="Nenhuma regra cadastrada"
          descricao="Sem regra vigente o imposto sobre vendas fica zerado e o lucro exibido é maior que o real."
        />
      ) : (
        <Tabela colunas={['Regra', 'Alíquota', 'Base', 'Canal', 'Vigência', 'Situação', '']}>
          {(regras.data ?? []).map((r) => (
            <tr key={r.id} className="hover:bg-surface-raised">
              <td className="td">{r.name}</td>
              <td className="td num">{pct(r.rate_pct, 2)}</td>
              <td className="td text-xs">{BASES[r.base] ?? r.base}</td>
              <td className="td text-xs">{r.channel || 'Todos'}</td>
              <td className="td text-xs">
                {data(r.valid_from)} — {r.valid_to ? data(r.valid_to) : 'em aberto'}
              </td>
              <td className="td text-xs">
                {r.is_active ? (
                  <span className="text-good">Ativa</span>
                ) : (
                  <span className="text-ink-muted">Inativa</span>
                )}
              </td>
              <td className="td">
                <div className="flex justify-end gap-1">
                  <button type="button" className="btn px-2 py-1 text-xs" onClick={() => abrir(r)}>
                    Editar
                  </button>
                  <BotaoExcluir
                    aoConfirmar={() => remover.mutate(r.id)}
                    ocupado={remover.isPending && remover.variables === r.id}
                  />
                </div>
              </td>
            </tr>
          ))}
        </Tabela>
      )}

      <Modal
        aberto={edicao !== null}
        titulo={edicao?.id ? 'Editar regra tributária' : 'Nova regra tributária'}
        descricao="A vigência é o que permite mudar de faixa do Simples sem reescrever o histórico."
        aoFechar={() => setEdicao(null)}
      >
        {edicao && (
          <form
            className="space-y-3"
            onSubmit={(evento) => {
              evento.preventDefault()
              salvar.mutate(
                { ...edicao, valid_to: edicao.valid_to || null },
                { onSuccess: () => setEdicao(null) },
              )
            }}
          >
            <Campo rotulo="Nome">
              <input
                required
                className="input"
                value={edicao.name}
                placeholder="Simples Nacional — Anexo I"
                onChange={(e) => setEdicao({ ...edicao, name: e.target.value })}
              />
            </Campo>
            <div className="grid grid-cols-2 gap-3">
              <Campo rotulo="Alíquota (%)">
                <input
                  required
                  type="number"
                  step="0.01"
                  min="0"
                  max="100"
                  className="input"
                  value={edicao.rate_pct}
                  onChange={(e) => setEdicao({ ...edicao, rate_pct: e.target.value })}
                />
              </Campo>
              <Campo rotulo="Base de cálculo">
                <select
                  className="input"
                  value={edicao.base}
                  onChange={(e) => setEdicao({ ...edicao, base: e.target.value })}
                >
                  {Object.entries(BASES).map(([valor, rotulo]) => (
                    <option key={valor} value={valor}>
                      {rotulo}
                    </option>
                  ))}
                </select>
              </Campo>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Campo rotulo="Vigente a partir de">
                <input
                  required
                  type="date"
                  className="input"
                  value={edicao.valid_from}
                  onChange={(e) => setEdicao({ ...edicao, valid_from: e.target.value })}
                />
              </Campo>
              <Campo rotulo="Vigente até" dica="Em branco = sem prazo final.">
                <input
                  type="date"
                  className="input"
                  value={edicao.valid_to}
                  onChange={(e) => setEdicao({ ...edicao, valid_to: e.target.value })}
                />
              </Campo>
            </div>
            <Campo rotulo="Canal" dica="Em branco aplica a todos os canais.">
              <select
                className="input"
                value={edicao.channel}
                onChange={(e) => setEdicao({ ...edicao, channel: e.target.value })}
              >
                <option value="">Todos os canais</option>
                <option value="mercadolivre">Mercado Livre</option>
                <option value="shopee">Shopee</option>
              </select>
            </Campo>
            {salvar.isError && <ErroBox erro={salvar.error} />}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn text-xs" onClick={() => setEdicao(null)}>
                Cancelar
              </button>
              <button type="submit" className="btn btn-primary text-xs" disabled={salvar.isPending}>
                {salvar.isPending ? 'Salvando…' : 'Salvar'}
              </button>
            </div>
          </form>
        )}
      </Modal>
    </Secao>
  )
}

// --- Despesas operacionais ---------------------------------------------------

function DespesasOperacionais() {
  const [mes, setMes] = useState(mesAtual())
  const despesas = useDespesas(`${mes}-01`)
  const salvar = useSalvarDespesa()
  const remover = useRemoverDespesa()
  const replicar = useReplicarDespesas()

  const vazia = {
    description: '',
    category: 'other',
    amount: '',
    competence_month: `${mes}-01`,
    is_recurring: false,
    channel: '',
    notes: '',
  }
  const [edicao, setEdicao] = useState<(typeof vazia & { id?: number }) | null>(null)

  const total = (despesas.data ?? []).reduce((soma, d) => soma + Number(d.amount), 0)

  const mesAnterior = () => {
    const [ano, m] = mes.split('-').map(Number)
    const d = new Date(Date.UTC(ano, m - 2, 1))
    return d.toISOString().slice(0, 7)
  }

  return (
    <Secao
      titulo="Despesas operacionais"
      descricao="Lançadas por competência: entram no mês a que se referem, não no mês em que foram pagas."
      acao={
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="month"
            className="input py-1 text-xs"
            value={mes}
            onChange={(e) => setMes(e.target.value || mesAtual())}
          />
          <button
            type="button"
            className="btn text-xs"
            disabled={replicar.isPending}
            title="Copia as despesas marcadas como recorrentes do mês anterior para este mês."
            onClick={() =>
              replicar.mutate({ origem: `${mesAnterior()}-01`, destino: `${mes}-01` })
            }
          >
            {replicar.isPending ? 'Replicando…' : 'Replicar recorrentes'}
          </button>
          <button
            type="button"
            className="btn btn-primary text-xs"
            onClick={() => setEdicao({ ...vazia })}
          >
            Nova despesa
          </button>
        </div>
      }
    >
      {despesas.isLoading ? (
        <Carregando altura="h-32" />
      ) : (despesas.data ?? []).length === 0 ? (
        <Vazio
          titulo="Nenhuma despesa neste mês"
          descricao="Aluguel, pró-labore, contador e software entram aqui. Sem eles o lucro exibido ainda é margem de contribuição."
        />
      ) : (
        <>
          <Tabela colunas={['Descrição', 'Categoria', 'Canal', 'Recorrente', 'Valor', '']}>
            {(despesas.data ?? []).map((d: Despesa) => (
              <tr key={d.id} className="hover:bg-surface-raised">
                <td className="td max-w-[260px] truncate">{d.description}</td>
                <td className="td text-xs">{CATEGORIAS[d.category] ?? d.category}</td>
                <td className="td text-xs">{d.channel || 'Todos'}</td>
                <td className="td text-xs">{d.is_recurring ? 'Sim' : 'Não'}</td>
                <td className="td num">{brl(d.amount)}</td>
                <td className="td">
                  <div className="flex justify-end gap-1">
                    <button
                      type="button"
                      className="btn px-2 py-1 text-xs"
                      onClick={() =>
                        setEdicao({
                          id: d.id,
                          description: d.description,
                          category: d.category,
                          amount: d.amount,
                          competence_month: d.competence_month,
                          is_recurring: d.is_recurring,
                          channel: d.channel,
                          notes: d.notes,
                        })
                      }
                    >
                      Editar
                    </button>
                    <BotaoExcluir
                      aoConfirmar={() => remover.mutate(d.id)}
                      ocupado={remover.isPending && remover.variables === d.id}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </Tabela>
          <p className="mt-3 text-right text-sm text-ink">
            Total do mês: <span className="num font-semibold">{brl(total)}</span>
          </p>
        </>
      )}

      <Modal
        aberto={edicao !== null}
        titulo={edicao?.id ? 'Editar despesa' : 'Nova despesa'}
        aoFechar={() => setEdicao(null)}
      >
        {edicao && (
          <form
            className="space-y-3"
            onSubmit={(evento) => {
              evento.preventDefault()
              salvar.mutate(edicao, { onSuccess: () => setEdicao(null) })
            }}
          >
            <Campo rotulo="Descrição">
              <input
                required
                className="input"
                value={edicao.description}
                placeholder="Aluguel do galpão"
                onChange={(e) => setEdicao({ ...edicao, description: e.target.value })}
              />
            </Campo>
            <div className="grid grid-cols-2 gap-3">
              <Campo rotulo="Categoria">
                <select
                  className="input"
                  value={edicao.category}
                  onChange={(e) => setEdicao({ ...edicao, category: e.target.value })}
                >
                  {Object.entries(CATEGORIAS).map(([valor, rotulo]) => (
                    <option key={valor} value={valor}>
                      {rotulo}
                    </option>
                  ))}
                </select>
              </Campo>
              <Campo rotulo="Valor (R$)">
                <input
                  required
                  type="number"
                  step="0.01"
                  min="0"
                  className="input"
                  value={edicao.amount}
                  onChange={(e) => setEdicao({ ...edicao, amount: e.target.value })}
                />
              </Campo>
            </div>
            <Campo rotulo="Mês de competência">
              <input
                required
                type="month"
                className="input"
                value={edicao.competence_month.slice(0, 7)}
                onChange={(e) =>
                  setEdicao({ ...edicao, competence_month: `${e.target.value}-01` })
                }
              />
            </Campo>
            <label className="flex items-center gap-2 text-xs text-ink-soft">
              <input
                type="checkbox"
                checked={edicao.is_recurring}
                onChange={(e) => setEdicao({ ...edicao, is_recurring: e.target.checked })}
              />
              Recorrente — entra na replicação para o próximo mês
            </label>
            {salvar.isError && <ErroBox erro={salvar.error} />}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" className="btn text-xs" onClick={() => setEdicao(null)}>
                Cancelar
              </button>
              <button type="submit" className="btn btn-primary text-xs" disabled={salvar.isPending}>
                {salvar.isPending ? 'Salvando…' : 'Salvar'}
              </button>
            </div>
          </form>
        )}
      </Modal>
    </Secao>
  )
}
