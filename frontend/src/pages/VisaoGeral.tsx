import { useRankingProdutos, useSerie, usePorCanal, useVisaoGeral } from '@/api/queries'
import { GraficoCanais, GraficoReceita } from '@/components/charts'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import { Carregando, ErroBox, KpiCard, SeloCanal, Secao, Tabela } from '@/components/ui'
import { ROTULO_STATUS, brl, inteiro, pct } from '@/lib/format'

export function VisaoGeralPage() {
  const [filtros] = useFiltros()
  const geral = useVisaoGeral(filtros)
  const serie = useSerie(filtros, 'day')
  const canais = usePorCanal(filtros)
  const produtos = useRankingProdutos(filtros, 10)

  if (geral.isError) return <ErroBox erro={geral.error} />

  const k = geral.data?.kpis
  const d = geral.data?.derivados

  return (
    <div className="space-y-4">
      <FiltroGlobal />

      {geral.isLoading || !k ? (
        <Carregando altura="h-24" />
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
          <KpiCard rotulo="Pedidos" valor={k.pedidos.valor} variacao={k.pedidos.variacao_pct} formato="numero" />
          <KpiCard rotulo="Unidades" valor={k.unidades.valor} variacao={k.unidades.variacao_pct} formato="numero" />
          <KpiCard rotulo="Receita bruta" valor={k.receita_bruta.valor} variacao={k.receita_bruta.variacao_pct} formato="moeda" />
          <KpiCard
            rotulo="Receita líquida"
            valor={k.receita_liquida.valor}
            variacao={k.receita_liquida.variacao_pct}
            formato="moeda"
            dica="Bruto menos comissões, taxas de pagamento e frete."
          />
          <KpiCard
            rotulo="Taxas descontadas"
            valor={k.taxas.valor}
            variacao={k.taxas.variacao_pct}
            formato="moeda"
            dica={`Equivale a ${pct(d?.taxa_efetiva_pct ?? 0)} do faturamento bruto.`}
          />
          <KpiCard rotulo="Ticket médio" valor={k.ticket_medio.valor} variacao={k.ticket_medio.variacao_pct} formato="moeda" />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <Secao
            titulo="Receita bruta e líquida por dia"
            descricao="A distância entre as duas linhas é o peso das taxas e do frete sobre o faturamento."
          >
            {serie.isLoading ? <Carregando altura="h-64" /> : <GraficoReceita dados={serie.data ?? []} />}
          </Secao>
        </div>

        <Secao titulo="Participação por marketplace" descricao="Receita bruta no período.">
          {canais.isLoading ? <Carregando altura="h-56" /> : <GraficoCanais dados={canais.data ?? []} />}
        </Secao>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Secao titulo="Funil operacional" descricao="Distribuição dos pedidos por status.">
          <ul className="space-y-2">
            {Object.entries(geral.data?.por_status ?? {})
              .filter(([, qtd]) => qtd > 0)
              .sort((a, b) => b[1] - a[1])
              .map(([status, qtd]) => {
                const total = Object.values(geral.data?.por_status ?? {}).reduce((s, v) => s + v, 0)
                const largura = total ? (qtd / total) * 100 : 0
                return (
                  <li key={status}>
                    <div className="mb-1 flex items-baseline justify-between text-xs">
                      <span className="text-ink-soft">{ROTULO_STATUS[status] ?? status}</span>
                      <span className="num text-ink">{inteiro(qtd)}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${largura}%`,
                          background: status === 'cancelled' ? 'var(--status-critical)' : 'var(--series-1)',
                        }}
                      />
                    </div>
                  </li>
                )
              })}
          </ul>
          <dl className="mt-4 grid grid-cols-2 gap-2 border-t border-line pt-3 text-xs">
            <div>
              <dt className="text-ink-muted">Taxa de cancelamento</dt>
              <dd className="num text-ink">{pct(d?.taxa_cancelamento_pct ?? 0)}</dd>
            </div>
            <div>
              <dt className="text-ink-muted">Margem de contribuição</dt>
              <dd className="num text-ink">{brl(d?.margem_contribuicao ?? 0)}</dd>
            </div>
          </dl>
        </Secao>

        <div className="xl:col-span-2">
          <Secao
            titulo="Top produtos por receita"
            descricao="Consolidado por SKU base — o mesmo produto em vários anúncios e canais aparece numa linha só."
          >
            {produtos.isLoading ? (
              <Carregando />
            ) : (
              <Tabela
                colunas={['SKU', 'Produto', 'Unid.', 'Receita', 'Margem']}
                vazio={(produtos.data ?? []).length === 0}
              >
                {(produtos.data ?? []).map((p) => (
                  <tr key={p.sku} className="hover:bg-surface-raised">
                    <td className="td num font-medium">{p.sku}</td>
                    <td className="td max-w-[280px] truncate" title={p.titulo}>
                      {p.titulo}
                    </td>
                    <td className="td num">{inteiro(p.unidades)}</td>
                    <td className="td num">{brl(p.receita_bruta)}</td>
                    <td className="td num">{p.margem_pct === '—' ? '—' : pct(p.margem_pct)}</td>
                  </tr>
                ))}
              </Tabela>
            )}
          </Secao>
        </div>
      </div>

      <Secao titulo="Comparativo entre marketplaces" descricao="Números do período por canal.">
        <Tabela
          colunas={['Marketplace', 'Pedidos', 'Receita bruta', 'Receita líquida', 'Taxas', 'Taxa efetiva', 'Ticket médio']}
          vazio={(canais.data ?? []).length === 0}
        >
          {(canais.data ?? []).map((c) => (
            <tr key={c.channel} className="hover:bg-surface-raised">
              <td className="td">
                <SeloCanal canal={c.channel} />
              </td>
              <td className="td num">{inteiro(c.pedidos)}</td>
              <td className="td num">{brl(c.receita_bruta)}</td>
              <td className="td num">{brl(c.receita_liquida)}</td>
              <td className="td num">{brl(c.taxas)}</td>
              <td className="td num">{pct(c.taxa_efetiva_pct)}</td>
              <td className="td num">{brl(c.ticket_medio)}</td>
            </tr>
          ))}
        </Tabela>
      </Secao>
    </div>
  )
}
