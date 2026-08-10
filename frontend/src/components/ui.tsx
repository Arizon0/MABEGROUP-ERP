/** Peças de interface reutilizadas pelas dez abas. */
import { useEffect, useRef, useState } from 'react'
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

// --- Formulários e ações destrutivas -----------------------------------------

export function Campo({
  rotulo,
  dica,
  children,
}: {
  rotulo: string
  dica?: string
  children: ReactNode
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-ink-soft">{rotulo}</span>
      {children}
      {dica && <span className="text-[11px] text-ink-muted">{dica}</span>}
    </label>
  )
}

/**
 * Diálogo modal simples.
 *
 * Usa `<dialog>` nativo pelo que ele traz de graça e que uma div não tem:
 * foco preso dentro do diálogo, fechamento no `Esc` e semântica de modal para
 * leitor de tela.
 */
export function Modal({
  aberto,
  titulo,
  descricao,
  aoFechar,
  children,
}: {
  aberto: boolean
  titulo: string
  descricao?: string
  aoFechar: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialogo = ref.current
    if (!dialogo) return
    if (aberto && !dialogo.open) dialogo.showModal()
    if (!aberto && dialogo.open) dialogo.close()
  }, [aberto])

  return (
    <dialog
      ref={ref}
      onCancel={(evento) => {
        evento.preventDefault()
        aoFechar()
      }}
      onClick={(evento) => {
        // Clique no backdrop (o próprio elemento, fora do conteúdo) fecha.
        if (evento.target === ref.current) aoFechar()
      }}
      className="w-[min(32rem,92vw)] rounded-xl border border-line bg-surface p-0 text-ink backdrop:bg-black/40"
    >
      <div className="border-b border-line px-4 py-3">
        <h3 className="card-title">{titulo}</h3>
        {descricao && <p className="card-sub mt-0.5">{descricao}</p>}
      </div>
      <div className="p-4">{children}</div>
    </dialog>
  )
}

/**
 * Botão de exclusão com confirmação embutida.
 *
 * A confirmação fica no próprio botão em vez de um `window.confirm`: o texto
 * diz o que será apagado, e o segundo clique exige mirar de novo — o bastante
 * para evitar o apagamento por reflexo sem transformar cada remoção em um
 * diálogo de duas telas.
 */
export function BotaoExcluir({
  aoConfirmar,
  rotulo = 'Excluir',
  confirmacao = 'Confirmar exclusão?',
  ocupado = false,
}: {
  aoConfirmar: () => void
  rotulo?: string
  confirmacao?: string
  ocupado?: boolean
}) {
  const [armado, setArmado] = useState(false)

  useEffect(() => {
    if (!armado) return
    // Some sozinho: um botão que fica armado indefinidamente vira armadilha
    // para o próximo clique, que já não lembra que estava confirmando.
    const t = setTimeout(() => setArmado(false), 4000)
    return () => clearTimeout(t)
  }, [armado])

  return (
    <button
      type="button"
      disabled={ocupado}
      className={`btn px-2 py-1 text-xs ${
        armado ? 'border-bad-line bg-bad-soft text-bad' : 'text-ink-soft'
      }`}
      onClick={() => {
        if (armado) {
          aoConfirmar()
          setArmado(false)
        } else {
          setArmado(true)
        }
      }}
    >
      {ocupado ? 'Removendo…' : armado ? confirmacao : rotulo}
    </button>
  )
}

/** Aviso de qualidade do dado: o número existe, mas não está completo. */
export function AvisoQualidade({ texto }: { texto: string }) {
  if (!texto) return null
  return (
    <div className="flex gap-2 rounded-lg border border-warn-line bg-warn-soft p-3 text-xs text-warn">
      <span aria-hidden>⚠</span>
      <p className="whitespace-pre-line">{texto}</p>
    </div>
  )
}
