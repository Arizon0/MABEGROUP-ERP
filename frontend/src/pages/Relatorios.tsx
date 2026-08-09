import { useState } from 'react'

import { urlExportacao } from '@/api/client'
import { usePorEstado, useRankingProdutos, useSerie } from '@/api/queries'
import { GraficoEstados, GraficoReceita } from '@/components/charts'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import { Carregando, Secao, Tabela } from '@/components/ui'
import { brl, inteiro, pct } from '@/lib/format'

type Granularidade = 'hour' | 'day' | 'month'

export function Relatorios() {
  const [filtros] = useFiltros()
  const [granularidade, setGranularidade] = useState<Granularidade>('day')

  const serie = useSerie(filtros, granularidade)
  const produtos = useRankingProdutos(filtros, 50)
  const estados = usePorEstado(filtros)

  const parametrosExport = { inicio: filtros.inicio, fim: filtros.fim, channel: filtros.channel }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <FiltroGlobal />
        <div className="flex gap-2">
          <a className="btn text-xs" href={urlExportacao('/reports/orders/export', { ...parametrosExport, formato: 'csv' })}>
            ↓ Pedidos (CSV)
          </a>
          <a className="btn text-xs" href={urlExportacao('/reports/orders/export', { ...parametrosExport, formato: 'xlsx' })}>
            ↓ Pedidos (Excel)
          </a>
          <a className="btn text-xs" href={urlExportacao('/reports/financial/export', parametrosExport)}>
            ↓ Financeiro
          </a>
          <a className="btn text-xs" href={urlExportacao('/reports/reconciliation/export')}>
            ↓ Divergências
          </a>
        </div>
      </div>

      <Secao
        titulo="Evolução da receita"
        descricao="Selecione a granularidade conforme o período analisado."
        acao={
          <div className="flex overflow-hidden rounded-lg border border-line text-xs">
            {(
              [
                ['hour', 'Hora'],
                ['day', 'Dia'],
                ['month', 'Mês'],
              ] as [Granularidade, string][]
            ).map(([valor, rotulo]) => (
              <button
                key={valor}
                type="button"
                onClick={() => setGranularidade(valor)}
                className={`border-r border-line px-3 py-1.5 last:border-r-0 ${
                  granularidade === valor
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-ink-soft hover:bg-surface-raised'
                }`}
              >
                {rotulo}
              </button>
            ))}
          </div>
        }
      >
        {serie.isLoading ? <Carregando altura="h-64" /> : <GraficoReceita dados={serie.data ?? []} />}
      </Secao>

      <div className="grid gap-4 xl:grid-cols-2">
        <Secao
          titulo="Distribuição geográfica"
          descricao="Por estado de destino — a granularidade que as APIs oficiais liberam para todos os tipos de envio."
        >
          {estados.isLoading ? <Carregando /> : <GraficoEstados dados={estados.data ?? []} />}
        </Secao>

        <Secao titulo="Receita por estado" descricao="Mesma informação em tabela, para leitura exata e exportação.">
          <Tabela colunas={['UF', 'Pedidos', 'Receita bruta']} vazio={(estados.data ?? []).length === 0}>
            {(estados.data ?? []).slice(0, 12).map((e) => (
              <tr key={e.estado}>
                <td className="td">{e.estado}</td>
                <td className="td num">{inteiro(e.pedidos)}</td>
                <td className="td num">{brl(e.receita_bruta)}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>
      </div>

      <Secao
        titulo="Ranking completo de produtos"
        descricao="Consolidado por SKU base, com margem calculada sobre o custo congelado na venda."
      >
        {produtos.isLoading ? (
          <Carregando altura="h-64" />
        ) : (
          <Tabela
            colunas={['#', 'SKU', 'Produto', 'Pedidos', 'Unidades', 'Receita', 'CMV', 'Margem bruta', 'Margem %']}
            vazio={(produtos.data ?? []).length === 0}
          >
            {(produtos.data ?? []).map((p, i) => (
              <tr key={p.sku} className="hover:bg-surface-raised">
                <td className="td num text-ink-muted">{i + 1}</td>
                <td className="td num font-medium">{p.sku}</td>
                <td className="td max-w-[280px] truncate" title={p.titulo}>
                  {p.titulo}
                </td>
                <td className="td num">{inteiro(p.pedidos)}</td>
                <td className="td num">{inteiro(p.unidades)}</td>
                <td className="td num">{brl(p.receita_bruta)}</td>
                <td className="td num">{brl(p.cmv)}</td>
                <td className="td num">{brl(p.margem_bruta)}</td>
                <td className="td num">{p.margem_pct === '—' ? '—' : pct(p.margem_pct)}</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>
    </div>
  )
}
