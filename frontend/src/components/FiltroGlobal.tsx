/**
 * Barra de filtros compartilhada.
 *
 * O estado vive na URL (`?de=…&ate=…&canal=…`): assim qualquer visão é
 * compartilhável por link e sobrevive ao F5 — dois comportamentos que o usuário
 * espera de um painel e que um estado só em memória quebra.
 */
import { useSearchParams } from 'react-router-dom'

import { ROTULO_CANAL, diasAtras, isoDia } from '@/lib/format'
import type { Filtros } from '@/types/api'

const PRESETS = [
  { rotulo: 'Hoje', dias: 0 },
  { rotulo: '7 dias', dias: 7 },
  { rotulo: '30 dias', dias: 30 },
  { rotulo: '90 dias', dias: 90 },
  { rotulo: '12 meses', dias: 365 },
]

export function useFiltros(): [Filtros, (parcial: Partial<Filtros> & { dias?: number }) => void] {
  const [params, setParams] = useSearchParams()

  const de = params.get('de') ?? isoDia(diasAtras(30))
  const ate = params.get('ate') ?? isoDia(new Date())

  const filtros: Filtros = {
    inicio: `${de}T00:00:00Z`,
    fim: `${ate}T23:59:59Z`,
    channel: params.get('canal') ?? undefined,
    account_id: params.get('conta') ? Number(params.get('conta')) : undefined,
    status: params.get('status') ?? undefined,
    logistic_type: params.get('logistica') ?? undefined,
    state: params.get('uf') ?? undefined,
  }

  const atualizar = (parcial: Partial<Filtros> & { dias?: number }) => {
    const novo = new URLSearchParams(params)
    if (parcial.dias !== undefined) {
      novo.set('de', isoDia(diasAtras(parcial.dias)))
      novo.set('ate', isoDia(new Date()))
    }
    const mapa: Record<string, string> = {
      channel: 'canal',
      status: 'status',
      logistic_type: 'logistica',
      state: 'uf',
    }
    for (const [campo, chave] of Object.entries(mapa)) {
      if (campo in parcial) {
        const valor = (parcial as Record<string, unknown>)[campo]
        if (valor) novo.set(chave, String(valor))
        else novo.delete(chave)
      }
    }
    if (parcial.inicio) novo.set('de', parcial.inicio.slice(0, 10))
    if (parcial.fim) novo.set('ate', parcial.fim.slice(0, 10))
    setParams(novo, { replace: true })
  }

  return [filtros, atualizar]
}

export function FiltroGlobal({ mostrarCanal = true }: { mostrarCanal?: boolean }) {
  const [params] = useSearchParams()
  const [filtros, atualizar] = useFiltros()

  const de = params.get('de') ?? filtros.inicio?.slice(0, 10) ?? ''
  const ate = params.get('ate') ?? filtros.fim?.slice(0, 10) ?? ''
  const canalAtual = params.get('canal') ?? ''

  // Filtros numa linha só, acima dos gráficos.
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex overflow-hidden rounded-lg border border-line">
        {PRESETS.map((p) => (
          <button
            key={p.rotulo}
            type="button"
            onClick={() => atualizar({ dias: p.dias })}
            className="border-r border-line px-3 py-1.5 text-xs font-medium text-ink-soft last:border-r-0 hover:bg-surface-raised"
          >
            {p.rotulo}
          </button>
        ))}
      </div>

      <label className="flex items-center gap-1.5 text-xs text-ink-muted">
        De
        <input
          type="date"
          value={de}
          onChange={(e) => atualizar({ inicio: `${e.target.value}T00:00:00Z` })}
          className="input py-1"
        />
      </label>
      <label className="flex items-center gap-1.5 text-xs text-ink-muted">
        até
        <input
          type="date"
          value={ate}
          onChange={(e) => atualizar({ fim: `${e.target.value}T23:59:59Z` })}
          className="input py-1"
        />
      </label>

      {mostrarCanal && (
        <select
          value={canalAtual}
          onChange={(e) => atualizar({ channel: e.target.value })}
          className="input py-1"
          aria-label="Marketplace"
        >
          <option value="">Todos os marketplaces</option>
          {Object.entries(ROTULO_CANAL).map(([valor, rotulo]) => (
            <option key={valor} value={valor}>
              {rotulo}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
