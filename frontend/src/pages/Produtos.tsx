import { useState } from 'react'

import { useAnuncios, useMapearSku, usePendencias, useProdutos, useSaudeEstoque } from '@/api/queries'
import { Carregando, ErroBox, KpiCard, SeloCanal, Secao, Tabela, Vazio } from '@/components/ui'
import { brl, data, inteiro, pct } from '@/lib/format'

export function Produtos() {
  const [apenasRuptura, setApenasRuptura] = useState(false)
  const [busca, setBusca] = useState('')

  const anuncios = useAnuncios({ apenas_ruptura: apenasRuptura, busca: busca || undefined })
  const saude = useSaudeEstoque(30)
  const pendencias = usePendencias()
  const produtos = useProdutos()
  const mapear = useMapearSku()

  if (anuncios.isError) return <ErroBox erro={anuncios.error} />

  const r = saude.data?.resumo

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          rotulo="Em ruptura"
          valor={r?.ruptura ?? 0}
          formato="numero"
          dica="Estoque zerado com venda nos últimos 30 dias."
        />
        <KpiCard
          rotulo="Estoque crítico"
          valor={r?.criticos ?? 0}
          formato="numero"
          dica="Menos de 7 dias de cobertura pelo giro atual."
        />
        <KpiCard rotulo="Parados" valor={r?.parados ?? 0} formato="numero" dica="Com estoque e sem venda no período." />
        <KpiCard rotulo="Saudáveis" valor={r?.saudaveis ?? 0} formato="numero" />
      </div>

      {(saude.data?.ruptura ?? []).length > 0 && (
        <Secao
          titulo="Ruptura — atenção imediata"
          descricao="Produtos com histórico de venda e estoque zerado. Cada dia parado é receita perdida."
        >
          <Tabela colunas={['Canal', 'SKU', 'Produto', 'Vendas 30d', 'Média/dia']}>
            {(saude.data?.ruptura ?? []).slice(0, 15).map((l) => (
              <tr key={l.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={l.channel} />
                </td>
                <td className="td num text-xs">{l.sku_channel || '—'}</td>
                <td className="td max-w-[320px] truncate">{l.title}</td>
                <td className="td num">{inteiro(l.vendas_periodo)}</td>
                <td className="td num">{l.media_diaria}</td>
              </tr>
            ))}
          </Tabela>
        </Secao>
      )}

      <Secao
        titulo="Anúncios"
        descricao="Catálogo consolidado de todos os canais conectados."
        acao={
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              placeholder="Buscar…"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              className="input py-1 text-xs"
            />
            <label className="flex items-center gap-1.5 text-xs text-ink-soft">
              <input
                type="checkbox"
                checked={apenasRuptura}
                onChange={(e) => setApenasRuptura(e.target.checked)}
              />
              Só ruptura
            </label>
          </div>
        }
      >
        {anuncios.isLoading ? (
          <Carregando altura="h-64" />
        ) : (
          <Tabela
            colunas={['Canal', 'SKU', 'Anúncio', 'Preço', 'Estoque', 'Vendidos', 'Visitas 30d', 'Conversão']}
            vazio={(anuncios.data?.itens ?? []).length === 0}
          >
            {(anuncios.data?.itens ?? []).map((a) => (
              <tr key={a.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={a.channel} />
                </td>
                <td className="td num text-xs">{a.sku_channel || '—'}</td>
                <td className="td max-w-[300px] truncate" title={a.title}>
                  {a.title}
                </td>
                <td className="td num">{brl(a.price)}</td>
                <td className={`td num ${a.em_ruptura ? 'font-semibold text-bad' : ''}`}>
                  {a.em_ruptura ? '0 · ruptura' : inteiro(a.available_quantity)}
                </td>
                <td className="td num">{inteiro(a.sold_quantity)}</td>
                <td className="td num">{inteiro(a.visits_30d)}</td>
                <td className="td num">{a.conversao_pct === '—' ? '—' : pct(a.conversao_pct)}</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>

      <Secao
        titulo="SKUs sem de-para"
        descricao="Códigos vistos na importação sem produto correspondente. A pendência não bloqueia o pedido — só deixa a margem daquele item indisponível."
      >
        {pendencias.isLoading ? (
          <Carregando />
        ) : (pendencias.data ?? []).length === 0 ? (
          <Vazio titulo="Nenhuma pendência" descricao="Todos os SKUs importados estão mapeados a um produto interno." />
        ) : (
          <Tabela colunas={['Canal', 'SKU do canal', 'Exemplo', 'Ocorrências', 'Visto em', 'Vincular a']}>
            {(pendencias.data ?? []).map((p) => (
              <tr key={p.id} className="hover:bg-surface-raised">
                <td className="td">
                  <SeloCanal canal={p.channel} />
                </td>
                <td className="td num font-medium">{p.sku_channel}</td>
                <td className="td max-w-[260px] truncate text-xs">{p.sample_title}</td>
                <td className="td num">{inteiro(p.occurrences)}</td>
                <td className="td text-xs">{data(p.last_seen_at)}</td>
                <td className="td">
                  <select
                    className="input py-1 text-xs"
                    defaultValue=""
                    disabled={mapear.isPending}
                    onChange={(e) => {
                      if (!e.target.value) return
                      mapear.mutate({
                        channel: p.channel,
                        sku_channel: p.sku_channel,
                        product_id: Number(e.target.value),
                      })
                    }}
                  >
                    <option value="">Selecionar produto…</option>
                    {(produtos.data ?? []).map((prod) => (
                      <option key={prod.id} value={prod.id}>
                        {prod.sku} — {prod.name}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </Tabela>
        )}
        {mapear.isSuccess && (
          <p className="mt-2 text-xs text-good">
            {mapear.data?.mensagem} — {mapear.data?.dados?.itens_atualizados ?? 0} itens e{' '}
            {mapear.data?.dados?.pedidos_recalculados ?? 0} pedidos recalculados com o custo do produto.
          </p>
        )}
      </Secao>
    </div>
  )
}
