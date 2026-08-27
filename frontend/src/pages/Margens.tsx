/**
 * Margem por pedido — a análise venda-a-venda.
 *
 * Cada linha responde "este pedido deu lucro?" com todos os custos na mesa:
 * margem = líquido − CMV − Ads − imposto do vendedor. Líquido, CMV e imposto
 * vêm congelados do pedido; o investimento em Ads é lançado por competência
 * no painel desta tela e rateado proporcionalmente à receita.
 */
import { useMemo, useState } from 'react'

import {
  useAdSpends,
  useMargens,
  useRemoverAdSpend,
  useSalvarAdSpend,
} from '@/api/queries'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import {
  AvisoQualidade,
  BotaoExcluir,
  Campo,
  Carregando,
  ErroBox,
  Secao,
  SeloCanal,
  Tabela,
  Vazio,
} from '@/components/ui'
import { brl, data, inteiro, num, pct, ROTULO_CANAL } from '@/lib/format'
import type {
  AlertaMargem,
  EscopoAds,
  OrdemMargem,
  PedidoMargem,
  RecorteMargem,
} from '@/types/api'

/** Acima disto a margem é saudável (verde); abaixo, apertada (âmbar). */
const MARGEM_SAUDAVEL = 10

const RECORTES: { valor: RecorteMargem; rotulo: string; aviso?: boolean }[] = [
  { valor: 'todos', rotulo: 'Todos' },
  { valor: 'negativos', rotulo: 'Só negativos' },
  { valor: 'sem-custo', rotulo: 'Sem custo' },
  { valor: 'sem-comissao', rotulo: 'Sem comissão' },
  { valor: 'sem-frete', rotulo: 'Sem frete' },
  { valor: 'pacotes', rotulo: 'Vários itens' },
  { valor: 'revisar', rotulo: 'A revisar', aviso: true },
]

const ORDENACOES: { valor: OrdemMargem; rotulo: string }[] = [
  { valor: 'pior-margem-valor', rotulo: 'Pior margem R$' },
  { valor: 'pior-margem-pct', rotulo: 'Pior margem %' },
  { valor: 'melhor-margem-valor', rotulo: 'Melhor margem R$' },
  { valor: 'melhor-margem-pct', rotulo: 'Melhor margem %' },
  { valor: 'maior-venda', rotulo: 'Maior venda' },
  { valor: 'maior-frete', rotulo: 'Maior frete' },
  { valor: 'data', rotulo: 'Data' },
]

const EXPLICACAO_ALERTA: Record<AlertaMargem, string> = {
  sem_sku: 'Item sem SKU mapeado — o custo não pôde ser resolvido.',
  sem_custo: 'Produto sem custo cadastrado — a margem está otimista.',
  sem_comissao: 'O canal não informou comissão nesta venda.',
  liquido_diverge:
    'A reconstrução (bruto + frete cobrado − taxas − frete pago) não fecha com o líquido informado pelo canal.',
}

const MESES = [
  'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]

function Chip({
  ativo,
  rotulo,
  contagem,
  aviso,
  onClick,
}: {
  ativo: boolean
  rotulo: string
  contagem?: number
  aviso?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={ativo}
      className={`whitespace-nowrap rounded-full px-3 py-1 text-sm transition ${
        ativo
          ? 'bg-brand font-medium text-white'
          : 'text-ink-soft hover:bg-surface-2 hover:text-ink'
      }`}
    >
      {aviso && <span aria-hidden="true">⚠ </span>}
      {rotulo}
      {contagem !== undefined && contagem > 0 && (
        <span className={ativo ? 'ml-1.5 opacity-75' : 'ml-1.5 text-ink-muted'}>
          {inteiro(contagem)}
        </span>
      )}
    </button>
  )
}

/** A margem % como pastilha — a primeira leitura da linha. */
function PastilhaMargem({ valor }: { valor: string | null }) {
  if (valor === null) {
    return (
      <span
        className="inline-block rounded-full bg-surface-2 px-2 py-0.5 text-sm text-ink-muted"
        title="Pedido sem receita — não existe margem percentual."
      >
        —
      </span>
    )
  }
  const n = num(valor)
  const estilo =
    n < 0
      ? 'bg-[color-mix(in_srgb,var(--negative)_14%,transparent)] text-negative'
      : n < MARGEM_SAUDAVEL
        ? 'bg-[color-mix(in_srgb,var(--warning)_16%,transparent)] text-warning'
        : 'bg-[color-mix(in_srgb,var(--positive)_14%,transparent)] text-positive'
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-sm font-medium num ${estilo}`}>
      {pct(valor, 2)} <span aria-hidden="true">{n < 0 ? '↘' : '↗'}</span>
    </span>
  )
}

function Linha({ pedido }: { pedido: PedidoMargem }) {
  return (
    <tr className="border-b border-line align-top hover:bg-surface-2/50">
      <td className="td !whitespace-normal">
        <span className="font-medium text-ink">#{pedido.external_id}</span>
        <p className="line-clamp-1 text-xs text-ink-soft">{pedido.titulo || '—'}</p>
        <p className="text-xs text-ink-muted">
          {pedido.skus.length > 0 ? pedido.skus.join(' · ') : 'sem SKU'}
          {pedido.has_multiple_items && ' · pacote'}
        </p>
        {pedido.alertas.length > 0 && (
          <p className="mt-0.5 flex flex-wrap gap-1">
            {pedido.alertas.map((a) => (
              <span
                key={a}
                title={EXPLICACAO_ALERTA[a]}
                className="rounded bg-[color-mix(in_srgb,var(--warning)_16%,transparent)] px-1.5 py-0.5 text-[11px] text-warning"
              >
                ⚠ {a.replace(/_/g, ' ')}
              </span>
            ))}
          </p>
        )}
      </td>
      <td className="td num text-ink-soft">{data(pedido.date_created)}</td>
      <td className="td">
        <SeloCanal canal={pedido.channel} />
      </td>
      <td className="td">
        <PastilhaMargem valor={pedido.margem_pct} />
        <span className="block text-xs text-ink-muted num">{brl(pedido.margem_valor)}</span>
      </td>
      <td className="td num text-right font-medium">{brl(pedido.total)}</td>
      <td className="td num text-right">{brl(pedido.custo)}</td>
      <td className="td num text-right">{brl(pedido.frete)}</td>
      <td className="td num text-right">{brl(pedido.comissao)}</td>
      <td className="td num text-right">{brl(pedido.ads)}</td>
      <td
        className="td num text-right italic text-ink-muted"
        title={
          pedido.acos_pct === null
            ? 'O canal não informou a receita atribuída à publicidade.'
            : 'Investimento sobre a receita que o canal atribuiu à publicidade.'
        }
      >
        {pedido.acos_pct === null ? '—' : pct(pedido.acos_pct, 2)}
      </td>
      <td className="td num text-right" title="Investimento sobre a receita total do pedido.">
        {pedido.tacos_pct === null ? '—' : pct(pedido.tacos_pct, 2)}
      </td>
      <td className="td num text-right">{brl(pedido.imposto)}</td>
    </tr>
  )
}

const AGORA = new Date()

function PainelAds() {
  const lancamentos = useAdSpends()
  const salvar = useSalvarAdSpend()
  const remover = useRemoverAdSpend()

  const [form, setForm] = useState({
    channel: 'mercadolivre',
    year: AGORA.getFullYear(),
    month: AGORA.getMonth() + 1,
    scope: 'channel' as EscopoAds,
    reference: '',
    amount: '',
    attributed_revenue: '',
  })

  return (
    <Secao
      titulo="Investimento em publicidade"
      descricao="Nenhuma API entrega o custo de Ads por pedido. Lance aqui o investido do mês (do relatório de Product Ads) e ele será rateado entre os pedidos proporcionalmente à receita — anúncio antes de SKU, SKU antes do canal inteiro. A receita atribuída é opcional: sem ela há TACOS, não ACOS."
    >
      <form
        className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault()
          salvar.mutate(
            {
              channel: form.channel,
              year: form.year,
              month: form.month,
              scope: form.scope,
              reference: form.scope === 'channel' ? '' : form.reference,
              amount: form.amount || '0',
              attributed_revenue: form.attributed_revenue || null,
            },
            { onSuccess: () => setForm((f) => ({ ...f, reference: '', amount: '', attributed_revenue: '' })) },
          )
        }}
      >
        <Campo rotulo="Canal">
          <select
            className="input"
            value={form.channel}
            onChange={(e) => setForm({ ...form, channel: e.target.value })}
          >
            <option value="mercadolivre">Mercado Livre</option>
            <option value="shopee">Shopee</option>
          </select>
        </Campo>
        <Campo rotulo="Mês">
          <select
            className="input"
            value={form.month}
            onChange={(e) => setForm({ ...form, month: Number(e.target.value) })}
          >
            {MESES.map((m, i) => (
              <option key={m} value={i + 1}>{m}</option>
            ))}
          </select>
        </Campo>
        <Campo rotulo="Ano">
          <input
            type="number"
            className="input"
            value={form.year}
            onChange={(e) => setForm({ ...form, year: Number(e.target.value) })}
          />
        </Campo>
        <Campo rotulo="Escopo" dica="Do mais específico ao mais amplo.">
          <select
            className="input"
            value={form.scope}
            onChange={(e) => setForm({ ...form, scope: e.target.value as EscopoAds })}
          >
            <option value="channel">Canal inteiro</option>
            <option value="listing">Anúncio</option>
            <option value="sku">SKU</option>
          </select>
        </Campo>
        <Campo rotulo="Referência" dica={form.scope === 'listing' ? 'Id do anúncio (MLB…).' : form.scope === 'sku' ? 'SKU base.' : 'Não se aplica ao canal inteiro.'}>
          <input
            className="input disabled:opacity-50"
            disabled={form.scope === 'channel'}
            value={form.reference}
            onChange={(e) => setForm({ ...form, reference: e.target.value })}
          />
        </Campo>
        <Campo rotulo="Investido R$">
          <input
            className="input"
            inputMode="decimal"
            placeholder="0,00"
            value={form.amount}
            onChange={(e) => setForm({ ...form, amount: e.target.value.replace(',', '.') })}
          />
        </Campo>
        <Campo rotulo="Receita atribuída R$" dica="Do relatório de Ads — habilita o ACOS.">
          <input
            className="input"
            inputMode="decimal"
            placeholder="opcional"
            value={form.attributed_revenue}
            onChange={(e) =>
              setForm({ ...form, attributed_revenue: e.target.value.replace(',', '.') })
            }
          />
        </Campo>
        <div className="flex items-end">
          <button type="submit" className="btn btn-primary w-full" disabled={salvar.isPending}>
            {salvar.isPending ? 'Salvando…' : 'Lançar'}
          </button>
        </div>
      </form>

      {salvar.isError && <ErroBox erro={salvar.error} />}

      <Tabela
        colunas={['Competência', 'Canal', 'Escopo', 'Investido', 'Receita ads', '']}
        vazio={(lancamentos.data ?? []).length === 0}
      >
        {(lancamentos.data ?? []).map((l) => (
          <tr key={l.id} className="border-b border-line">
            <td className="td">{MESES[l.month - 1]}/{l.year}</td>
            <td className="td">{ROTULO_CANAL[l.channel] ?? l.channel}</td>
            <td className="td text-ink-soft">
              {l.scope === 'channel' ? 'canal inteiro' : `${l.scope === 'listing' ? 'anúncio' : 'SKU'} ${l.reference}`}
            </td>
            <td className="td num">{brl(l.amount)}</td>
            <td className="td num text-ink-soft">
              {l.attributed_revenue ? brl(l.attributed_revenue) : '—'}
            </td>
            <td className="td text-right">
              <BotaoExcluir aoConfirmar={() => remover.mutate(l.id)} />
            </td>
          </tr>
        ))}
      </Tabela>
    </Secao>
  )
}

export function Margens() {
  const [filtros] = useFiltros()
  const [recorte, setRecorte] = useState<RecorteMargem>('todos')
  const [ordem, setOrdem] = useState<OrdemMargem>('data')
  const [busca, setBusca] = useState('')
  const [buscaAtiva, setBuscaAtiva] = useState('')
  const [pagina, setPagina] = useState(1)

  const consulta = useMargens(filtros, {
    recorte,
    ordem,
    busca: buscaAtiva || undefined,
    pagina,
  })

  const resumo = consulta.data?.resumo
  const paginacao = consulta.data?.paginacao

  const avisos = useMemo(() => {
    const lista: string[] = []
    if (!resumo || resumo.pedidos === 0) return lista
    if (num(resumo.imposto) === 0) {
      lista.push(
        'Nenhum imposto está sendo aplicado. Configure a regra tributária em Custos e lucro — sem ela a margem aparece maior do que é.',
      )
    }
    if (num(resumo.ads) === 0) {
      lista.push(
        'Nenhum investimento em publicidade lançado — ACOS e TACOS ficam vazios e a margem ignora o custo de Ads.',
      )
    }
    if (num(resumo.ads_nao_alocado) > 0) {
      lista.push(
        `${brl(resumo.ads_nao_alocado)} de publicidade não foram rateados: o anúncio ou SKU lançado não teve venda na competência. Esse custo está fora das margens exibidas.`,
      )
    }
    return lista
  }, [resumo])

  function trocar(mudanca: () => void) {
    mudanca()
    setPagina(1) // a página antiga não existe no novo recorte
  }

  if (consulta.isError) return <ErroBox erro={consulta.error} />

  return (
    <div className="space-y-4">
      <FiltroGlobal />

      <Secao
        titulo="Margem por pedido"
        descricao="margem = líquido recebido − custo do produto − publicidade − imposto do regime. Comissão e frete explicam o líquido; não são subtraídos de novo."
      >
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Recorte
        </div>
        <div className="mb-3 flex flex-wrap gap-1">
          {RECORTES.map((r) => (
            <Chip
              key={r.valor}
              rotulo={r.rotulo}
              aviso={r.aviso}
              ativo={recorte === r.valor}
              contagem={consulta.data?.contagem_por_recorte?.[r.valor]}
              onClick={() => trocar(() => setRecorte(r.valor))}
            />
          ))}
        </div>

        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Ordenar por
        </div>
        <div className="mb-3 flex flex-wrap gap-1">
          {ORDENACOES.map((o) => (
            <Chip
              key={o.valor}
              rotulo={o.rotulo}
              ativo={ordem === o.valor}
              onClick={() => trocar(() => setOrdem(o.valor))}
            />
          ))}
        </div>

        <form
          className="mb-3 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            trocar(() => setBuscaAtiva(busca.trim()))
          }}
        >
          <input
            className="input min-w-[16rem] flex-1"
            placeholder="Buscar por pedido, SKU ou título"
            aria-label="Buscar pedidos"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
          <button type="submit" className="btn">Buscar</button>
          {buscaAtiva && (
            <button
              type="button"
              className="btn"
              onClick={() => trocar(() => { setBusca(''); setBuscaAtiva('') })}
            >
              Limpar
            </button>
          )}
        </form>

        {avisos.map((aviso) => (
          <AvisoQualidade key={aviso} texto={aviso} />
        ))}

        {resumo && paginacao && (
          <p role="status" aria-label="Resumo do recorte" className="mb-2 text-sm text-ink-soft">
            <strong className="text-ink">{inteiro(resumo.pedidos)}</strong> pedidos
            {' · '}
            <span className={resumo.negativos > 0 ? 'text-negative' : undefined}>
              {inteiro(resumo.negativos)} negativos ({pct(resumo.pct_negativos, 0)})
            </span>
            {resumo.para_revisar > 0 && (
              <span className="text-warning"> · ⚠ {inteiro(resumo.para_revisar)} a revisar</span>
            )}
            {paginacao.total > 0 && <> · mostrando {paginacao.de}–{paginacao.ate}</>}
            {' · '}margem do recorte <strong className="num">{brl(resumo.margem_valor)}</strong>{' '}
            ({pct(resumo.margem_pct, 2)})
          </p>
        )}

        {consulta.isLoading ? (
          <Carregando altura="h-64" />
        ) : (consulta.data?.pedidos ?? []).length === 0 ? (
          <Vazio
            titulo="Nenhum pedido neste recorte"
            descricao="Ajuste o período no filtro acima ou escolha outro recorte."
          />
        ) : (
          <Tabela
            colunas={[
              'Pedido', 'Data', 'Canal', 'Margem %', 'Total', 'Custo', 'Frete',
              'Comissão', 'Ads', 'ACOS', 'TACOS', 'Imposto',
            ]}
          >
            {(consulta.data?.pedidos ?? []).map((p) => (
              <Linha key={`${p.channel}-${p.external_id}`} pedido={p} />
            ))}
          </Tabela>
        )}

        {paginacao && paginacao.paginas > 1 && (
          <nav className="mt-3 flex items-center justify-center gap-3" aria-label="Paginação">
            <button
              type="button"
              className="btn disabled:opacity-40"
              disabled={paginacao.pagina <= 1}
              onClick={() => setPagina((p) => p - 1)}
            >
              Anterior
            </button>
            <span className="text-sm text-ink-soft">
              Página {paginacao.pagina} de {paginacao.paginas}
            </span>
            <button
              type="button"
              className="btn disabled:opacity-40"
              disabled={paginacao.pagina >= paginacao.paginas}
              onClick={() => setPagina((p) => p + 1)}
            >
              Próxima
            </button>
          </nav>
        )}
      </Secao>

      <PainelAds />
    </div>
  )
}
