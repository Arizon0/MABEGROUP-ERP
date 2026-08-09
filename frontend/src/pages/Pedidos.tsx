import { useState } from 'react'

import { usePedido, usePedidos } from '@/api/queries'
import { FiltroGlobal, useFiltros } from '@/components/FiltroGlobal'
import {
  Carregando,
  ErroBox,
  SeloCanal,
  SeloProcedencia,
  SeloStatus,
  Secao,
  Tabela,
} from '@/components/ui'
import { ROTULO_LOGISTICA, brl, dataHora, inteiro } from '@/lib/format'

export function Pedidos() {
  const [filtros] = useFiltros()
  const [busca, setBusca] = useState('')
  const [pagina, setPagina] = useState(0)
  const [selecionado, setSelecionado] = useState<number | null>(null)

  const limite = 50
  const lista = usePedidos({ ...filtros, busca: busca || undefined, limite, offset: pagina * limite })
  const detalhe = usePedido(selecionado)

  if (lista.isError) return <ErroBox erro={lista.error} />

  const total = lista.data?.total ?? 0
  const ultimaPagina = Math.max(0, Math.ceil(total / limite) - 1)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <FiltroGlobal />
        <input
          type="search"
          placeholder="Buscar por nº do pedido, SKU ou título…"
          value={busca}
          onChange={(e) => {
            setBusca(e.target.value)
            setPagina(0)
          }}
          className="input min-w-[260px] flex-1"
        />
      </div>

      <Secao
        titulo="Pedidos"
        descricao={`${inteiro(total)} pedidos no período e filtros selecionados.`}
        acao={
          <div className="flex items-center gap-2 text-xs">
            <button
              type="button"
              className="btn px-2 py-1"
              disabled={pagina === 0}
              onClick={() => setPagina((p) => Math.max(0, p - 1))}
            >
              ←
            </button>
            <span className="text-ink-muted">
              {pagina + 1} / {ultimaPagina + 1}
            </span>
            <button
              type="button"
              className="btn px-2 py-1"
              disabled={pagina >= ultimaPagina}
              onClick={() => setPagina((p) => p + 1)}
            >
              →
            </button>
          </div>
        }
      >
        {lista.isLoading ? (
          <Carregando altura="h-64" />
        ) : (
          <Tabela
            colunas={['Pedido', 'Canal', 'Data', 'Produto', 'Status', 'Logística', 'Bruto', 'Líquido', '']}
            vazio={(lista.data?.itens ?? []).length === 0}
          >
            {(lista.data?.itens ?? []).map((p) => (
              <tr
                key={p.id}
                className="cursor-pointer hover:bg-surface-raised"
                onClick={() => setSelecionado(p.id)}
              >
                <td className="td num font-medium">#{p.external_id}</td>
                <td className="td">
                  <SeloCanal canal={p.channel} />
                </td>
                <td className="td text-xs">{dataHora(p.date_created)}</td>
                <td className="td max-w-[260px] truncate" title={p.titulo}>
                  {p.titulo || '—'}
                  {p.itens_count > 1 && (
                    <span className="ml-1 text-xs text-ink-muted">+{p.itens_count - 1}</span>
                  )}
                </td>
                <td className="td">
                  <SeloStatus status={p.status} />
                </td>
                <td className="td text-xs">{ROTULO_LOGISTICA[p.logistic_type] ?? (p.logistic_type || '—')}</td>
                <td className="td num">{brl(p.gross_amount)}</td>
                <td className="td num">
                  <span className="mr-1.5">{brl(p.net_amount)}</span>
                  <SeloProcedencia fonte={p.net_source} />
                </td>
                <td className="td text-ink-muted">›</td>
              </tr>
            ))}
          </Tabela>
        )}
      </Secao>

      {selecionado !== null && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-black/40"
          onClick={() => setSelecionado(null)}
        >
          <aside
            className="h-full w-full max-w-xl overflow-y-auto bg-surface p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="mb-4 flex items-start justify-between">
              <div>
                <h2 className="text-base font-semibold text-ink">Pedido #{String(detalhe.data?.external_id ?? '')}</h2>
                <p className="card-sub">{dataHora(String(detalhe.data?.date_created ?? ''))}</p>
              </div>
              <button type="button" className="btn px-2 py-1" onClick={() => setSelecionado(null)}>
                ✕
              </button>
            </header>

            {detalhe.isLoading ? (
              <Carregando altura="h-64" />
            ) : (
              <DetalhePedido dados={detalhe.data ?? {}} />
            )}
          </aside>
        </div>
      )}
    </div>
  )
}

function DetalhePedido({ dados }: { dados: Record<string, any> }) {
  const fin = dados.financeiro ?? {}
  const conc = dados.conciliacao

  return (
    <div className="space-y-5">
      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">Itens</h3>
        <Tabela colunas={['SKU', 'Produto', 'Qtd.', 'Unit.', 'Total']} vazio={(dados.itens ?? []).length === 0}>
          {(dados.itens ?? []).map((i: Record<string, any>) => (
            <tr key={i.id}>
              <td className="td num text-xs">
                {i.sku_base ?? i.sku_channel}
                {i.sem_custo && (
                  <span className="ml-1 text-warn" title="Produto sem custo cadastrado: margem indisponível.">
                    ⚠
                  </span>
                )}
              </td>
              <td className="td max-w-[200px] truncate text-xs">{i.title}</td>
              <td className="td num text-xs">{inteiro(i.quantity)}</td>
              <td className="td num text-xs">{brl(i.unit_price)}</td>
              <td className="td num text-xs">{brl(i.gross_amount)}</td>
            </tr>
          ))}
        </Tabela>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">Financeiro</h3>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
          {[
            ['Receita bruta', fin.gross_amount],
            ['Frete cobrado', fin.shipping_revenue],
            ['Comissão', fin.platform_fee],
            ['Taxa de pagamento', fin.payment_fee],
            ['Custo de frete', fin.shipping_cost],
            ['Reembolsos', fin.refund_amount],
            ['CMV', fin.cogs],
            ['Margem', fin.margem],
          ].map(([rotulo, valor]) => (
            <div key={String(rotulo)} className="flex justify-between border-b border-line py-1">
              <dt className="text-ink-muted">{rotulo}</dt>
              <dd className="num text-ink">{brl(String(valor ?? 0))}</dd>
            </div>
          ))}
          <div className="col-span-2 flex items-center justify-between pt-2">
            <dt className="font-medium text-ink">Receita líquida</dt>
            <dd className="flex items-center gap-2">
              <span className="num text-base font-semibold text-ink">{brl(fin.net_amount)}</span>
              <SeloProcedencia fonte={String(fin.net_source ?? '')} />
            </dd>
          </div>
        </dl>
      </section>

      {conc && (
        <section className="rounded-lg border border-line p-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Conciliação</h3>
          <p className="text-sm text-ink">
            {conc.status} — divergência {brl(conc.divergence)}
          </p>
          {conc.notes && <p className="mt-1 text-xs text-ink-soft">{conc.notes}</p>}
        </section>
      )}

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Linha do tempo
        </h3>
        <ol className="space-y-2 border-l border-line pl-4">
          {(dados.timeline ?? []).map((e: Record<string, any>, i: number) => (
            <li key={i} className="relative text-xs">
              <span
                aria-hidden
                className="absolute -left-[21px] top-1 h-2 w-2 rounded-full"
                style={{ background: 'var(--series-1)' }}
              />
              <p className="text-ink">{e.evento}</p>
              {e.descricao && <p className="text-ink-soft">{e.descricao}</p>}
              <p className="text-ink-muted">{dataHora(e.ocorrido_em)}</p>
            </li>
          ))}
          {(dados.timeline ?? []).length === 0 && (
            <li className="text-xs text-ink-muted">Sem eventos registrados.</li>
          )}
        </ol>
      </section>
    </div>
  )
}
