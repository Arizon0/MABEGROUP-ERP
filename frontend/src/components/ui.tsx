/** Peças de interface reutilizadas pelas dez abas. */
import type { ReactNode } from 'react'

import { ROTULO_CANAL, ROTULO_STATUS, brl, corDoCanal, num, pct } from '@/lib/format'

// --- Cartão de KPI -----------------------------------------------------------

export function KpiCard({
  rotulo,
  valor,
  variacao,
  formato = 'texto',
  dica,
}: {
  rotulo: string
  valor: string | number
  variacao?: string
  formato?: 'moeda' | 'numero' | 'texto' | 'percentual'
  dica?: string
}) {
  const exibicao =
    formato === 'moeda'
      ? brl(valor)
      : formato === 'percentual'
        ? pct(valor)
        : formato === 'numero'
          ? num(valor).toLocaleString('pt-BR')
          : String(valor)

  const delta = variacao !== undefined ? num(variacao) : null
  // Cor de estado só entra acompanhada do sinal: a seta carrega a informação
  // mesmo para quem não distingue as cores.
  const corDelta =
    delta === null || Math.abs(delta) < 0.05
      ? 'text-ink-muted'
      : delta > 0
        ? 'text-good'
        : 'text-bad'

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-2">
        <span className="card-sub">{rotulo}</span>
        {dica && <span className="text-[10px] text-ink-muted" title={dica}>ⓘ</span>}
      </div>
      <div className="num mt-1 text-2xl font-semibold text-ink">{exibicao}</div>
      {delta !== null && (
        <div className={`mt-1 text-xs ${corDelta}`}>
          {delta > 0 ? '▲' : delta < 0 ? '▼' : '■'} {pct(Math.abs(delta))} vs. período anterior
        </div>
      )}
    </div>
  )
}

// --- Selos -------------------------------------------------------------------

export function SeloCanal({ canal }: { canal: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-ink">
      <span
        aria-hidden
        className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
        style={{ background: corDoCanal(canal) }}
      />
      {ROTULO_CANAL[canal] ?? canal}
    </span>
  )
}

const ESTILO_STATUS: Record<string, string> = {
  delivered: 'border-good-line bg-good-soft text-good',
  paid: 'border-brand-line bg-brand-soft text-brand',
  shipped: 'border-brand-line bg-brand-soft text-brand',
  processing: 'border-warn-line bg-warn-soft text-warn',
  pending: 'border-warn-line bg-warn-soft text-warn',
  cancelled: 'border-bad-line bg-bad-soft text-bad',
  returned: 'border-bad-line bg-bad-soft text-bad',
}

export function SeloStatus({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded-md border px-2 py-0.5 text-xs font-medium ${
        ESTILO_STATUS[status] ?? 'border-line bg-surface-raised text-ink-soft'
      }`}
    >
      {ROTULO_STATUS[status] ?? status}
    </span>
  )
}

/**
 * Procedência do valor líquido — sempre visível ao lado do número.
 * Um painel que mistura estimativa com valor liquidado sem distinguir faz o
 * usuário confiar num número que não bate com o extrato dele.
 */
export function SeloProcedencia({ fonte }: { fonte: string }) {
  const mapa: Record<string, { texto: string; classe: string; titulo: string }> = {
    settled: {
      texto: 'Liquidado',
      classe: 'border-good-line bg-good-soft text-good',
      titulo: 'Confirmado por repasse: o dinheiro caiu na conta.',
    },
    api_reported: {
      texto: 'Informado',
      classe: 'border-brand-line bg-brand-soft text-brand',
      titulo: 'O canal informou o líquido, mas ainda não liberou o valor.',
    },
    computed: {
      texto: 'Estimado',
      classe: 'border-warn-line bg-warn-soft text-warn',
      titulo: 'Calculado pelo sistema a partir das taxas conhecidas.',
    },
  }
  const item = mapa[fonte] ?? {
    texto: fonte,
    classe: 'border-line text-ink-muted',
    titulo: '',
  }
  return (
    <span
      title={item.titulo}
      className={`inline-block rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${item.classe}`}
    >
      {item.texto}
    </span>
  )
}

// --- Estruturais -------------------------------------------------------------

export function Secao({
  titulo,
  descricao,
  acao,
  children,
}: {
  titulo: string
  descricao?: string
  acao?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="card">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="card-title">{titulo}</h2>
          {descricao && <p className="card-sub mt-0.5">{descricao}</p>}
        </div>
        {acao}
      </header>
      {children}
    </section>
  )
}

export function Carregando({ altura = 'h-40' }: { altura?: string }) {
  return (
    <div
      className={`${altura} animate-pulse rounded-lg bg-surface-raised`}
      role="status"
      aria-label="Carregando"
    />
  )
}

export function Vazio({ titulo, descricao }: { titulo: string; descricao?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line py-10 text-center">
      <p className="text-sm font-medium text-ink-soft">{titulo}</p>
      {descricao && <p className="mt-1 max-w-md text-xs text-ink-muted">{descricao}</p>}
    </div>
  )
}

export function ErroBox({ erro }: { erro: unknown }) {
  const mensagem = erro instanceof Error ? erro.message : 'Falha ao carregar os dados.'
  return (
    <div className="rounded-lg border border-bad-line bg-bad-soft p-4 text-sm text-bad">{mensagem}</div>
  )
}

export function Tabela({
  colunas,
  children,
  vazio,
}: {
  colunas: string[]
  children: ReactNode
  vazio?: boolean
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse">
        <thead className="border-b border-line">
          <tr>
            {colunas.map((c) => (
              <th key={c} className="th">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {vazio ? (
            <tr>
              <td className="td text-ink-muted" colSpan={colunas.length}>
                Nenhum registro no período selecionado.
              </td>
            </tr>
          ) : (
            children
          )}
        </tbody>
      </table>
    </div>
  )
}
